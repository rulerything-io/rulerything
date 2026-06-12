# Copyright 2026 Rule-KB Project Authors
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

# Copyright 2026 Rule-KB Project Authors
"""
AdaptiveRuleSystem — Everything 风格集成协调器（Phase 3）

核心路径（always on）：
- EnhancedEverythingIndex（多键排序 + 增量更新）
- EntropyEngine（熵计算 + 优化建议）

可选组件：
- RuleImmuneSystem（Phase 2）
- TimeDecayCache（Phase 3）
- SemanticEngine（Phase 3 插件，默认关闭）

设计原则：
1. Everything 铁律优先：核心路径零外部依赖
2. 每个组件独立启用/禁用
3. v1.0 API 和数据格式完全向后兼容
4. 语义插件故障不影响核心检索
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional

from rule import Rule


class AdaptiveRuleSystem:
    """
    自适应规则系统 — Everything 风格集成。

    使用方式：
        system = AdaptiveRuleSystem(config)
        results = system.query("Python 类型注解")
        system.process_feedback("python/001", False, "太宽泛")
        report = system.optimize()
    """

    def __init__(self, config: Dict, base_dir: str = 'data',
                 rules: Optional[List[Rule]] = None):
        self.config = config
        self._base_dir = base_dir
        self.rules: Dict[str, Rule] = {}

        # ── 核心：Everything 风格索引（始终启用） ──────
        from enhanced_index import EnhancedEverythingIndex
        self.index = EnhancedEverythingIndex()

        if rules is not None:
            # 使用已有 rules 列表（避免重复加载）
            for rule in rules:
                self.index.add(rule)
                self.rules[rule.id] = rule
        else:
            self._load_rules_into_index()

        # ── Phase 1：熵引擎（始终启用） ────────────────
        from entropy_engine import EntropyEngine
        self.entropy_engine = EntropyEngine(config.get("entropy", {}))

        # ── Phase 2：免疫系统（可选） ─────────────────
        self.immune_system = None
        if config.get("immune", {}).get("enabled", False):
            from immune_system import RuleImmuneSystem
            self.immune_system = RuleImmuneSystem(config.get("immune", {}))

        # ── Phase 3：时间衰减缓存（可选） ─────────────
        self.cache = None
        if config.get("cache", {}).get("enabled", False):
            from time_decay_cache import TimeDecayCache
            self.cache = TimeDecayCache(
                max_size=config["cache"].get("max_size", 5000),
                decay_half_life=config["cache"].get("decay_half_life", 3600),
                preheat_threshold=config["cache"].get("preheat_threshold", 1.0),
            )
            if config["cache"].get("preheat_on_start", True):
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(self._initial_preheat())
                except RuntimeError:
                    pass  # 无事件循环时跳过预热

        # ── Phase 3 插件：语义搜索（独立，默认关闭） ──
        self.semantic = None
        if config.get("semantic_search", {}).get("enabled", False):
            try:
                from semantic_plugin import SemanticEngine
                self.semantic = SemanticEngine(
                    backend=config["semantic_search"].get("backend", "bm25"),
                )
                self._build_semantic_index()
            except Exception:
                self.semantic = None  # 静默降级

    def _load_rules_into_index(self):
        """加载规则到 Everything 索引。"""
        from storage import RuleStorage
        base_dir = getattr(self, '_base_dir', 'data')
        storage = RuleStorage(base_dir)
        for rule in storage.list():
            self.index.add(rule)
            self.rules[rule.id] = rule

    def _build_semantic_index(self):
        """构建语义索引（仅 title，提高精度减少噪音）。"""
        try:
            corpus = [
                (rid, r.title)
                for rid, r in self.rules.items()
            ]
            self.semantic.build(corpus)
        except Exception:
            self.semantic = None

    # ── 查询 ─────────────────────────────────────────

    def query(
        self,
        query_text: str,
        sort_by: str = 'title',
        category: Optional[str] = None,
        use_semantic: bool = False,
        limit: int = 10,
    ) -> List[Rule]:
        """
        统一查询入口（Everything 风格）。

        Args:
            query_text: 查询文本
            sort_by: 排序方式（title | confidence | hit_count）
            use_semantic: 是否启用语义搜索插件
            limit: 返回数量
        """
        start = datetime.now()
        result_rules = []

        if use_semantic and self.semantic:
            sem_results = self.semantic.search(query_text, limit)
            seen = set()
            for rid, score in sem_results:
                rule = self.index.get(rid)
                if rule:
                    rule.confidence = score
                    if category and rule.category != category:
                        continue
                    result_rules.append(rule)
                    seen.add(rid)

            if len(result_rules) < limit:
                remaining = limit - len(result_rules)
                prefix_results = self.index.search_prefix(
                    query_text, sort_by=sort_by, category=category, limit=remaining
                )
                for r in prefix_results:
                    if r.id not in seen:
                        result_rules.append(r)
        else:
            result_rules = self.index.search_prefix(
                query_text, sort_by=sort_by, category=category, limit=limit
            )

        # 记录缓存访问
        if self.cache:
            for r in result_rules:
                self.cache.record_access(r.id)

        # 记录熵引擎指标
        latency_ms = (datetime.now() - start).total_seconds() * 1000
        self.entropy_engine.record_query(
            query_text,
            [r.id for r in result_rules],
            latency_ms,
            cache_hit=bool(self.cache and
                          any(r.id in self.cache.cache
                              for r in result_rules[:3])),
        )

        return result_rules

    # ── 缓存预热 ─────────────────────────────────────

    async def _initial_preheat(self):
        """启动时异步预热。"""
        try:
            if self.cache:
                await self.cache.auto_preheat(
                    lambda rid: self.index.get(rid),
                    set(self.rules.keys()),
                )
        except Exception:
            pass  # 预热失败不影响系统运行

    # ── 反馈处理 ─────────────────────────────────────

    def process_feedback(self, rule_id: str, satisfied: bool, context: str = ""):
        """处理用户反馈。"""
        # Phase 2：免疫系统评估
        if self.immune_system:
            rule = self.rules.get(rule_id)
            if rule:
                report = self.immune_system.evaluate_health(rule)
                if not satisfied and report.antibodies:
                    self._schedule_evolution(rule_id, report.antibodies)

        # v1.0 EvolutionEngine（始终可用）
        from evolution import EvolutionEngine
        from storage import RuleStorage
        storage = RuleStorage(self._base_dir)
        engine = EvolutionEngine(storage)
        engine.collect_feedback(rule_id, satisfied, context)

    def _schedule_evolution(self, rule_id: str, antibodies: List[str]):
        """将免疫系统抗体转为进化任务。"""
        from evolution import EvolutionEngine
        from storage import RuleStorage
        storage = RuleStorage(self._base_dir)
        engine = EvolutionEngine(storage)
        for antibody in antibodies[:1]:  # 取第一条抗体
            engine.collect_feedback(rule_id, False, antibody)

    # ── 系统优化 ─────────────────────────────────────

    def optimize(self) -> Dict:
        """熵引擎驱动优化。"""
        metrics = {
            'cache_hit_rate':
                self.entropy_engine.get_report().get('cache_hit_rate', 0),
            'avg_query_latency_ms':
                self.entropy_engine.get_report().get('avg_latency_ms', 0),
            'conflict_count':
                len(self.immune_system.regulatory_t_cells)
                if self.immune_system else 0,
            'low_quality_ratio':
                len(self.immune_system.nk_targets) / max(1, len(self.rules))
                if self.immune_system else 0,
            'preheat_accuracy': 0,
        }
        suggestions = self.entropy_engine.suggest_optimizations(metrics)

        executed = []
        for suggestion in suggestions[:3]:
            result = self._execute_optimization(suggestion)
            self.entropy_engine.mark_executed(suggestion)
            executed.append({
                'type': suggestion.type,
                'target': suggestion.target,
                'result': result,
            })

        return {
            'optimizations': executed,
            'metrics': metrics,
            'entropy': self.entropy_engine.get_report(),
        }

    def _execute_optimization(self, action) -> Dict:
        t = action.type
        if t == 'cache_threshold_adjust' and self.cache:
            self.cache.preheat_threshold *= 1.1
            return {'new_threshold': self.cache.preheat_threshold}
        elif t == 'immune_cleanup' and self.immune_system:
            cleared = self.immune_system.nk_clear()
            for rid in cleared:
                self.index.remove(rid)
                self.rules.pop(rid, None)
            return {'cleared_count': len(cleared)}
        elif t == 'evolution_boost':
            from evolution import EvolutionEngine
            from storage import RuleStorage
            storage = RuleStorage(self._base_dir)
            engine = EvolutionEngine(storage)
            changes = engine.apply_pending_evolutions()
            return {'applied_count': len(changes)}
        return {'status': 'no_action'}

    def get_full_status(self) -> Dict:
        """获取完整系统状态。"""
        return {
            "index": self.index.stats(),
            "entropy": self.entropy_engine.get_report(),
            "immune": self.immune_system.get_health_summary()
                if self.immune_system else {"status": "disabled"},
            "cache": {
                "size": len(self.cache.cache) if self.cache else 0,
                "max_size": self.cache.max_size if self.cache else 0,
            } if self.cache else {"status": "disabled"},
            "semantic": "enabled" if self.semantic else "disabled",
            "rules_total": len(self.rules),
        }
