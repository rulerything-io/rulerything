# Copyright 2026 Rulerything Project Authors
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

# Copyright 2026 Rulerything Project Authors
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

DB_FILENAME = "rules.db"
SNAPSHOT_DIR_NAME = "snapshots"
QUERY_LOG_RETENTION_DAYS = 56  # 8 周轮换


def _iso_now() -> str:
    return datetime.now().isoformat()


class RuleStorageV2:
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
                    cache_hit INTEGER
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

                -- Phase C: AI 缓存
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

    def _connect(self) -> sqlite3.Connection:
        """创建新的数据库连接（线程安全）。"""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        current_mode = conn.execute("PRAGMA journal_mode").fetchone()[0].upper()
        if current_mode != "WAL":
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

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
        return d

    # ── CRUD ────────────────────────────────────────────

    def add(self, rule: Rule) -> Tuple[bool, str]:
        """添加规则（含去重检测）。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    # 检查 ID 重复
                    existing = conn.execute(
                        "SELECT id FROM rules WHERE id = ?", (rule.id,)
                    ).fetchone()
                    if existing:
                        return False, f"规则 {rule.id} 已存在"

                    # 检查内容重复（同分类）
                    dup = conn.execute(
                        "SELECT id, title FROM rules WHERE category = ? AND content = ? AND duplicate_of IS NULL",
                        (rule.category, rule.content),
                    ).fetchone()
                    if dup:
                        return False, f"内容重复: 与 {dup['id']}「{dup['title']}」内容相同"

                    # 写入
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
        "last_hit", "is_active", "metadata", "version", "description", "source",
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

    # ── 查询日志 ────────────────────────────────────────

    def log_query(self, query_text: str, latency_ms: float,
                  result_count: int, cache_hit: bool):
        """记录一条查询日志。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO query_log (timestamp, query_text, latency_ms, result_count, cache_hit) VALUES (?, ?, ?, ?, ?)",
                        (_iso_now(), query_text, latency_ms, result_count, int(cache_hit)),
                    )
            except sqlite3.Error:
                pass  # 日志写入失败不阻断查询

    def get_recent_queries(self, days: int = 7, min_freq: int = 1) -> List[dict]:
        """获取近期高频查询。"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT query_text, COUNT(*) as freq FROM query_log WHERE timestamp > ? GROUP BY query_text HAVING freq >= ? ORDER BY freq DESC LIMIT 200",
                    (cutoff, min_freq),
                ).fetchall()
        return [{"query": r["query_text"], "frequency": r["freq"]} for r in rows]

    def rotate_query_log(self):
        """清理超过保留期的查询日志。"""
        cutoff = (datetime.now() - timedelta(days=QUERY_LOG_RETENTION_DAYS)).isoformat()
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM query_log WHERE timestamp < ?", (cutoff,))

    # ── 指标记录 ────────────────────────────────────────

    def log_metric(self, name: str, value: float):
        """记录一个指标值。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO metrics_log (timestamp, metric_name, metric_value) VALUES (?, ?, ?)",
                        (_iso_now(), name, value),
                    )
            except sqlite3.Error:
                pass

    def get_metrics(self, name: str, hours: int = 24) -> List[dict]:
        """获取某个指标最近 N 小时的记录。"""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT timestamp, metric_value FROM metrics_log WHERE metric_name = ? AND timestamp > ? ORDER BY timestamp",
                    (name, cutoff),
                ).fetchall()
        return [{"timestamp": r["timestamp"], "value": r["metric_value"]} for r in rows]

    # ── 运行时配置 ──────────────────────────────────────

    def get_config(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """获取运行时配置。"""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value FROM config_runtime WHERE key = ?", (key,)
                ).fetchone()
        return row["value"] if row else default

    def set_config(self, key: str, value: str):
        """设置运行时配置。"""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO config_runtime (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, _iso_now()),
                )

    # ── 提案管理 ────────────────────────────────────────

    def create_proposal(self, title: str, description: str,
                        module: str, dedup_key: str,
                        priority: int = 3,
                        risk_score: float = 0.0) -> str:
        """创建新提案，返回 proposal_id。"""
        proposal_id = f"{module}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("""
                        INSERT INTO proposals (id, title, description, module, dedup_key,
                            priority, risk_score, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """, (proposal_id, title, description, module, dedup_key,
                          priority, risk_score, _iso_now()))
                return proposal_id
            except sqlite3.Error:
                return ""

    def get_proposal(self, proposal_id: str) -> Optional[dict]:
        """获取提案详情。"""
        with self._lock:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
                ).fetchone()
        return dict(row) if row else None

    def list_proposals(self, status: Optional[str] = None,
                       module: Optional[str] = None,
                       limit: int = 100) -> List[dict]:
        """列出提案，可按状态/模块过滤。"""
        with self._lock:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                clauses = []
                params = []
                if status:
                    clauses.append("status = ?")
                    params.append(status)
                if module:
                    clauses.append("module = ?")
                    params.append(module)
                where = " AND ".join(clauses) if clauses else "1=1"
                rows = conn.execute(
                    f"SELECT * FROM proposals WHERE {where} ORDER BY created_at DESC LIMIT ?",
                    params + [limit],
                ).fetchall()
        return [dict(r) for r in rows]

    def update_proposal_status(self, proposal_id: str, status: str,
                               **extra):
        """更新提案状态和额外字段。"""
        allowed_statuses = {"pending", "running", "success", "failed", "rolled_back", "cancelled"}
        if status not in allowed_statuses:
            return False
        with self._lock:
            try:
                with self._connect() as conn:
                    sets = ["status = ?"]
                    vals = [status]
                    if status in ("success", "failed", "rolled_back"):
                        sets.append("executed_at = ?")
                        vals.append(_iso_now())
                    for key in ("snapshot_id", "result", "error_message"):
                        if key in extra:
                            sets.append(f"{key} = ?")
                            vals.append(extra[key])
                    vals.append(proposal_id)
                    conn.execute(
                        f"UPDATE proposals SET {', '.join(sets)} WHERE id = ?",
                        vals,
                    )
                return True
            except sqlite3.Error:
                return False

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

    # ── AI 缓存 ──────────────────────────────────────────

    def ai_cache_get(self, query_hash: str, ttl_hours: int = 24) -> Optional[dict]:
        """获取 AI 缓存条目。"""
        with self._lock:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM ai_cache WHERE query_hash = ? AND created_at > datetime('now', ?)",
                    (query_hash, f'-{ttl_hours} hours'),
                ).fetchone()
        return dict(row) if row else None

    def ai_cache_set(self, query_hash: str, query: str, response: str,
                     model: str, cost_usd: float, latency_ms: float,
                     validation: Optional[str] = None):
        """写入 AI 缓存。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO ai_cache
                        (query_hash, query, response, model, validation, cost_usd, latency_ms, created_at, hit_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """, (query_hash, query, response, model, validation,
                          round(cost_usd, 6), round(latency_ms, 2), _iso_now()))
            except sqlite3.Error:
                pass

    def ai_cache_hit(self, query_hash: str):
        """更新缓存命中计数。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE ai_cache SET hit_count = hit_count + 1 WHERE query_hash = ?",
                        (query_hash,),
                    )
            except sqlite3.Error:
                pass

    def ai_cache_cleanup(self, max_entries: int = 5000):
        """LRU 淘汰：删除最旧的条目。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    count = conn.execute("SELECT COUNT(*) FROM ai_cache").fetchone()[0]
                    if count > max_entries:
                        conn.execute(
                            "DELETE FROM ai_cache WHERE query_hash IN ("
                            "SELECT query_hash FROM ai_cache ORDER BY created_at ASC LIMIT ?"
                            ")",
                            (count - max_entries,),
                        )
            except sqlite3.Error:
                pass

    def get_rule_version_hash(self) -> str:
        """获取规则版本哈希，用于缓存失效检测。"""
        import hashlib
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT GROUP_CONCAT(version ORDER BY id) FROM rules WHERE expires_at IS NULL AND duplicate_of IS NULL"
                ).fetchone()
        versions_str = row[0] if row and row[0] else "empty"
        return hashlib.sha256(versions_str.encode()).hexdigest()[:16]

    # ── AI 提炼日志 ──────────────────────────────────────

    def log_ingestion(self, query: str, status: str,
                      rule_id: Optional[str] = None,
                      title: Optional[str] = None,
                      category: Optional[str] = None,
                      dedup_method: Optional[str] = None,
                      matched_rule_id: Optional[str] = None,
                      error_message: Optional[str] = None,
                      cost_usd: float = 0.0):
        """记录一条规则提炼日志。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("""
                        INSERT INTO ingestion_log
                        (timestamp, query, rule_id, title, category, status,
                         dedup_method, matched_rule_id, error_message, cost_usd)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (_iso_now(), query, rule_id, title, category, status,
                          dedup_method, matched_rule_id, error_message, round(cost_usd, 6)))
            except sqlite3.Error:
                pass

    def get_ingestion_logs(self, limit: int = 100,
                           status: Optional[str] = None) -> List[dict]:
        """获取提炼日志。"""
        with self._lock:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                if status:
                    rows = conn.execute(
                        "SELECT * FROM ingestion_log WHERE status = ? ORDER BY timestamp DESC LIMIT ?",
                        (status, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM ingestion_log ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
        return [dict(r) for r in rows]

    # ── AI 反馈 ──────────────────────────────────────────

    def record_ai_feedback(self, rule_id: str, positive: bool, source: str = "user"):
        """记录一条 AI 规则反馈。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO ai_feedback (rule_id, positive, source, created_at) VALUES (?, ?, ?, ?)",
                        (rule_id, 1 if positive else 0, source, _iso_now()),
                    )
            except sqlite3.Error:
                pass

    def get_ai_feedback(self, rule_id: str) -> List[dict]:
        """获取某规则的所有反馈。"""
        with self._lock:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM ai_feedback WHERE rule_id = ? ORDER BY created_at DESC",
                    (rule_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_ai_feedback_stats(self, rule_id: str) -> dict:
        """获取规则反馈统计。"""
        with self._lock:
            with self._connect() as conn:
                total = conn.execute(
                    "SELECT COUNT(*) FROM ai_feedback WHERE rule_id = ?", (rule_id,)
                ).fetchone()[0]
                positive = conn.execute(
                    "SELECT COUNT(*) FROM ai_feedback WHERE rule_id = ? AND positive = 1", (rule_id,)
                ).fetchone()[0]
        return {
            "total": total,
            "positive": positive,
            "negative": total - positive,
            "ratio": round(positive / max(total, 1), 3),
        }

    # ── AI 统计 ──────────────────────────────────────────

    def get_ai_stats(self) -> dict:
        """获取 AI 模块汇总统计。"""
        with self._lock:
            with self._connect() as conn:
                cache_count = conn.execute("SELECT COUNT(*) FROM ai_cache").fetchone()[0]
                cache_hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) FROM ai_cache").fetchone()[0]
                today = _iso_now()[:10]
                daily_cost = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM ai_cache WHERE created_at LIKE ?",
                    (f"{today}%",),
                ).fetchone()[0]
                total_cost = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM ai_cache"
                ).fetchone()[0]
                ingest_ok = conn.execute(
                    "SELECT COUNT(*) FROM ingestion_log WHERE status = 'created'"
                ).fetchone()[0]
                ingest_dup = conn.execute(
                    "SELECT COUNT(*) FROM ingestion_log WHERE status = 'duplicate'"
                ).fetchone()[0]
        return {
            "cache_entries": cache_count,
            "cache_total_hits": cache_hits,
            "daily_ai_cost": round(daily_cost, 4),
            "total_ai_cost": round(total_cost, 4),
            "rules_ingested": ingest_ok,
            "rules_duplicate_skipped": ingest_dup,
        }

    # ── 父 AI 委托查询 ─────────────────────────────────

    def add_pending_query(self, query: str) -> str:
        """添加一条待处理的 AI 查询。
        Returns: query_id（可用于后续查询结果）
        """
        import uuid
        query_id = f"q_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO pending_ai_queries (id, query, status, created_at) VALUES (?, ?, 'pending', ?)",
                    (query_id, query, _iso_now()),
                )
        return query_id

    def get_pending_queries(self, status: Optional[str] = "pending", limit: int = 50) -> List[dict]:
        """获取待处理查询列表。status=None 返回所有状态。"""
        with self._lock:
            with self._connect() as conn:
                if status:
                    rows = conn.execute(
                        "SELECT * FROM pending_ai_queries WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                        (status, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM pending_ai_queries ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
        return [dict(r) for r in rows]

    def answer_pending_query(self, query_id: str, response: str,
                              responder: str = "parent-ai",
                              error_message: Optional[str] = None) -> bool:
        """回答一条待处理的 AI 查询。"""
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE pending_ai_queries SET status='answered', response=?, "
                    "responder=?, answered_at=?, error_message=? WHERE id=? AND status='pending'",
                    (response, responder, _iso_now(), error_message, query_id),
                )
                return cur.rowcount > 0

    def get_pending_query_count(self) -> dict:
        """获取各类状态计数。"""
        with self._lock:
            with self._connect() as conn:
                total = conn.execute("SELECT COUNT(*) FROM pending_ai_queries").fetchone()[0]
                pending = conn.execute(
                    "SELECT COUNT(*) FROM pending_ai_queries WHERE status='pending'"
                ).fetchone()[0]
                answered = conn.execute(
                    "SELECT COUNT(*) FROM pending_ai_queries WHERE status='answered'"
                ).fetchone()[0]
        return {"total": total, "pending": pending, "answered": answered}

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
