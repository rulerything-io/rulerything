"""
Rulerything — AI 存储层（storage_v2 AI 相关功能拆分）

包括：AI 缓存、AI 提炼日志、AI 反馈、AI 统计、父 AI 委托查询
"""

import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from core.utils import _iso_now


class AIMixin:
    """storage_v2 AI 功能混入类。"""

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
