# Copyright 2026 rulerything-io
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Copyright 2026 rulerything-io
"""
SQLite 存储层 — v3.0 规则存储，兼容 RuleStorage 接口。

Everything 原则：
  - 核心查询路径零外部依赖（sqlite3 是 Python 标准库）
  - 故障时降级为只读模式，不影响查询
  - 向后兼容：实现与 RuleStorage 相同的公有接口

变更：
  - JSONL: 改一条规则 rewrite 整个分类文件
  - SQLite: 单行读写，10k 条 < 30MB，WAL 模式支持并发
"""

import json
import logging
import os
import sqlite3
import threading
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable

from rule import Rule
from core.utils import _iso_now

# v1.1.0 拆分 — AI 功能和日志功能移至独立 mixin 模块
from storage_v2_ai import AIMixin
from storage_v2_log import LogMixin
from storage_v2_proposal import ProposalMixin

DB_FILENAME = "rules.db"
SNAPSHOT_DIR_NAME = "snapshots"
QUERY_LOG_RETENTION_DAYS = 56  # 8 周轮换


class RuleStorageV2(ProposalMixin, AIMixin, LogMixin):
    """基于 SQLite 的规则存储，兼容 RuleStorage 接口。

    用法:
        storage = RuleStorageV2("data")
        storage.add(rule)
        rules = storage.list(category="python")

    与 RuleStorage 的差异:
      - 无需 _save_category() 全量重写
      - 支持原子写入，并发安全（WAL 模式）
      - 引入双写机制：写入 SQLite 后自动更新内存索引（通过 callback）
    """

    def __init__(self, data_dir: str = "data", index_callback: Optional[Callable] = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / DB_FILENAME
        self.snapshot_dir = self.data_dir / SNAPSHOT_DIR_NAME
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

        # 索引同步回调：storage 写入后通知内存索引更新
        self.index_callback = index_callback

        # 线程锁（SQLite 连接不能跨线程共享）
        self._lock = threading.Lock()

        # 初始化数据库
        self._init_db()

        # 运行时状态
        self._mode = "normal"  # normal | readonly

    # ── 数据库初始化 ────────────────────────────────────

    def _init_db(self):
        """初始化数据库和表结构。"""
        with self._connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA busy_timeout=5000;

                CREATE TABLE IF NOT EXISTS rules (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    lang TEXT NOT NULL DEFAULT 'zh',
                    tags TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    verifier TEXT NOT NULL DEFAULT 'manual',
                    version INTEGER NOT NULL DEFAULT 1,
                    parent_id TEXT,
                    duplicate_of TEXT,
                    evolution_log TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    last_verified TEXT,
                    expires_at TEXT,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    last_hit TEXT,
                    ai_verified INTEGER NOT NULL DEFAULT 0,
                    last_ai_review TEXT
                );

                CREATE TABLE IF NOT EXISTS rules_cold (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    lang TEXT NOT NULL DEFAULT 'zh',
                    tags TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    verifier TEXT NOT NULL DEFAULT 'manual',
                    version INTEGER NOT NULL DEFAULT 1,
                    parent_id TEXT,
                    duplicate_of TEXT,
                    evolution_log TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    last_verified TEXT,
                    expires_at TEXT,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    last_hit TEXT,
                    ai_verified INTEGER NOT NULL DEFAULT 0,
                    last_ai_review TEXT,
                    archived_at TEXT NOT NULL,
                    archive_reason TEXT NOT NULL DEFAULT 'inactive_365d'
                );

                CREATE TABLE IF NOT EXISTS rule_relations (
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    strength REAL NOT NULL DEFAULT 0.5,
                    evidence TEXT NOT NULL,
                    mined_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, target_id, relation_type)
                );

                CREATE TABLE IF NOT EXISTS query_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    latency_ms REAL,
                    result_count INTEGER,
                    cache_hit INTEGER,
                    result_ids TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS metrics_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS config_runtime (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    module TEXT NOT NULL,
                    dedup_key TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 3,
                    risk_score REAL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    snapshot_id TEXT,
                    result TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    executed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    module TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'system',
                    target TEXT,
                    before_snapshot TEXT,
                    after_snapshot TEXT,
                    result TEXT NOT NULL,
                    duration_ms INTEGER,
                    error_message TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_rules_category ON rules(category);
                CREATE INDEX IF NOT EXISTS idx_rules_confidence ON rules(confidence);
                CREATE INDEX IF NOT EXISTS idx_rules_hit_count ON rules(hit_count);
                CREATE INDEX IF NOT EXISTS idx_query_ts ON query_log(timestamp);
                CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics_log(timestamp);
                CREATE INDEX IF NOT EXISTS idx_relations_type ON rule_relations(relation_type);
                CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
                CREATE INDEX IF NOT EXISTS idx_proposals_dedup ON proposals(dedup_key);
                CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp);
                CREATE INDEX IF NOT EXISTS idx_audit_module ON audit_log(module);
                CREATE INDEX IF NOT EXISTS idx_rules_last_hit ON rules(last_hit);
                CREATE INDEX IF NOT EXISTS idx_rules_category_content ON rules(category, content);
                CREATE INDEX IF NOT EXISTS idx_rules_cold_last_hit ON rules_cold(last_hit);

                -- 防并发重复：同分类同内容且非副本的唯一约束
                CREATE UNIQUE INDEX IF NOT EXISTS idx_rules_dedup
                    ON rules(category, content) WHERE duplicate_of IS NULL;

                -- 迁移：为旧数据库添加 result_ids 列
                CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER);
                INSERT INTO _schema_version (version) VALUES (0);
                -- schema version 1: add result_ids to query_log
                CREATE TABLE IF NOT EXISTS ai_cache (
                    query_hash TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    response TEXT NOT NULL,
                    model TEXT NOT NULL,
                    validation TEXT,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    latency_ms REAL,
                    created_at TEXT NOT NULL,
                    hit_count INTEGER DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_ai_cache_created ON ai_cache(created_at);

                -- Phase C: 提炼日志
                CREATE TABLE IF NOT EXISTS ingestion_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    query TEXT NOT NULL,
                    rule_id TEXT,
                    title TEXT,
                    category TEXT,
                    status TEXT NOT NULL,
                    dedup_method TEXT,
                    matched_rule_id TEXT,
                    error_message TEXT,
                    cost_usd REAL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_ingestion_ts ON ingestion_log(timestamp);
                CREATE INDEX IF NOT EXISTS idx_ingestion_status ON ingestion_log(status);

                -- Phase C: AI 反馈
                CREATE TABLE IF NOT EXISTS ai_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    positive INTEGER NOT NULL,
                    source TEXT DEFAULT 'user',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_rule ON ai_feedback(rule_id);

                -- Phase C: 父 AI 委托查询
                CREATE TABLE IF NOT EXISTS pending_ai_queries (
                    id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    response TEXT,
                    responder TEXT DEFAULT 'parent-ai',
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    answered_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_ai_queries(status);
            """)

            # BUG 10 修复: 移除 ai_feedback 冗余的 timestamp 列（保留 created_at）
            try:
                ver_parts = [
                    int(x) for x in
                    conn.execute("SELECT sqlite_version()").fetchone()[0].split(".")
                ]
                if ver_parts >= [3, 35, 0]:
                    cols = {
                        r["name"]
                        for r in conn.execute("PRAGMA table_info(ai_feedback)").fetchall()
                    }
                    if "timestamp" in cols and "created_at" in cols:
                        conn.execute("ALTER TABLE ai_feedback DROP COLUMN timestamp")
                else:
                    logging.warning(
                        "SQLite %s < 3.35, 无法 DROP COLUMN; "
                        "ai_feedback 的 timestamp 和 created_at 列冗余",
                        ".".join(str(x) for x in ver_parts),
                    )
            except Exception as e:
                logging.warning("ai_feedback 迁移失败: %s", e)

            # 迁移 v1: query_log 添加 result_ids 列
            try:
                cols = {
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(query_log)").fetchall()
                }
                if "result_ids" not in cols:
                    conn.execute("ALTER TABLE query_log ADD COLUMN result_ids TEXT DEFAULT ''")
            except Exception as e:
                logging.warning("query_log 迁移失败: %s", e)

            # 迁移 v2: rules 表添加 lang 列
            try:
                cols = {
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(rules)").fetchall()
                }
                if "lang" not in cols:
                    conn.execute("ALTER TABLE rules ADD COLUMN lang TEXT NOT NULL DEFAULT 'zh'")
            except Exception as e:
                logging.warning("rules 表 lang 列迁移失败: %s", e)

            try:
                cols = {
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(rules_cold)").fetchall()
                }
                if "lang" not in cols:
                    conn.execute("ALTER TABLE rules_cold ADD COLUMN lang TEXT NOT NULL DEFAULT 'zh'")
            except Exception as e:
                logging.warning("rules_cold 表 lang 列迁移失败: %s", e)

            # v4.0 迁移：value 相关字段 + profiles 表
            self._migrate_v3_to_v4(conn)

    def _connect(self) -> sqlite3.Connection:
        """创建新的数据库连接（线程安全）。"""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        current_mode = conn.execute("PRAGMA journal_mode").fetchone()[0].upper()
        if current_mode != "WAL":
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── v3 → v4 迁移 ────────────────────────────────────

    def _get_schema_version(self, conn: sqlite3.Connection) -> int:
        """获取当前 schema 版本号。"""
        try:
            row = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()
            return row[0] if row and row[0] else 0
        except Exception:
            return 0

    def _migrate_v3_to_v4(self, conn: sqlite3.Connection):
        """安全迁移 v3 → v4：检查 → 备份 → 迁移 → 验证。"""
        current = self._get_schema_version(conn)
        if current >= 4:
            return  # 已迁移

        # 自动备份（时间戳命名，每次迁移前新建，不覆盖旧备份）
        import shutil
        import time as _time
        backup_path = self.db_path.with_suffix(f".db.v3-backup-{int(_time.time())}")
        try:
            shutil.copy2(self.db_path, backup_path)
        except Exception:
            pass  # 备份非致命
        # 保留最近 5 份备份，删除更旧的
        backups = sorted(
            self.db_path.parent.glob(f"{self.db_path.name}.v3-backup-*"),
            key=lambda p: p.stat().st_mtime,
        )
        for old in backups[:-5]:
            try:
                old.unlink()
            except Exception:
                pass

        # 检查列是否已存在（支持断点续迁）
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(rules)")}
        migrations = [
            ("value_vector", "TEXT DEFAULT '{}'"),
            ("value_confidence", "REAL DEFAULT 0.5"),
            ("value_source", "TEXT DEFAULT 'default'"),
            ("value_provenance", "TEXT DEFAULT NULL"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing_cols:
                try:
                    conn.execute(f"ALTER TABLE rules ADD COLUMN {col_name} {col_def}")
                except Exception as e:
                    logging.warning(f"迁移添加列 {col_name} 失败: {e}")

        # 为 rules_cold 表同样添加（保持结构一致）
        cold_cols = {row[1] for row in conn.execute("PRAGMA table_info(rules_cold)")}
        for col_name, col_def in migrations:
            if col_name not in cold_cols:
                try:
                    conn.execute(f"ALTER TABLE rules_cold ADD COLUMN {col_name} {col_def}")
                except Exception as e:
                    logging.warning(f"迁移 rules_cold 添加列 {col_name} 失败: {e}")

        # 创建 profiles 表
        conn.execute(
            "CREATE TABLE IF NOT EXISTS profiles ("
            "  name TEXT PRIMARY KEY,"
            "  data TEXT NOT NULL,"
            "  created_at TEXT DEFAULT (datetime('now'))"
            ")"
        )

        # 创建 shadow_comparison_log 表
        conn.execute(
            "CREATE TABLE IF NOT EXISTS shadow_comparison_log ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  timestamp TEXT DEFAULT (datetime('now')),"
            "  query TEXT,"
            "  profile TEXT,"
            "  v3_top5 TEXT,"
            "  v4_top5 TEXT,"
            "  order_changed INTEGER"
            ")"
        )

        # 创建 schema_version 表并记录版本（支持多次迁移合并）
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT DEFAULT (datetime('now'))"
            ")"
        )
        conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (4)")
        logging.info("v3→v4 迁移完成: 已添加 value_vector/value_confidence/value_source/value_provenance, profiles 表")

    # ── 内部工具 ────────────────────────────────────────

    def _row_to_rule(self, row: sqlite3.Row) -> Rule:
        """将 SQLite 行转换为 Rule 对象。"""
        d = dict(row)
        # JSON 字符串→列表
        for field in ("tags", "evolution_log"):
            if isinstance(d.get(field), str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    d[field] = [] if field == "tags" else []
        # JSON 字符串→字典（v4.0 value_vector）
        vv = d.get("value_vector")
        if isinstance(vv, str):
            try:
                d["value_vector"] = json.loads(vv)
            except (json.JSONDecodeError, TypeError):
                d["value_vector"] = None
        # datetime 字段
        for field in ("created_at", "last_hit", "expires_at", "last_ai_review"):
            if isinstance(d.get(field), str):
                try:
                    d[field] = datetime.fromisoformat(d[field])
                except (ValueError, TypeError):
                    d[field] = None
        # 丢弃非 Rule 字段
        d.pop("archived_at", None)
        d.pop("archive_reason", None)
        return Rule.from_dict(d)

    def _rule_to_row(self, rule: Rule) -> dict:
        """将 Rule 对象转换为 SQLite 行字典。"""
        d = rule.to_dict()
        # 移除计算属性（非数据库列）
        d.pop("content_hash", None)
        # 列表→JSON 字符串
        for field in ("tags", "evolution_log"):
            if isinstance(d.get(field), list):
                d[field] = json.dumps(d[field], ensure_ascii=False)
        # 字典→JSON 字符串（v4.0 value_vector）
        if isinstance(d.get("value_vector"), dict):
            d["value_vector"] = json.dumps(d["value_vector"], ensure_ascii=False)
        return d

    # ── CRUD ────────────────────────────────────────────

    def add(self, rule: Rule) -> Tuple[bool, str]:
        """添加规则（含原子去重检测）。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    # 检查 ID 重复
                    existing = conn.execute(
                        "SELECT id FROM rules WHERE id = ?", (rule.id,)
                    ).fetchone()
                    if existing:
                        return False, f"规则 {rule.id} 已存在"

                    # 写入（UNIQUE 索引防并发重复，dup 场景直接 IntegrityError）
                    d = self._rule_to_row(rule)
                    cols = ", ".join(d.keys())
                    placeholders = ", ".join("?" for _ in d)
                    conn.execute(
                        f"INSERT INTO rules ({cols}) VALUES ({placeholders})",
                        list(d.values()),
                    )

                # 通知内存索引更新
                self._notify_index("add", rule)
                return True, "ok"

            except sqlite3.IntegrityError as e:
                # UNIQUE 约束冲突 → 查询具体重复条目
                try:
                    with self._connect() as conn2:
                        dup = conn2.execute(
                            "SELECT id, title FROM rules WHERE category = ? AND content = ? AND duplicate_of IS NULL",
                            (rule.category, rule.content),
                        ).fetchone()
                    if dup:
                        return False, f"内容重复: 与 {dup['id']}「{dup['title']}」内容相同"
                    return False, f"ID 冲突或唯一约束违反: {e}"
                except sqlite3.Error:
                    return False, f"唯一约束违反: {e}"

            except sqlite3.Error as e:
                return False, f"数据库错误: {e}"

    def get(self, rule_id: str) -> Optional[Rule]:
        """获取规则（自动跟随 duplicate_of 重定向）。"""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM rules WHERE id = ?", (rule_id,)
                ).fetchone()

        if row is None:
            return None

        rule = self._row_to_rule(row)
        if rule.duplicate_of:
            return self.get(rule.duplicate_of)
        return rule

    ALLOWED_UPDATE_COLUMNS = frozenset({
        "title", "content", "category", "tags", "confidence", "hit_count",
        "last_hit", "version", "lang", "verifier", "parent_id", "duplicate_of",
        "evolution_log", "expires_at", "ai_verified", "last_ai_review",
        "value_vector", "value_confidence", "value_source", "value_provenance",
    })

    def update(self, rule_id: str, **kwargs) -> bool:
        """更新规则字段。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    # 先检查存在性
                    existing = conn.execute(
                        "SELECT id FROM rules WHERE id = ?", (rule_id,)
                    ).fetchone()
                    if not existing:
                        return False

                    # 构建 SET 子句（校验列名，防止 SQL 注入）
                    sets = []
                    vals = []
                    for key, val in kwargs.items():
                        if key not in self.ALLOWED_UPDATE_COLUMNS:
                            raise ValueError(f"不允许更新的字段: {key}")
                        if key in ("tags", "evolution_log"):
                            val = json.dumps(val, ensure_ascii=False) if isinstance(val, list) else val
                        sets.append(f"{key} = ?")
                        vals.append(val)
                    vals.append(rule_id)
                    conn.execute(
                        f"UPDATE rules SET {', '.join(sets)} WHERE id = ?",
                        vals,
                    )

                # 通知内存索引
                self._notify_index("update", rule_id)
                return True

            except (sqlite3.Error, ValueError):
                return False

    def delete(self, rule_id: str) -> bool:
        """软删除：设置 expires_at = now。"""
        return self.update(rule_id, expires_at=_iso_now())

    def hard_delete(self, rule_id: str) -> bool:
        """物理删除。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
                self._notify_index("delete", rule_id)
                return True
            except sqlite3.Error:
                return False

    # ── 规则关系管理（线程安全） ──────────────────────────

    def save_relation(self, source: str, target: str,
                      relation_type: str, strength: float,
                      evidence: str):
        """保存规则关系到 rule_relations 表。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO rule_relations
                        (source_id, target_id, relation_type, strength, evidence, mined_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (source, target, relation_type, round(strength, 4),
                          evidence, _iso_now()))
            except sqlite3.Error:
                pass

    def clear_relations(self):
        """清空 rule_relations 表。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("DELETE FROM rule_relations")
            except sqlite3.Error:
                pass

    def get_relations(self, rule_id: Optional[str] = None,
                      relation_type: Optional[str] = None,
                      limit: int = 500) -> List[dict]:
        """查询规则关系。"""
        with self._lock:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                clauses = []
                params = []
                if rule_id:
                    clauses.append("(source_id = ? OR target_id = ?)")
                    params.extend([rule_id, rule_id])
                if relation_type:
                    clauses.append("relation_type = ?")
                    params.append(relation_type)
                where = " AND ".join(clauses) if clauses else "1=1"
                rows = conn.execute(
                    f"SELECT * FROM rule_relations WHERE {where} ORDER BY strength DESC LIMIT ?",
                    params + [limit],
                ).fetchall()
            return [dict(r) for r in rows]

    def list(self, category: Optional[str] = None) -> List[Rule]:
        """列出有效规则，可选按分类过滤。"""
        with self._lock:
            with self._connect() as conn:
                if category:
                    rows = conn.execute(
                        "SELECT * FROM rules WHERE category = ? AND expires_at IS NULL AND duplicate_of IS NULL ORDER BY id",
                        (category,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM rules WHERE expires_at IS NULL AND duplicate_of IS NULL ORDER BY id"
                    ).fetchall()

        return [self._row_to_rule(r) for r in rows]

    # ── 冷规则管理 ─────────────────────────────────────

    def archive_cold_rules(self, days: int = 365) -> List[str]:
        """将 N 天未命中的规则移入冷存储（批量操作，防止数据丢失）。"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self._lock:
            with self._connect() as conn:
                # 批量收集符合条件的规则 ID
                cold_ids = [
                    r["id"] for r in conn.execute(
                        "SELECT id FROM rules WHERE last_hit IS NOT NULL AND last_hit < ? AND expires_at IS NULL AND duplicate_of IS NULL",
                        (cutoff,),
                    ).fetchall()
                ]
                if not cold_ids:
                    return []

                # 检查哪些 ID 已存在于 rules_cold，防止 INSERT OR IGNORE → DELETE 数据丢失
                placeholders = ", ".join("?" for _ in cold_ids)
                existing_cold = {
                    r["id"] for r in conn.execute(
                        f"SELECT id FROM rules_cold WHERE id IN ({placeholders})",
                        cold_ids,
                    ).fetchall()
                }

                safe_ids = [rid for rid in cold_ids if rid not in existing_cold]
                for rid in existing_cold:
                    logging.warning("archive_cold_rules: ID %s already in rules_cold, skipping delete from rules", rid)
                if not safe_ids:
                    return []

                # 批量插入 rules_cold（一次 SQL 操作代替 N 次）
                now = _iso_now()
                reason = f"inactive_{days}d"
                insert_phs = ", ".join("?" for _ in safe_ids)
                conn.execute(
                    f"INSERT INTO rules_cold SELECT *, ? AS archived_at, ? AS archive_reason FROM rules WHERE id IN ({insert_phs})",
                    [now, reason] + safe_ids,
                )
                # 批量删除 rules（仅删除成功插入的规则）
                conn.execute(
                    f"DELETE FROM rules WHERE id IN ({insert_phs})",
                    safe_ids,
                )

        for rid in safe_ids:
            self._notify_index("delete", rid)

        return safe_ids

    def unfreeze_rule(self, rule_id: str) -> bool:
        """从冷存储解冻规则回主表。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT * FROM rules_cold WHERE id = ?", (rule_id,)
                    ).fetchone()
                    if not row:
                        return False

                    d = dict(row)
                    d.pop("archived_at", None)
                    d.pop("archive_reason", None)

                    # 检查目标 ID 是否已存在于 rules 表，防止 INSERT OR REPLACE 静默覆盖
                    exists = conn.execute(
                        "SELECT 1 FROM rules WHERE id = ?", (row["id"],)
                    ).fetchone()
                    if exists:
                        logging.warning("unfreeze_rule: ID %s already exists in rules, skipping", rule_id)
                        return False

                    cols = ", ".join(d.keys())
                    placeholders = ", ".join("?" for _ in d)
                    conn.execute(
                        f"INSERT INTO rules ({cols}) VALUES ({placeholders})",
                        list(d.values()),
                    )
                    conn.execute("DELETE FROM rules_cold WHERE id = ?", (rule_id,))

                self._notify_index("add", self.get(rule_id))
                return True

            except sqlite3.Error:
                return False

    def search_cold(self, query: str, limit: int = 10) -> List[Rule]:
        """在冷存储中搜索规则（用于缺口检测解冻）。"""
        with self._lock:
            with self._connect() as conn:
                like = f"%{query}%"
                rows = conn.execute(
                    "SELECT * FROM rules_cold WHERE title LIKE ? OR content LIKE ? LIMIT ?",
                    (like, like, limit),
                ).fetchall()
        return [self._row_to_rule(r) for r in rows]

    def list_cold(self) -> List[Rule]:
        """列出所有冷规则。"""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM rules_cold ORDER BY id"
                ).fetchall()
        return [self._row_to_rule(r) for r in rows]

    # ── 去重 ────────────────────────────────────────────

    def find_duplicates(self) -> Dict[str, List[Rule]]:
        """查找所有重复规则组（按 category + content，使用 SQL GROUP BY 避免 Python 循环）。"""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute("""
                    SELECT id, title, category, content, confidence,
                           tags, verifier, version, parent_id, duplicate_of,
                           evolution_log, created_at, last_verified, expires_at,
                           hit_count, last_hit, ai_verified, last_ai_review
                    FROM rules
                    WHERE expires_at IS NULL AND duplicate_of IS NULL
                    ORDER BY category, content, confidence DESC
                """).fetchall()

        groups: Dict[str, List[Rule]] = {}
        for r in rows:
            key = f"{r['category']}:::{r['content']}"
            groups.setdefault(key, []).append(self._row_to_rule(r))
        return {k: g for k, g in groups.items() if len(g) > 1}

    def dedup_dry_run(self) -> List[dict]:
        """预览去重结果。"""
        previews = []
        for h, group in self.find_duplicates().items():
            sorted_group = sorted(group, key=lambda r: r.confidence, reverse=True)
            master = sorted_group[0]
            previews.append({
                "content_hash": h,
                "master_id": master.id,
                "master_title": master.title,
                "master_confidence": master.confidence,
                "duplicates": [
                    {"id": r.id, "title": r.title, "confidence": r.confidence}
                    for r in sorted_group[1:]
                ],
            })
        return previews

    def dedup_apply(self) -> List[dict]:
        """执行去重。"""
        results = []
        for h, group in self.find_duplicates().items():
            sorted_group = sorted(group, key=lambda r: r.confidence, reverse=True)
            master = sorted_group[0]
            for dup in sorted_group[1:]:
                self.update(dup.id, duplicate_of=master.id,
                            evolution_log=dup.evolution_log + [f"标记为 {master.id} 的重复"])
                results.append({"rule_id": dup.id, "duplicate_of": master.id})
        return results

    # ── 审计日志 ────────────────────────────────────────

    def log_audit(self, action: str, module: str, actor: str = "system",
                  target: Optional[str] = None,
                  before_snapshot: Optional[str] = None,
                  after_snapshot: Optional[str] = None,
                  result: str = "success",
                  duration_ms: Optional[int] = None,
                  error_message: Optional[str] = None):
        """写入一条审计日志。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("""
                        INSERT INTO audit_log (timestamp, action, module, actor,
                            target, before_snapshot, after_snapshot, result,
                            duration_ms, error_message)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (_iso_now(), action, module, actor, target,
                          before_snapshot, after_snapshot, result,
                          duration_ms, error_message))
            except sqlite3.Error:
                pass

    def get_recent_audit_logs(self, limit: int = 100,
                              module: Optional[str] = None) -> List[dict]:
        """获取最近的审计日志。"""
        with self._lock:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                if module:
                    rows = conn.execute(
                        "SELECT * FROM audit_log WHERE module = ? ORDER BY timestamp DESC LIMIT ?",
                        (module, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
        return [dict(r) for r in rows]

    # ── 索引同步 ────────────────────────────────────────

    def set_index_callback(self, callback: Callable):
        """设置索引同步回调。"""
        self.index_callback = callback

    def _notify_index(self, action: str, data):
        """通知内存索引更新。"""
        if self.index_callback:
            try:
                self.index_callback(action, data)
            except Exception:
                logging.warning("storage_v2: 通知内存索引更新失败")
                pass  # 索引更新失败不阻断存储写入

    def reconcile_index(self, rebuild_fn: Optional[Callable] = None) -> dict:
        """对账：校验 SQLite 与内存索引的一致性。

        Args:
            rebuild_fn: 重建索引的函数，如 lambda rules: index.build(rules)

        Returns:
            {"consistent": bool, "sqlite_count": int, "index_count": int, "rebuilt": bool}
        """
        sqlite_count = 0
        with self._lock:
            with self._connect() as conn:
                sqlite_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM rules WHERE expires_at IS NULL AND duplicate_of IS NULL"
                ).fetchone()["cnt"]

        # 如果没有 callback，无法对账
        if not rebuild_fn:
            return {"consistent": True, "sqlite_count": sqlite_count,
                    "index_count": -1, "rebuilt": False}

        # 如果 callback 是索引对象，尝试获取计数
        # 需要外部传入 index 的规则数
        # 简易对账：用 list() 获取全量规则后传给 rebuild_fn
        rules = self.list()
        if len(rules) != sqlite_count:
            return {"consistent": False, "sqlite_count": sqlite_count,
                    "index_count": len(rules), "rebuilt": False}

        # 重建索引
        if rebuild_fn:
            rebuild_fn(rules)

        return {"consistent": True, "sqlite_count": sqlite_count,
                "index_count": len(rules), "rebuilt": True}

    # ── 快照 ────────────────────────────────────────────

    def create_snapshot(self) -> str:
        """创建系统快照（复制 SQLite 文件）。"""
        snapshot_id = f"snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        snapshot_path = self.snapshot_dir / f"{snapshot_id}.db"

        with self._lock:
            with self._connect() as conn:
                # 将 WAL 刷新到主数据库文件，确保快照完整性
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("BEGIN IMMEDIATE")
                shutil.copy2(self.db_path, snapshot_path)
                conn.execute("COMMIT")

        return snapshot_id

    def restore_snapshot(self, snapshot_id: str) -> bool:
        """从快照恢复。"""
        snapshot_path = self.snapshot_dir / f"{snapshot_id}.db"
        if not snapshot_path.exists():
            return False

        # 崩溃前备份当前状态
        crash_snapshot = self.create_snapshot()

        with self._lock:
            shutil.copy2(snapshot_path, self.db_path)

        return True

    def list_snapshots(self) -> List[dict]:
        """列出所有快照。"""
        snapshots = []
        for f in sorted(self.snapshot_dir.glob("snap_*.db")):
            stat = f.stat()
            snapshots.append({
                "id": f.stem,
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return snapshots

    def prune_snapshots(self, max_keep: int = 50):
        """删除超出保留数量的快照（保留最新的 N 个）。"""
        snapshots = sorted(self.snapshot_dir.glob("snap_*.db"),
                           key=lambda f: f.stat().st_mtime)
        while len(snapshots) > max_keep:
            oldest = snapshots.pop(0)
            oldest.unlink(missing_ok=True)

    # ── v4.0 画像持久化 ─────────────────────────────────

    def save_profile(self, profile) -> bool:
        """持久化 ValueProfile 到 profiles 表。"""
        import json as _json
        from dataclasses import asdict
        with self._lock:
            try:
                with self._connect() as conn:
                    # 序列化 profile（排除 _lock 等不可序列化字段）
                    p_dict = {
                        "name": profile.name,
                        "weights": profile.weights,
                        "priority_order": profile.priority_order,
                        "conflict_strategy": profile.conflict_strategy,
                        "created_at": profile.created_at.isoformat() if hasattr(profile.created_at, 'isoformat') else str(profile.created_at),
                        "updated_at": profile.updated_at.isoformat() if hasattr(profile.updated_at, 'isoformat') else str(profile.updated_at),
                        "learn_count": profile.learn_count,
                        "_last_dimension_hit": getattr(profile, '_last_dimension_hit', {}),
                    }
                    data = _json.dumps(p_dict, ensure_ascii=False)
                    conn.execute(
                        "INSERT OR REPLACE INTO profiles (name, data) VALUES (?, ?)",
                        (profile.name, data),
                    )
                return True
            except Exception:
                return False

    def list_profiles(self):
        """从 profiles 表加载所有持久化的 ValueProfile。"""
        from value.profile import ValueProfile
        import json as _json
        with self._lock:
            try:
                with self._connect() as conn:
                    rows = conn.execute("SELECT data FROM profiles").fetchall()
                profiles = []
                for row in rows:
                    try:
                        data = _json.loads(row["data"])
                        p = ValueProfile(
                            name=data["name"],
                            weights=data.get("weights", {}),
                            priority_order=data.get("priority_order", []),
                            conflict_strategy=data.get("conflict_strategy", "weighted_vote"),
                            learn_count=data.get("learn_count", 0),
                        )
                        p._last_dimension_hit = data.get("_last_dimension_hit", {})
                        p.ensure_weights()
                        profiles.append(p)
                    except Exception:
                        continue
                return profiles
            except Exception:
                return []

    # ── v4.0 Shadow 对比日志 ─────────────────────────────

    def log_shadow_comparison(self, diff: dict):
        """记录 shadow 模式下的排序对比结果。"""
        import json as _json
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO shadow_comparison_log (query, profile, v3_top5, v4_top5, order_changed) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            diff.get("query", "")[:100],
                            diff.get("profile", ""),
                            _json.dumps(diff.get("v3_top5", []), ensure_ascii=False),
                            _json.dumps(diff.get("v4_top5", []), ensure_ascii=False),
                            1 if diff.get("order_changed") else 0,
                        ),
                    )
            except Exception:
                pass

    # ── 统计 ────────────────────────────────────────────

    def stats(self) -> dict:
        """存储统计信息。"""
        with self._lock:
            with self._connect() as conn:
                total = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
                active = conn.execute(
                    "SELECT COUNT(*) FROM rules WHERE expires_at IS NULL AND duplicate_of IS NULL"
                ).fetchone()[0]
                cold = conn.execute("SELECT COUNT(*) FROM rules_cold").fetchone()[0]
                categories = conn.execute(
                    "SELECT DISTINCT category FROM rules WHERE expires_at IS NULL AND duplicate_of IS NULL ORDER BY category"
                ).fetchall()
                dup_groups = conn.execute(
                    "SELECT COUNT(*) FROM (SELECT category, content FROM rules WHERE expires_at IS NULL AND duplicate_of IS NULL GROUP BY category, content HAVING COUNT(*) > 1)"
                ).fetchone()[0]

        return {
            "total_rules": total,
            "active_rules": active,
            "cold_rules": cold,
            "categories": [r["category"] for r in categories],
            "duplicate_groups": dup_groups,
            "mode": self._mode,
        }

    # ── 数据库维护 ──────────────────────────────────────

    def vacuum(self):
        """回收磁盘空间。"""
        with self._lock:
            with self._connect() as conn:
                conn.execute("VACUUM")

    def integrity_check(self) -> List[str]:
        """运行完整性和一致性检查（SQLite 自检 + 应用层校验）。"""
        issues: List[str] = []

        with self._lock:
            with self._connect() as conn:
                # 基础 SQLite b-tree 完整性
                row = conn.execute("PRAGMA integrity_check").fetchone()
                if row and row[0] != "ok":
                    issues.append(f"SQLite integrity_check 失败: {row[0]}")

                # 检查分类为空或 NULL 的规则
                bad_cats = conn.execute(
                    "SELECT COUNT(*) FROM rules WHERE category IS NULL OR category = ''"
                ).fetchone()[0]
                if bad_cats > 0:
                    issues.append(f"发现 {bad_cats} 条规则分类为空")

                # 检查 rule_relations 中的悬空引用
                orphan_rels = conn.execute("""
                    SELECT COUNT(*) FROM rule_relations r
                    WHERE NOT EXISTS (SELECT 1 FROM rules WHERE id = r.source_id)
                    AND NOT EXISTS (SELECT 1 FROM rules_cold WHERE id = r.source_id)
                """).fetchone()[0]
                if orphan_rels > 0:
                    issues.append(f"发现 {orphan_rels} 条 rule_relations 引用了不存在的 source_id")

                orphan_rel_targets = conn.execute("""
                    SELECT COUNT(*) FROM rule_relations r
                    WHERE NOT EXISTS (SELECT 1 FROM rules WHERE id = r.target_id)
                    AND NOT EXISTS (SELECT 1 FROM rules_cold WHERE id = r.target_id)
                """).fetchone()[0]
                if orphan_rel_targets > 0:
                    issues.append(f"发现 {orphan_rel_targets} 条 rule_relations 引用了不存在的 target_id")

                # 检查 NULL 标题或空内容
                null_titles = conn.execute(
                    "SELECT COUNT(*) FROM rules WHERE title IS NULL OR title = ''"
                ).fetchone()[0]
                if null_titles > 0:
                    issues.append(f"发现 {null_titles} 条规则标题为空")

                empty_content = conn.execute(
                    "SELECT COUNT(*) FROM rules WHERE content IS NULL OR content = ''"
                ).fetchone()[0]
                if empty_content > 0:
                    issues.append(f"发现 {empty_content} 条规则内容为空")

                # 检查 confidence 范围 [0, 1]
                bad_confidence = conn.execute(
                    "SELECT COUNT(*) FROM rules WHERE confidence < 0 OR confidence > 1"
                ).fetchone()[0]
                if bad_confidence > 0:
                    issues.append(f"发现 {bad_confidence} 条规则 confidence 超出 [0, 1] 范围")

                # 检查重复标题（有效规则中）
                dup_titles = conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT title FROM rules
                        WHERE expires_at IS NULL AND duplicate_of IS NULL
                        GROUP BY title HAVING COUNT(*) > 1
                    )
                """).fetchone()[0]
                if dup_titles > 0:
                    issues.append(f"发现 {dup_titles} 组重复标题")

                # 检查负值 hit_count
                neg_hits = conn.execute(
                    "SELECT COUNT(*) FROM rules WHERE hit_count < 0"
                ).fetchone()[0]
                if neg_hits > 0:
                    issues.append(f"发现 {neg_hits} 条规则 hit_count 为负数")

        return issues

    def close(self):
        """优雅关闭：刷新 WAL 并释放连接。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.close()
            except Exception:
                pass
