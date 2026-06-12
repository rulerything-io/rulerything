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
规则免疫系统 — 自动质量监控与防护（Phase 2）

模拟生物免疫系统，自动识别、标记和清除低质量规则。

组件：
- 记忆T细胞：记住已验证的好规则
- 调节T细胞：抑制冲突规则
- 自然杀伤细胞：清除死亡规则
- B细胞：产生抗体（修复方案）

与 EvolutionEngine 协作：抗体自动转为进化任务。

Design philosophy:
- 纯规则评估，零外部依赖
- 与现有 EvolutionEngine 互补
- 所有权重通过 config 控制
"""

import hashlib
import re
import statistics
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from rule import Rule


class RuleHealthStatus(Enum):
    HEALTHY = "healthy"
    WEAKENED = "weakened"
    INFECTED = "infected"
    DEAD = "dead"


@dataclass
class HealthReport:
    """单条规则的健康报告"""
    rule_id: str
    status: RuleHealthStatus
    score: float
    dimensions: Dict[str, float]
    conflicts: List[str] = field(default_factory=list)
    antibodies: List[str] = field(default_factory=list)


class RuleImmuneSystem:
    """
    规则免疫系统。

    使用方式：
        immune = RuleImmuneSystem()
        report = immune.evaluate_health(rule)
        results = immune.batch_scan(rules)
        cleared = immune.nk_clear()
    """

    def __init__(self, config: Optional[Dict] = None):
        # 记忆T细胞
        self.memory_t_cells: Dict[str, str] = {}
        self.memory_confidence: Dict[str, float] = {}

        # 调节T细胞：冲突记录
        self.regulatory_t_cells: Dict[Tuple[str, str], float] = {}

        # 自然杀伤细胞：待清除规则
        self.nk_targets: Set[str] = set()

        # B细胞：抗体
        self.b_cells: Dict[str, List[str]] = {}

        # 权重配置
        cfg = config or {}
        self.weights = {
            'confidence': cfg.get('weight_confidence', 0.3),
            'timeliness': cfg.get('weight_timeliness', 0.2),
            'conflict_free': cfg.get('weight_conflict_free', 0.25),
            'usefulness': cfg.get('weight_usefulness', 0.15),
            'completeness': cfg.get('weight_completeness', 0.1),
        }

    # ── 健康评估 ─────────────────────────────────────

    def evaluate_health(
        self, rule: Rule, context: Optional[Dict] = None,
    ) -> HealthReport:
        """
        评估单条规则的健康度。

        评分维度：
        - 置信度（30%）：自身置信度
        - 时效性（20%）：最近验证时间
        - 冲突度（25%）：与其他规则的冲突程度
        - 有用性（15%）：实际使用频率
        - 完整性（10%）：内容结构
        """
        scores: Dict[str, float] = {}

        # 1. 置信度
        scores['confidence'] = rule.confidence

        # 2. 时效性
        scores['timeliness'] = self._score_timeliness(rule)

        # 3. 冲突度
        conflict_score = self._calculate_conflict_score(rule.id)
        scores['conflict_free'] = 1.0 - conflict_score

        # 4. 有用性
        scores['usefulness'] = min(1.0, rule.hit_count / 100)

        # 5. 完整性
        scores['completeness'] = self._score_completeness(rule.content)

        # 加权总分
        total = sum(scores[k] * self.weights[k] for k in self.weights)

        # 状态判定
        if total >= 0.7:
            status = RuleHealthStatus.HEALTHY
            self._add_to_memory(rule)
        elif total >= 0.4:
            status = RuleHealthStatus.WEAKENED
        elif total >= 0.2:
            status = RuleHealthStatus.INFECTED
            self.nk_targets.add(rule.id)
        else:
            status = RuleHealthStatus.DEAD
            self.nk_targets.add(rule.id)

        # 生成抗体
        antibodies = self.generate_antibody(rule, scores)

        # 冲突规则列表
        conflicts = self._get_conflict_ids(rule.id)

        return HealthReport(
            rule_id=rule.id,
            status=status,
            score=round(total, 4),
            dimensions=scores,
            conflicts=conflicts,
            antibodies=antibodies,
        )

    def _score_timeliness(self, rule: Rule) -> float:
        if rule.last_verified is None:
            return 0.3
        days_since = (datetime.now() - rule.last_verified).days
        return max(0.0, 1.0 - days_since / 365)

    def _score_completeness(self, content: str) -> float:
        score = 0.0
        if len(content) > 10:
            score += 0.3
        if any(kw in content for kw in ['应该', '必须', '建议', '示例']):
            score += 0.3
        if '```' in content or '`' in content:
            score += 0.2
        if any(kw in content for kw in ['因为', '由于', '导致', '避免']):
            score += 0.2
        return score

    # ── 批量扫描 ─────────────────────────────────────

    def batch_scan(
        self, rules: List[Rule], auto_cleanup: bool = False,
    ) -> Dict[str, List[HealthReport]]:
        """批量扫描所有规则，按健康状态分组。"""
        self._batch_detect_conflicts(rules)

        results: Dict[str, List[HealthReport]] = {
            "healthy": [], "weakened": [], "infected": [], "dead": [],
        }
        for rule in rules:
            report = self.evaluate_health(rule)
            results[report.status.value].append(report)

        if auto_cleanup and results["dead"]:
            dead_ids = [r.rule_id for r in results["dead"]]
            self.nk_clear(dead_ids)

        return results

    # ── 冲突检测 ─────────────────────────────────────

    def _batch_detect_conflicts(self, rules: List[Rule]):
        """按分类分组后检测冲突（同分类才可能冲突，O(n²) 降为 O(k·m²)）。"""
        by_category: Dict[str, List[Rule]] = {}
        for rule in rules:
            by_category.setdefault(rule.category, []).append(rule)
        for cat_rules in by_category.values():
            n = len(cat_rules)
            for i in range(n):
                for j in range(i + 1, n):
                    self.detect_conflict(cat_rules[i], cat_rules[j])

    def detect_conflict(self, rule_a: Rule, rule_b: Rule) -> float:
        """检测两条规则之间的冲突。返回 0~1 冲突分数。"""
        content_a = rule_a.content.lower()
        content_b = rule_b.content.lower()
        conflict_score = 0.0

        # 对立词检测
        opposites = [
            ('应该', '不应该'), ('必须', '禁止'), ('推荐', '不推荐'),
            ('使用', '避免使用'), ('yes', 'no'), ('true', 'false'),
        ]
        for pos, neg in opposites:
            if (pos in content_a and neg in content_b) or \
               (neg in content_a and pos in content_b):
                conflict_score += 0.3

        # 阈值冲突检测
        thresholds_a = re.findall(r'[<>]=?\s*(\d+)', content_a)
        thresholds_b = re.findall(r'[<>]=?\s*(\d+)', content_b)
        for ta in thresholds_a:
            for tb in thresholds_b:
                if abs(int(ta) - int(tb)) < 100:
                    conflict_score += 0.2

        conflict_score = min(1.0, conflict_score)
        self.regulatory_t_cells[(rule_a.id, rule_b.id)] = conflict_score
        self.regulatory_t_cells[(rule_b.id, rule_a.id)] = conflict_score
        return conflict_score

    def _calculate_conflict_score(self, rule_id: str) -> float:
        scores = [
            score for (r1, r2), score in self.regulatory_t_cells.items()
            if r1 == rule_id
        ]
        return float(statistics.mean(scores)) if scores else 0.0

    def _get_conflict_ids(self, rule_id: str) -> List[str]:
        return [
            r2 if r1 == rule_id else r1
            for (r1, r2) in self.regulatory_t_cells
            if r1 == rule_id
        ]

    # ── 抗体生成 ─────────────────────────────────────

    def generate_antibody(self, rule: Rule, scores: Dict[str, float]) -> List[str]:
        """根据评分生成修复建议"""
        antibodies = []

        if '过时' in rule.content or '已废弃' in rule.content:
            antibodies.append("建议：检查并更新为最新版本 API")

        if self._calculate_conflict_score(rule.id) > 0.5:
            antibodies.append("建议：添加条件约束或优先级说明来解决冲突")

        if len(rule.content) < 50:
            antibodies.append("建议：补充具体示例和原因说明")

        if rule.content.count('可能') > 2:
            antibodies.append("建议：减少不确定性描述，增加确定性条件")

        if not antibodies:
            antibodies.append("建议：重新评估规则的适用场景和边界条件")

        if antibodies:
            self.b_cells[rule.id] = antibodies

        return antibodies

    # ── NK 清除 ──────────────────────────────────────

    def nk_clear(self, rule_ids: Optional[List[str]] = None) -> List[str]:
        """
        自然杀伤细胞执行清除。

        Args:
            rule_ids: 要清除的规则。None 时清除所有 nk_targets。

        Returns:
            实际清除的规则 ID 列表
        """
        targets = set(rule_ids) if rule_ids else self.nk_targets.copy()
        if not targets:
            return []

        self.nk_targets -= targets

        # 清理冲突记录
        to_remove = [
            key for key in self.regulatory_t_cells
            if key[0] in targets or key[1] in targets
        ]
        for key in to_remove:
            del self.regulatory_t_cells[key]

        # 清理 B 细胞
        for rid in targets:
            self.b_cells.pop(rid, None)

        return list(targets)

    # ── 记忆 T 细胞 ──────────────────────────────────

    def _add_to_memory(self, rule: Rule):
        signature = self._generate_signature(rule)
        self.memory_t_cells[signature] = rule.id
        self.memory_confidence[signature] = rule.confidence

    @staticmethod
    def _generate_signature(rule: Rule) -> str:
        raw = f"{rule.title}:{rule.content[:100]}"
        return hashlib.md5(raw.encode()).hexdigest()

    # ── 健康报告 ─────────────────────────────────────

    def get_health_summary(self) -> Dict:
        """生成健康摘要"""
        return {
            "memory_t_cells": len(self.memory_t_cells),
            "regulatory_records": len(self.regulatory_t_cells),
            "nk_targets": len(self.nk_targets),
            "antibodies_available": len(self.b_cells),
        }
