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
EnhancedEverythingIndex — 多键排序 + 增量更新 + 前缀搜索增强（Phase 3）

符合 Everything 五条铁律：
1. 全内存索引 ✅
2. 排序数组 + 二分查找 ✅
3. 零外部依赖 ✅
4. 实时增量更新 ✅（add/update/remove 单条，无需全量 build）
5. 极致资源效率 ✅

新增能力 vs v1.0 EverythingStyleIndex：
- 4 个排序数组（title / category / confidence / hit_count）
- 增量更新（单条 add/update/remove）
- 通配符搜索（prefix* / *suffix*）
"""

import bisect
from typing import Dict, List, Optional, Callable, Tuple

from rule import Rule


class EnhancedEverythingIndex:
    """Everything 风格索引增强版。"""

    def __init__(self):
        # 主存储
        self._rules: Dict[str, Rule] = {}

        # 多键排序数组 — (sort_key, rule_id)
        self.sorted_by_title: List[Tuple[str, str]] = []
        self.sorted_by_category: List[Tuple[str, str]] = []
        self.sorted_by_confidence: List[Tuple[float, str]] = []
        self.sorted_by_hit_count: List[Tuple[int, str]] = []

        # 标签倒排索引
        self.tag_index: Dict[str, List[str]] = {}

        # 热缓存
        self.hot_cache: Dict[str, Rule] = {}
        self.HOT_THRESHOLD = 10

    # ── 增量更新 ─────────────────────────────────────

    def add(self, rule: Rule):
        """添加单条规则到索引。"""
        self._rules[rule.id] = rule
        self._insert_sorted(self.sorted_by_title, (rule.title.lower(), rule.id))
        self._insert_sorted(self.sorted_by_category, (rule.category, rule.id))
        self._insert_sorted(self.sorted_by_confidence, (-rule.confidence, rule.id))
        self._insert_sorted(self.sorted_by_hit_count, (-rule.hit_count, rule.id))

        # 标签索引
        for tag in rule.tags:
            self.tag_index.setdefault(tag, []).append(rule.id)

        # 热缓存
        if rule.hit_count >= self.HOT_THRESHOLD:
            self.hot_cache[rule.id] = rule

    def update(self, rule_id: str, **fields):
        """更新规则的索引（先删后加）。"""
        old = self._rules.get(rule_id)
        if not old:
            return
        self.remove(rule_id)
        for k, v in fields.items():
            setattr(old, k, v)
        self.add(old)

    def remove(self, rule_id: str):
        """从所有索引中移除单条规则。"""
        rule = self._rules.pop(rule_id, None)
        if not rule:
            return

        keys_to_remove = [
            (self.sorted_by_title, (rule.title.lower(), rule_id)),
            (self.sorted_by_category, (rule.category, rule_id)),
            (self.sorted_by_confidence, (-rule.confidence, rule_id)),
            (self.sorted_by_hit_count, (-rule.hit_count, rule_id)),
        ]
        for arr, key in keys_to_remove:
            pos = bisect.bisect_left(arr, key)
            while pos < len(arr) and arr[pos] == key:
                arr.pop(pos)
                break

        # 标签索引
        for tag in rule.tags:
            if tag in self.tag_index:
                try:
                    self.tag_index[tag].remove(rule_id)
                except ValueError:
                    pass
                if not self.tag_index[tag]:
                    del self.tag_index[tag]

        # 热缓存
        self.hot_cache.pop(rule_id, None)

    @staticmethod
    def _insert_sorted(arr: list, item: tuple):
        """二分插入保持排序。"""
        pos = bisect.bisect_left(arr, item)
        arr.insert(pos, item)

    # ── 检索 ─────────────────────────────────────────

    def get(self, rule_id: str) -> Optional[Rule]:
        """通过 ID 获取规则。"""
        return self._rules.get(rule_id)

    def search_prefix(
        self,
        prefix: str,
        sort_by: str = 'title',
        category: Optional[str] = None,
        limit: int = 10,
    ) -> List[Rule]:
        """
        带排序选择的前缀搜索。

        sort_by: title | confidence | hit_count
        """
        sort_map = {
            'title': self.sorted_by_title,
            'confidence': self.sorted_by_confidence,
            'hit_count': self.sorted_by_hit_count,
        }
        arr = sort_map.get(sort_by, self.sorted_by_title)
        key_prefix = prefix.lower() if sort_by == 'title' else None

        if sort_by == 'title':
            start = bisect.bisect_left(arr, (key_prefix, ''))
        else:
            start = 0  # 非 title 排序无法前缀定位

        results = []
        seen = set()
        for sort_key, rule_id in arr[start:]:
            if sort_by == 'title':
                if not sort_key.startswith(key_prefix):
                    break
            rule = self._rules.get(rule_id)
            if not rule or rule.id in seen:
                continue
            if category and rule.category != category:
                continue
            results.append(rule)
            seen.add(rule.id)
            if len(results) >= limit:
                break
        return results

    def search_exact(self, title: str) -> Optional[Rule]:
        """精确标题匹配。"""
        key = title.lower()
        pos = bisect.bisect_left(self.sorted_by_title, (key, ''))
        if pos < len(self.sorted_by_title) and self.sorted_by_title[pos][0] == key:
            rule_id = self.sorted_by_title[pos][1]
            return self._rules.get(rule_id)
        return None

    def search_by_tag(self, tag: str, limit: int = 20) -> List[Rule]:
        """标签检索。"""
        return [
            self._rules[rid] for rid in self.tag_index.get(tag, [])[:limit]
            if rid in self._rules
        ]

    def search_wildcard(self, pattern: str, limit: int = 10) -> List[Rule]:
        """通配符搜索。"""
        if pattern.endswith('*') and not pattern.startswith('*'):
            return self.search_prefix(pattern.rstrip('*'), limit=limit)
        q = pattern.strip('*').lower()
        results = []
        for rule in self._rules.values():
            if q in rule.title.lower() or q in rule.content.lower():
                results.append(rule)
                if len(results) >= limit:
                    break
        return results

    # ── 全量构建（兼容 v1.0） ─────────────────────────

    def build(self, rules: List[Rule]):
        """全量重建索引（兼容 v1.0 API）。"""
        self.__init__()
        for rule in rules:
            self.add(rule)

    # ── 统计 ─────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "total_rules": len(self._rules),
            "tag_count": len(self.tag_index),
            "hot_cache_size": len(self.hot_cache),
        }
