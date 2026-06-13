"""
Rulerything 4.0 — 影子模式 + 双写校验引擎

Phase 1 (shadow):    不采集信号，仅对比排序差异
Phase 2 (dual_write): 采集信号 + 对比 + 持久化学习
"""

import logging
from typing import Dict, List, Optional


class ShadowEngine:
    """
    影子引擎 — 静默运行 4.0 排序，与 3.0 对比，记录差异。
    """

    def __init__(self, value_engine, storage):
        self.engine = value_engine
        self.storage = storage
        self.comparison_log: List[Dict] = []

    def compare_and_log(
        self,
        query: str,
        v3_results: List[str],      # 3.0 排序的 rule_id
        v4_results: List[str],      # 4.0 排序的 rule_id
        profile_name: str,
    ) -> Dict:
        """对比两个排序结果，记录差异。返回差异摘要。"""
        diff = {
            "query": query[:100],
            "profile": profile_name,
            "v3_top5": v3_results[:5],
            "v4_top5": v4_results[:5],
            "order_changed": v3_results[:5] != v4_results[:5],
        }

        if diff["order_changed"]:
            logging.info(f"[Shadow] 排序差异: query='{query[:50]}', "
                        f"v3_top3={v3_results[:3]}, v4_top3={v4_results[:3]}")

        self.comparison_log.append(diff)
        if len(self.comparison_log) > 1000:
            self.comparison_log = self.comparison_log[-500:]

        if self.storage:
            try:
                self.storage.log_shadow_comparison(diff)
            except Exception:
                pass

        return diff

    def get_stats(self) -> Dict:
        """返回 shadow 运行期间的统计摘要。"""
        if not self.comparison_log:
            return {"total": 0}
        changed = sum(1 for d in self.comparison_log if d["order_changed"])
        return {
            "total_comparisons": len(self.comparison_log),
            "order_changed": changed,
            "change_rate": round(changed / len(self.comparison_log), 4),
        }
