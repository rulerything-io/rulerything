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
Everything 风格索引层 — 排序数组 + 二分查找 + 前缀搜索 + 标签索引 + 热缓存
"""

import bisect
import threading
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from nlp_utils import (
    extract_cjk_ngrams, extract_english_words, extract_words,
    has_cjk, is_cjk,
)
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
    HOT_THRESHOLD = 3          # hit_count ≥ 此值进入热缓存（v2.0 优化：从 10 降至 3）
    COLD_DAYS = 30             # 超过此天数未访问进入冷区
    HOT_UPDATE_INTERVAL = 5    # 每 N 次命中触发缓存刷新（v2.0 优化：从 10 降至 5）

    def __init__(self, rules: Optional[List[Rule]] = None):
        self._rules: Dict[str, Rule] = {}           # id → Rule（索引持有引用）
        self.sorted_titles: List[str] = []           # 排序后的标题列表
        self.title_to_id: Dict[str, str] = {}        # 标题 → id
        self.tag_index: Dict[str, List[str]] = {}    # 标签 → [rule_ids]
        self._content_index: Dict[str, Set[str]] = {}  # 内容关键词 → {rule_ids}（加速 _search_content）
        self.hot_cache: Dict[str, Rule] = {}         # 高频规则（内存常驻）
        self.hot_ids: set = set()                    # 热缓存 ID 集合
        self.cold_ids: set = set()                   # 低频规则 ID 集合

        # 分类名缓存（build() 时填充）
        self._category_names: set = set()

        # 统计
        self.index_version: int = 0
        self._hit_counter: int = 0                   # 累计命中计数
        self._lock = threading.Lock()                # 命中计数线程安全锁
        self._estimated_cache_bytes: int = 0         # 热缓存字节估算
        self.total_search_count: int = 0
        self.cache_hit_count: int = 0
        self._cumulative_latency_ms: float = 0.0     # 累计搜索延迟（用于 avg_latency_ms）
        self._latency_sample_count: int = 0          # record_latency 调用次数

        if rules:
            self.build(rules)

    # ── 索引构建 ───────────────────────────────────────

    def build(self, rules: List[Rule]):
        """全量重建索引。"""
        self._rules = {r.id: r for r in rules}
        self._category_names = {r.category for r in rules}
        self._build_sorted_titles()
        self._build_tag_index()
        self._build_content_index()
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

    def _build_content_index(self):
        """构建内容关键词倒排索引，加速 _search_content。"""
        idx: Dict[str, Set[str]] = {}
        for r in self._rules.values():
            words = set()
            for text in [r.title, r.content]:
                words |= extract_words(text)
            for w in words:
                idx.setdefault(w, set()).add(r.id)
        self._content_index = idx

    @staticmethod
    def _extract_content_words(text: str) -> Set[str]:
        """从文本中提取关键词（字母/数字/CJK，长度 ≥ 2）。"""
        return extract_words(text)

    def reconcile(self, storage_rules: List[Rule]) -> dict:
        """与存储层对比，将索引恢复到一致状态。

        对比 storage_rules 与 self._rules 的差异：
        - 存储中有但索引中没有的 → add
        - 索引中有但存储中已无的 → remove
        - 内容不同的已存在规则   → 重新 add（覆盖）

        Returns:
            {"added": [str], "removed": [str], "updated": [str]}
        """
        added: List[str] = []
        removed: List[str] = []
        updated: List[str] = []

        storage_map = {r.id: r for r in storage_rules}

        # 找需要添加或更新的
        for rid, rule in storage_map.items():
            existing = self._rules.get(rid)
            if existing is None:
                self.add(rule)
                added.append(rid)
            elif (existing.title != rule.title
                  or existing.content != rule.content
                  or existing.category != rule.category):
                # 内容变化，先删后加
                self.remove(rid)
                self.add(rule)
                updated.append(rid)

        # 找需要删除的（索引中有但存储已无）
        for rid in list(self._rules.keys()):
            if rid not in storage_map:
                self.remove(rid)
                removed.append(rid)

        if added or removed or updated:
            self.index_version += 1

        return {"added": added, "removed": removed, "updated": updated}

    def _classify_hot_cold(self):
        """热度分层：将规则分为热/温/冷三级。"""
        with self._lock:
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

    # ── 增量操作 ───────────────────────────────────────

    def add(self, rule: Rule):
        """Add a single rule to the index incrementally (thread-safe)."""
        with self._lock:
            self._rules[rule.id] = rule
            # Insert into sorted_titles maintaining sort order
            bisect.insort(self.sorted_titles, rule.title)
            self.title_to_id[rule.title] = rule.id
            # Update category names
            self._category_names.add(rule.category)
            # Update tag index
            for tag in rule.tags:
                self.tag_index.setdefault(tag, []).append(rule.id)
            # Update content index
            for w in self._extract_content_words(rule.title + ' ' + rule.content):
                self._content_index.setdefault(w, set()).add(rule.id)
            # Classify hot/cold
            if rule.hit_count >= self.HOT_THRESHOLD:
                self.hot_ids.add(rule.id)
                self.hot_cache[rule.id] = rule
            elif rule.last_hit and (datetime.now() - rule.last_hit).days > self.COLD_DAYS:
                self.cold_ids.add(rule.id)
            self.index_version += 1

    def remove(self, rule_id: str):
        """Remove a rule from the index (thread-safe)."""
        with self._lock:
            rule = self._rules.pop(rule_id, None)
            if rule is None:
                return
            # Remove from sorted_titles
            pos = bisect.bisect_left(self.sorted_titles, rule.title)
            if pos < len(self.sorted_titles) and self.sorted_titles[pos] == rule.title:
                self.sorted_titles.pop(pos)
            self.title_to_id.pop(rule.title, None)
            # Remove from tag index
            for tag in rule.tags:
                if tag in self.tag_index:
                    try:
                        self.tag_index[tag].remove(rule_id)
                    except ValueError:
                        pass
                    if not self.tag_index[tag]:
                        del self.tag_index[tag]
            # Remove from content index
            for w in self._extract_content_words(rule.title + ' ' + rule.content):
                if w in self._content_index:
                    self._content_index[w].discard(rule_id)
                    if not self._content_index[w]:
                        del self._content_index[w]
            # Remove from caches
            self.hot_ids.discard(rule_id)
            self.hot_cache.pop(rule_id, None)
            self.cold_ids.discard(rule_id)
            self.index_version += 1

    # ── 命中记录 ───────────────────────────────────────

    def _record_hit(self, rule_id: str):
        """记录一次命中，更新统计和热度。"""
        rule = self._rules.get(rule_id)
        if rule is None:
            return
        rule.record_hit()
        with self._lock:
            self._hit_counter += 1
            if self._hit_counter % self.HOT_UPDATE_INTERVAL == 0:
                self._refresh_hot_cache()

    def _refresh_hot_cache(self):
        """增量刷新热缓存，超过上限时驱逐低热度条目。"""
        for rule in self._rules.values():
            if (rule.hit_count >= self.HOT_THRESHOLD
                    and rule.id not in self.hot_ids):
                self.hot_ids.add(rule.id)
                self.hot_cache[rule.id] = rule

        # 驱逐：超过上限时移除最低命中率的条目
        MAX_HOT = 500
        if len(self.hot_cache) > MAX_HOT:
            sorted_hot = sorted(self.hot_cache.items(), key=lambda x: x[1].hit_count)
            for rule_id, _ in sorted_hot[:len(self.hot_cache) - MAX_HOT]:
                self.hot_ids.discard(rule_id)
                del self.hot_cache[rule_id]

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
        """标签搜索 — O(1) 哈希表，按置信度排序取 top-k。"""
        rule_ids = self.tag_index.get(tag, [])
        # 先获取所有规则，按置信度排序后截断
        candidates = []
        for rule_id in rule_ids:
            self.total_search_count += 1
            self._record_hit(rule_id)
            rule = self._from_cache_or_store(rule_id)
            if rule:
                candidates.append(rule)
        candidates.sort(key=lambda r: r.confidence, reverse=True)
        return candidates[:limit]

    # ── CJK 辅助 ────────────────────────────────────────

    def _scored_merge(self, scored: List[Tuple[Rule, float]],
                      category: Optional[str] = None,
                      limit: int = 10) -> List[Rule]:
        """合并去重 → 分类过滤 → 按分数排序 → top-k。"""
        best: Dict[str, Tuple[Rule, float]] = {}
        for rule, score in scored:
            if category and rule.category != category:
                continue
            rid = rule.id
            if rid not in best or score > best[rid][1]:
                best[rid] = (rule, score)
        sorted_ = sorted(best.values(), key=lambda x: (x[1], x[0].confidence), reverse=True)
        return [r for r, _ in sorted_[:limit]]

    def search(self, query: str, search_type: str = "exact",
               category: Optional[str] = None, limit: int = 10) -> List[Rule]:
        """统一搜索入口 — 全策略合并 + 匹配类型加权排序。

        所有策略同时执行，结果按匹配类型 × confidence 加权后合并，
        避免链式兜底导致早期不佳结果阻止后续策略。

        权重规则：
          - 标题精确匹配:  ×1.00
          - 标题前缀匹配:  ×0.85
          - 标签精确匹配:  ×0.90
          - 标题内容匹配:  ×0.80（标题包含查询）
          - 仅内容包含:    ×0.50（标题未命中时）
          - 拆词前缀匹配:  ×0.55
          - 拆词标签匹配:  ×0.50
          - 拆词内容匹配:  ×0.45
          - 分类命中加分:  +0.05（query.lower() 正好是某分类名时）

        Args:
            query: 搜索关键词
            search_type: exact | prefix | tag
            category: 分类过滤（可选）
            limit: 最大返回数量
        """
        scored: List[Tuple[Rule, float]] = []
        q_lower = query.lower()
        has_cjk_flag = has_cjk(query)

        # ── 1. 主策略 ──
        _primary_had_results = False
        if search_type == "tag":
            primary_results = self.search_by_tag(query, limit * 2)
            _primary_had_results = len(primary_results) > 0
            for r in primary_results:
                scored.append((r, r.confidence * 0.90))
        elif search_type == "prefix":
            primary_results = self.search_prefix(query, limit * 2)
            _primary_had_results = len(primary_results) > 0
            for r in primary_results:
                scored.append((r, r.confidence * 0.85))
        else:  # exact
            r = self.search_exact(query)
            if r:
                _primary_had_results = True
                scored.append((r, r.confidence * 1.0))

        # ── 2. 标签搜索（query 整体作为标签） ──
        # 主策略已有结果时降权标签匹配，避免高频低质标签淹没精准前缀匹配
        _tag_weight = 0.75 if _primary_had_results else 0.90
        for r in self.search_by_tag(q_lower, limit * 2):
            scored.append((r, r.confidence * _tag_weight))

        # ── 3. 内容包含搜索（全查询） ──
        # 先收集标题匹配的规则 ID，避免内容匹配重复添加
        _title_matched_ids = set()
        for r in self._search_content(query, limit, match_mode="title_only"):
            scored.append((r, r.confidence * 0.80))
            _title_matched_ids.add(r.id)
        for r in self._search_content(query, limit, match_mode="content_only"):
            if r.id not in _title_matched_ids:
                scored.append((r, r.confidence * 0.50))

        # ── 4. 拆词搜索 ──
        if has_cjk_flag:
            # CJK n-gram 内容搜索（2+ 字，不含单字符）
            for gram in extract_cjk_ngrams(query, min_len=2):
                for r in self._search_content(gram, limit // 2):
                    scored.append((r, r.confidence * 0.55))
            # 混合 CJK 文本中的英文词
            for word in extract_english_words(query):
                for r in self.search_prefix(word, limit // 3):
                    scored.append((r, r.confidence * 0.55))
                for r in self.search_by_tag(word, limit // 3):
                    scored.append((r, r.confidence * 0.50))
                for r in self._search_content(word, limit // 3):
                    scored.append((r, r.confidence * 0.45))
        else:
            # 纯英文: 按空格分词
            for word in q_lower.split():
                if len(word) < 2:
                    continue
                for r in self.search_prefix(word, limit // 3):
                    scored.append((r, r.confidence * 0.55))
                for r in self.search_by_tag(word, limit // 3):
                    scored.append((r, r.confidence * 0.50))
                for r in self._search_content(word, limit // 3):
                    scored.append((r, r.confidence * 0.45))

        # ── 5. 分类命中加分 ──
        # 查询正好等于分类名时，该分类规则统一加固定分
        if q_lower in self._category_names:
            scored = [
                (r, s + 0.05) if r.category == q_lower else (r, s)
                for r, s in scored
            ]

        return self._scored_merge(scored, category=category, limit=limit)

    def _search_content(self, query: str, limit: int = 10,
                        match_mode: str = "anywhere") -> List[Rule]:
        """在标题和/或内容中搜索关键词（不区分大小写），按置信度取 top-k。

        使用倒排索引加速：当查询中的关键词在倒排索引中存在时，只扫描候选规则；
        否则回退全量扫描。

        Args:
            query: 搜索关键词
            limit: 最大返回数
            match_mode: 'anywhere'=标题或内容, 'title_only'=仅标题,
                       'content_only'=仅内容
        """
        q = query.lower()
        matches = []

        # 快速路径：仅对内容搜索使用倒排索引（title_only 已有 search_exact/prefix 快速路径）
        if match_mode != "title_only":
            query_words = self._extract_content_words(q)
            candidate_ids: Optional[Set[str]] = None
            if query_words:
                for w in query_words:
                    ids = self._content_index.get(w)
                    if ids is None:
                        candidate_ids = None
                        break
                    if candidate_ids is None:
                        candidate_ids = set(ids)
                    else:
                        candidate_ids &= ids

            if candidate_ids is not None and len(candidate_ids) < len(self._rules) // 2:
                for rid in candidate_ids:
                    rule = self._rules.get(rid)
                    if rule is None:
                        continue
                    in_title = q in rule.title.lower()
                    in_content = q in rule.content.lower()
                    ok = self._match_ok(in_title, in_content, match_mode)
                    if ok:
                        self.total_search_count += 1
                        self._record_hit(rule.id)
                        cached = self._from_cache_or_store(rule.id)
                        matches.append(cached or rule)
                matches.sort(key=lambda r: r.confidence, reverse=True)
                return matches[:limit]

        # 全量扫描（回退路径：title_only 或倒排索引不适用时）
        for rule in self._rules.values():
            in_title = q in rule.title.lower()
            in_content = q in rule.content.lower()
            ok = self._match_ok(in_title, in_content, match_mode)
            if ok:
                self.total_search_count += 1
                self._record_hit(rule.id)
                cached = self._from_cache_or_store(rule.id)
                matches.append(cached or rule)

        matches.sort(key=lambda r: r.confidence, reverse=True)
        return matches[:limit]

    @staticmethod
    def _match_ok(in_title: bool, in_content: bool, match_mode: str) -> bool:
        """判断匹配模式是否通过。"""
        if match_mode == "title_only":
            return in_title
        elif match_mode == "content_only":
            return in_content and not in_title
        else:  # anywhere
            return in_title or in_content

    def _from_cache_or_store(self, rule_id: str) -> Optional[Rule]:
        """优先从热缓存返回，否则从全量规则返回。

        TODO: 支持 cache.max_size_mb 配置，实现精确字节数计数和驱逐。
        """
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

    def record_latency(self, latency_ms: float):
        """记录一次搜索延迟（用于平均延迟统计）。"""
        self._cumulative_latency_ms += latency_ms
        self._latency_sample_count += 1

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
            "avg_latency_ms": (
                round(self._cumulative_latency_ms / self._latency_sample_count, 2)
                if self._latency_sample_count > 0 else 0.0
            ),
            "tag_count": len(self.tag_index),
        }

    @property
    def is_ready(self) -> bool:
        """索引是否就绪（已有规则加载）。"""
        return len(self._rules) > 0
