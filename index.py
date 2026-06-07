"""
Everything 风格索引层 — 排序数组 + 二分查找 + 前缀搜索 + 标签索引 + 热缓存
"""

import bisect
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from rule import Rule


class EverythingStyleIndex:
    """借鉴 Everything 搜索引擎的索引策略。

    核心机制：
    - 排序数组 + 二分查找 → 精确匹配 O(log N)
    - 前缀扫描 → 通配符匹配 O(log N + M)
    - 标签哈希表 → 标签检索 O(1)
    - 热度分层缓存 → 高频常驻内存
    """

    # 可配置阈值
    HOT_THRESHOLD = 10         # hit_count ≥ 此值进入热缓存
    COLD_DAYS = 30             # 超过此天数未访问进入冷区
    HOT_UPDATE_INTERVAL = 10   # 每 N 次命中触发缓存刷新

    def __init__(self, rules: Optional[List[Rule]] = None):
        self._rules: Dict[str, Rule] = {}           # id → Rule（索引持有引用）
        self.sorted_titles: List[str] = []           # 排序后的标题列表
        self.title_to_id: Dict[str, str] = {}        # 标题 → id
        self.tag_index: Dict[str, List[str]] = {}    # 标签 → [rule_ids]
        self.hot_cache: Dict[str, Rule] = {}         # 高频规则（内存常驻）
        self.hot_ids: set = set()                    # 热缓存 ID 集合
        self.cold_ids: set = set()                   # 低频规则 ID 集合

        # 统计
        self.index_version: int = 0
        self._hit_counter: int = 0                   # 累计命中计数
        self.total_search_count: int = 0
        self.cache_hit_count: int = 0

        if rules:
            self.build(rules)

    # ── 索引构建 ───────────────────────────────────────

    def build(self, rules: List[Rule]):
        """全量重建索引。"""
        self._rules = {r.id: r for r in rules}
        self._build_sorted_titles()
        self._build_tag_index()
        self._classify_hot_cold()
        self.index_version += 1

    def _build_sorted_titles(self):
        """构建排序标题数组。"""
        title_list = []
        title_to_id = {}
        for r in self._rules.values():
            title_list.append(r.title)
            title_to_id[r.title] = r.id
        title_list.sort()
        self.sorted_titles = title_list
        self.title_to_id = title_to_id

    def _build_tag_index(self):
        """构建标签倒排索引。"""
        index: Dict[str, List[str]] = {}
        for r in self._rules.values():
            for tag in r.tags:
                index.setdefault(tag, []).append(r.id)
        self.tag_index = index

    def _classify_hot_cold(self):
        """热度分层：将规则分为热/温/冷三级。"""
        now = datetime.now()
        self.hot_ids = set()
        self.cold_ids = set()
        self.hot_cache.clear()

        for r in self._rules.values():
            if r.hit_count >= self.HOT_THRESHOLD:
                self.hot_ids.add(r.id)
                self.hot_cache[r.id] = r
            elif (r.last_hit is not None
                  and (now - r.last_hit).days > self.COLD_DAYS):
                self.cold_ids.add(r.id)

    # ── 命中记录 ───────────────────────────────────────

    def _record_hit(self, rule_id: str):
        """记录一次命中，更新统计和热度。"""
        rule = self._rules.get(rule_id)
        if rule is None:
            return
        rule.record_hit()
        self._hit_counter += 1

        # 周期性刷新热缓存
        if self._hit_counter % self.HOT_UPDATE_INTERVAL == 0:
            self._refresh_hot_cache()

    def _refresh_hot_cache(self):
        """增量刷新热缓存（无需全量重建）。"""
        for rule in self._rules.values():
            if (rule.hit_count >= self.HOT_THRESHOLD
                    and rule.id not in self.hot_ids):
                self.hot_ids.add(rule.id)
                self.hot_cache[rule.id] = rule

    # ── 搜索方法 ───────────────────────────────────────

    def search_exact(self, title: str) -> Optional[Rule]:
        """精确标题搜索 — O(log N) 二分查找。"""
        pos = bisect.bisect_left(self.sorted_titles, title)
        if pos < len(self.sorted_titles) and self.sorted_titles[pos] == title:
            rule_id = self.title_to_id[title]
            self.total_search_count += 1
            self._record_hit(rule_id)
            return self._from_cache_or_store(rule_id)
        return None

    def search_prefix(self, prefix: str, limit: int = 10) -> List[Rule]:
        """前缀搜索 — 类比 Everything 的 'win*' 通配符。"""
        start = bisect.bisect_left(self.sorted_titles, prefix)
        results = []
        for title in self.sorted_titles[start:]:
            if not title.startswith(prefix):
                break
            rule_id = self.title_to_id[title]
            self.total_search_count += 1
            self._record_hit(rule_id)
            rule = self._from_cache_or_store(rule_id)
            if rule:
                results.append(rule)
                if len(results) >= limit:
                    break
        return results

    def search_by_tag(self, tag: str, limit: int = 20) -> List[Rule]:
        """标签搜索 — O(1) 哈希表。"""
        rule_ids = self.tag_index.get(tag, [])
        results = []
        for rule_id in rule_ids[:limit]:
            self.total_search_count += 1
            self._record_hit(rule_id)
            rule = self._from_cache_or_store(rule_id)
            if rule:
                results.append(rule)
        return results

    def search(self, query: str, search_type: str = "exact",
               category: Optional[str] = None, limit: int = 10) -> List[Rule]:
        """统一搜索入口。

        Args:
            query: 搜索关键词
            search_type: exact | prefix | tag
            category: 分类过滤（可选）
            limit: 最大返回数量
        """
        if search_type == "tag":
            results = self.search_by_tag(query, limit)
        elif search_type == "prefix":
            results = self.search_prefix(query, limit)
        else:
            rule = self.search_exact(query)
            results = [rule] if rule else []

        # 如果常规搜索无结果，回退到内容全文搜索
        if not results:
            results = self._search_content(query, limit)

        if category:
            results = [r for r in results if r.category == category]

        # 按置信度排序
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    def _search_content(self, query: str, limit: int = 10) -> List[Rule]:
        """在标题和内容中搜索关键词（不区分大小写）。"""
        q = query.lower()
        results = []
        for rule in self._rules.values():
            if q in rule.title.lower() or q in rule.content.lower():
                self.total_search_count += 1
                self._record_hit(rule.id)
                results.append(rule)
                if len(results) >= limit:
                    break
        return results

    def _from_cache_or_store(self, rule_id: str) -> Optional[Rule]:
        """优先从热缓存返回，否则从全量规则返回。"""
        if rule_id in self.hot_cache:
            self.cache_hit_count += 1
            return self.hot_cache[rule_id]
        return self._rules.get(rule_id)

    # ── 预热 ───────────────────────────────────────────

    def warmup(self, category: Optional[str] = None) -> dict:
        """预热缓存。

        Args:
            category: 可选，只预热指定分类

        Returns:
            {"loaded": int, "elapsed_ms": float, "category": str|None}
        """
        start = datetime.now()
        count = 0

        # 命中率 Top 20% 的规则加载到热缓存
        candidates = [
            r for r in self._rules.values()
            if r.hit_count > 0 and (not category or r.category == category)
        ]
        candidates.sort(key=lambda r: r.hit_count, reverse=True)
        top_n = max(1, len(candidates) // 5)

        for rule in candidates[:top_n]:
            if rule.id not in self.hot_ids:
                self.hot_ids.add(rule.id)
                self.hot_cache[rule.id] = rule
                count += 1

        elapsed = (datetime.now() - start).total_seconds() * 1000
        return {"loaded": count, "elapsed_ms": round(elapsed, 2), "category": category}

    # ── 统计与状态 ──────────────────────────────────────

    def stats(self) -> dict:
        """索引运行统计。"""
        return {
            "index_version": self.index_version,
            "total_rules_indexed": len(self._rules),
            "hot_cache_size": len(self.hot_cache),
            "cold_count": len(self.cold_ids),
            "total_searches": self.total_search_count,
            "cache_hit_count": self.cache_hit_count,
            "cache_hit_rate": (
                round(self.cache_hit_count / max(1, self.total_search_count) * 100, 1)
                if self.total_search_count > 0 else 0.0
            ),
            "tag_count": len(self.tag_index),
        }

    @property
    def is_ready(self) -> bool:
        """索引是否就绪（已有规则加载）。"""
        return len(self._rules) > 0
