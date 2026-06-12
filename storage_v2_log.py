"""
Rulerything — 日志与配置存储层（storage_v2 日志功能拆分）

包括：查询日志、指标记录、运行时配置
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional

QUERY_LOG_RETENTION_DAYS = 56  # 8 周轮换


def _iso_now() -> str:
    return datetime.now().isoformat()


class LogMixin:
    """storage_v2 日志与配置混入类。"""

    # ── 查询日志 ────────────────────────────────────────

    def log_query(self, query_text: str, latency_ms: float,
                  result_count: int, cache_hit: bool,
                  result_ids: Optional[List[str]] = None):
        """记录一条查询日志。"""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO query_log (timestamp, query_text, latency_ms, result_count, cache_hit, result_ids) VALUES (?, ?, ?, ?, ?, ?)",
                        (_iso_now(), query_text, latency_ms, result_count, int(cache_hit),
                         ",".join(result_ids) if result_ids else ""),
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

    def get_recent_query_results(self, days: int = 7, limit: int = 1000) -> List[List[str]]:
        """获取近期查询的返回规则 ID 列表（用于 dep_miner 共现分析）。"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT result_ids FROM query_log WHERE timestamp > ? AND result_ids != '' ORDER BY timestamp DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
        results = []
        for r in rows:
            ids_str = r["result_ids"]
            if ids_str:
                ids = [x for x in ids_str.split(",") if x]
                if ids:
                    results.append(ids)
        return results

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
