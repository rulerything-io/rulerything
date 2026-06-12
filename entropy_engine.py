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
熵引擎 — 信息增益驱动的优化建议系统（Phase 1）

核心概念：
- 系统熵：衡量系统整体不确定性
- 信息增益：某个操作能减少的熵值
- 建议优先级：按信息增益/成本比排序

Design philosophy:
- 纯计算模块，零外部依赖
- 不改变任何现有数据结构
- 所有阈值通过 config 控制
"""

import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

import numpy as np


@dataclass
class OptimizationAction:
    """优化操作描述"""
    type: str
    target: str
    description: str
    estimated_cost: float = 0.5
    predicted_improvement: float = 0.1
    cooldown_hours: float = 24.0


class EntropyEngine:
    """
    熵引擎 — 信息增益驱动的优化建议系统。

    使用方式：
        engine = EntropyEngine()
        suggestions = engine.suggest_optimizations(metrics)
        engine.mark_executed(suggestion)
        report = engine.get_report()
    """

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}
        self.query_log: List[Dict] = []
        self.max_log_size = cfg.get('max_query_log', 10000)
        self._last_optimized: Dict[str, datetime] = {}
        self._thresholds = self._load_thresholds(config or {})
        # 熵结果缓存（避免频繁重算）
        self._entropy_cache: Optional[float] = None
        self._entropy_cache_time: float = 0.0
        self._entropy_cache_ttl: float = 60.0  # 秒

    def _load_thresholds(self, config: Dict) -> Dict[str, float]:
        """从配置加载阈值"""
        return {
            'CACHE_HIT_RATE_MIN': config.get('cache_hit_rate_min', 0.6),
            'AVG_LATENCY_MAX_MS': config.get('avg_latency_max_ms', 20),
            'CONFLICT_COUNT_MAX': config.get('conflict_count_max', 100),
            'LOW_QUALITY_RATIO_MAX': config.get('low_quality_ratio_max', 0.3),
            'PREHEAT_ACCURACY_MIN': config.get('preheat_accuracy_min', 0.5),
        }

    # ── 系统熵计算 ─────────────────────────────────────

    def calculate_system_entropy(
        self,
        index_stats: Optional[Dict] = None,
        cache_stats: Optional[Dict] = None,
        rule_stats: Optional[Dict] = None,
    ) -> float:
        """
        计算系统整体熵（0~1），结果缓存 60 秒。

        熵来源：
        1. 查询结果熵（30%）：结果集的多样性
        2. 缓存命中波动熵（30%）：命中率的稳定性
        3. 规则置信度分布熵（40%）：置信度的集中程度
        """
        now = time.time()
        if (self._entropy_cache is not None
                and now - self._entropy_cache_time < self._entropy_cache_ttl):
            return self._entropy_cache

        entropy = 0.0

        # 1. 查询结果熵
        result_entropy = self._calc_query_entropy()
        entropy += result_entropy * 0.3

        # 2. 缓存命中率波动熵
        cache_entropy = self._calc_cache_entropy(cache_stats or {})
        entropy += cache_entropy * 0.3

        # 3. 置信度分布熵
        confidence_entropy = self._calc_confidence_entropy(rule_stats or {})
        entropy += confidence_entropy * 0.4

        entropy = min(1.0, entropy)

        self._entropy_cache = entropy
        self._entropy_cache_time = now
        return entropy

    def _calc_query_entropy(self) -> float:
        """查询结果集的多样性熵"""
        if len(self.query_log) < 100:
            return 0.0
        recent = self.query_log[-100:]
        all_results = [
            rid for q in recent for rid in q.get('result_ids', [])
        ]
        if not all_results:
            return 0.0
        counter = Counter(all_results)
        probs = np.array(list(counter.values())) / len(all_results)
        entropy = float(-np.sum(probs * np.log2(probs + 1e-10)))
        # 归一化：log2(794) ≈ 9.6 为最大熵
        return min(1.0, entropy / 9.6)

    def _calc_cache_entropy(self, cache_stats: Dict) -> float:
        """缓存命中率的波动熵"""
        hit_rates = cache_stats.get('cache_hit_rate_history', [])
        if len(hit_rates) < 10:
            return 0.0
        variance = float(np.var(hit_rates[-50:]))
        return min(1.0, variance / 0.1)

    def _calc_confidence_entropy(self, rule_stats: Dict) -> float:
        """置信度分布的集中程度"""
        confidences = rule_stats.get('confidence_distribution', [])
        if len(confidences) < 5:
            return 0.0
        bins = np.histogram(confidences, bins=5, range=(0, 1))[0]
        probs = bins / (len(confidences) + 1e-10)
        entropy = float(-np.sum(probs * np.log2(probs + 1e-10)))
        return min(1.0, entropy / 2.32)

    # ── 优化建议生成 ─────────────────────────────────────

    def suggest_optimizations(self, metrics: Dict) -> List[OptimizationAction]:
        """
        根据当前指标自动生成优化建议。

        Args:
            metrics: 包含 cache_hit_rate, avg_query_latency_ms,
                     conflict_count, low_quality_ratio, preheat_accuracy
        """
        suggestions: List[OptimizationAction] = []
        t = self._thresholds

        # 1. 缓存命中率低 → 调整热缓存阈值
        cache_hit_rate = metrics.get('cache_hit_rate', 0.5)
        if cache_hit_rate < t['CACHE_HIT_RATE_MIN']:
            suggestions.append(OptimizationAction(
                type='cache_threshold_adjust',
                target='HOT_THRESHOLD',
                description=(
                    f'缓存命中率偏低 ({cache_hit_rate:.1%})，'
                    f'建议降低热缓存阈值'
                ),
                estimated_cost=0.1,
                predicted_improvement=0.15,
            ))

        # 2. 查询延迟高 → 优化索引
        avg_latency = metrics.get('avg_query_latency_ms', 10)
        if avg_latency > t['AVG_LATENCY_MAX_MS']:
            suggestions.append(OptimizationAction(
                type='index_optimization',
                target='suffix_tree',
                description=f'查询延迟偏高 ({avg_latency}ms)，建议重建后缀树索引',
                estimated_cost=0.5,
                predicted_improvement=0.25,
            ))

        # 3. 规则冲突多 → 免疫系统清理
        conflict_count = metrics.get('conflict_count', 0)
        if conflict_count > t['CONFLICT_COUNT_MAX']:
            suggestions.append(OptimizationAction(
                type='immune_cleanup',
                target='regulatory_t_cells',
                description=f'检测到 {conflict_count} 条冲突记录，建议执行 NK 清除',
                estimated_cost=0.2,
                predicted_improvement=0.2,
            ))

        # 4. 低质量规则多 → 批量进化
        low_quality_ratio = metrics.get('low_quality_ratio', 0)
        if low_quality_ratio > t['LOW_QUALITY_RATIO_MAX']:
            suggestions.append(OptimizationAction(
                type='evolution_boost',
                target='pending_evolutions',
                description=(
                    f'低质量规则占比 {low_quality_ratio:.1%}，'
                    f'建议批量执行进化任务'
                ),
                estimated_cost=0.3,
                predicted_improvement=0.3,
            ))

        # 5. 预热准确率低 → 调整周期检测
        preheat_accuracy = metrics.get('preheat_accuracy', 0)
        if preheat_accuracy < t['PREHEAT_ACCURACY_MIN']:
            suggestions.append(OptimizationAction(
                type='time_crystal_tuning',
                target='periodicity_detection',
                description=(
                    f'预热准确率偏低 ({preheat_accuracy:.1%})，'
                    f'建议调整周期检测灵敏度'
                ),
                estimated_cost=0.15,
                predicted_improvement=0.1,
            ))

        return self._prioritize(suggestions)

    def _prioritize(self, actions: List[OptimizationAction]) -> List[OptimizationAction]:
        """按信息增益/成本比、冷却状态排序"""
        now = datetime.now()

        def score(action: OptimizationAction) -> float:
            last = self._last_optimized.get(action.type)
            if last and (now - last).total_seconds() < action.cooldown_hours * 3600:
                return -1.0  # 冷却中，排到最后
            return action.predicted_improvement / max(action.estimated_cost, 0.01)

        actions.sort(key=score, reverse=True)
        return actions

    def mark_executed(self, action: OptimizationAction):
        """标记某操作已执行（用于冷却周期保护）"""
        self._last_optimized[action.type] = datetime.now()

    # ── 查询记录 ─────────────────────────────────────

    def record_query(
        self,
        query: str,
        result_ids: List[str],
        latency_ms: float,
        cache_hit: bool = False,
    ):
        """记录一次查询，用于熵计算（新数据到达时清空熵缓存）。"""
        self._entropy_cache = None  # 新查询使缓存失效
        self.query_log.append({
            'query': query,
            'result_ids': result_ids,
            'latency_ms': latency_ms,
            'cache_hit': cache_hit,
            'timestamp': datetime.now().isoformat(),
        })
        if len(self.query_log) > self.max_log_size:
            self.query_log = self.query_log[-self.max_log_size:]

    # ── 报告 ─────────────────────────────────────────

    def get_report(self) -> Dict:
        """生成熵报告"""
        if not self.query_log:
            return {"status": "insufficient_data"}

        recent = self.query_log[-100:]
        latencies = [q.get('latency_ms', 0) for q in recent]
        cache_hits = [1 if q.get('cache_hit') else 0 for q in recent]
        queries = [q.get('query', '') for q in recent]

        return {
            "query_diversity": len(set(queries)),
            "avg_latency_ms": round(float(np.mean(latencies)), 2),
            "latency_std_ms": round(float(np.std(latencies)), 2),
            "cache_hit_rate": round(float(np.mean(cache_hits)), 4),
            "total_queries": len(self.query_log),
            "estimated_system_entropy": round(
                self.calculate_system_entropy(
                    {}, {'cache_hit_rate_history': cache_hits}, {}
                ), 4
            ),
            "pending_optimizations": len([
                t for t, last in self._last_optimized.items()
                if (datetime.now() - last).total_seconds() < 86400
            ]),
        }
