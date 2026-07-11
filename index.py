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
    HOT_THRESHOLD = 10         # hit_count ≥ 此值进入热缓存
    COLD_DAYS = 30             # 超过此天数未访问进入冷区
    HOT_UPDATE_INTERVAL = 10   # 每 N 次命中触发缓存刷新
    MAX_HOT_SIZE = 500         # 热缓存最大条目数（可被 config 覆盖）
    MAX_CACHE_MB = 512         # 缓存最大估算字节数（MB）
    CACHE_TTL_SEC = 3600       # 缓存 TTL（秒，0=不限制）

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
        self._lock = threading.RLock()               # 支持幂等替换时的嵌套加锁
        self._hit_callback = None                    # 批量持久化命中
        self._estimated_cache_bytes: int = 0         # 热缓存字节估算
        self.total_search_count: int = 0
        self.cache_hit_count: int = 0
        self._eviction_count: int = 0                # 缓存驱逐次数
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
            # Repository 回调可能被重复触发；按 ID 替换而不是追加索引项。
            if rule.id in self._rules:
                self.remove(rule.id)
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
        now = datetime.now()
        for rule in self._rules.values():
            if (rule.hit_count >= self.HOT_THRESHOLD
                    and rule.id not in self.hot_ids):
                self.hot_ids.add(rule.id)
                self.hot_cache[rule.id] = rule

        # TTL 驱逐
        if self.CACHE_TTL_SEC > 0:
            stale_ids = []
            for rid, rule in self.hot_cache.items():
                if rule.last_hit and (now - rule.last_hit).total_seconds() > self.CACHE_TTL_SEC:
                    stale_ids.append(rid)
            for rid in stale_ids:
                self.hot_ids.discard(rid)
                del self.hot_cache[rid]
                self._eviction_count += 1

        # 驱逐：超过上限时移除最低命中率的条目
        if len(self.hot_cache) > self.MAX_HOT_SIZE:
            sorted_hot = sorted(self.hot_cache.items(), key=lambda x: x[1].hit_count)
            evict_count = len(self.hot_cache) - self.MAX_HOT_SIZE
            for rule_id, _ in sorted_hot[:evict_count]:
                self.hot_ids.discard(rule_id)
                del self.hot_cache[rule_id]
                self._eviction_count += 1

    # ── 搜索方法 ───────────────────────────────────────

    def _record_results(self, results: List[Rule]):
        """Record one user-visible query and its unique returned rules."""
        self.total_search_count += 1
        unique = {rule.id: rule for rule in results}
        if any(rule_id in self.hot_cache for rule_id in unique):
            self.cache_hit_count += 1
        for rule_id in unique:
            self._record_hit(rule_id)
        if unique and self._hit_callback:
            self._hit_callback(list(unique))

    def set_hit_callback(self, callback):
        """Set a batch callback used to persist one hit per returned rule."""
        self._hit_callback = callback

    def search_exact(self, title: str, *, record: bool = True) -> Optional[Rule]:
        """精确标题搜索 — O(log N) 二分查找。"""
        pos = bisect.bisect_left(self.sorted_titles, title)
        if pos < len(self.sorted_titles) and self.sorted_titles[pos] == title:
            rule_id = self.title_to_id[title]
            rule = self._from_cache_or_store(rule_id)
            if record:
                self._record_results([rule] if rule else [])
            return rule
        if record:
            self._record_results([])
        return None

    def search_prefix(self, prefix: str, limit: int = 10, *, record: bool = True) -> List[Rule]:
        """前缀搜索 — 类比 Everything 的 'win*' 通配符。"""
        start = bisect.bisect_left(self.sorted_titles, prefix)
        results = []
        for title in self.sorted_titles[start:]:
            if not title.startswith(prefix):
                break
            rule_id = self.title_to_id[title]
            rule = self._from_cache_or_store(rule_id)
            if rule:
                results.append(rule)
                if len(results) >= limit:
                    break
        if record:
            self._record_results(results)
        return results

    def search_by_tag(self, tag: str, limit: int = 20, *, record: bool = True) -> List[Rule]:
        """标签搜索 — O(1) 哈希表，按置信度排序取 top-k。"""
        rule_ids = self.tag_index.get(tag, [])
        # 先获取所有规则，按置信度排序后截断
        candidates = []
        for rule_id in rule_ids:
            rule = self._from_cache_or_store(rule_id)
            if rule:
                candidates.append(rule)
        candidates.sort(key=lambda r: r.confidence, reverse=True)
        results = candidates[:limit]
        if record:
            self._record_results(results)
        return results

    # ── CJK 辅助 ────────────────────────────────────────

    def _scored_merge(self, scored: List[Tuple[Rule, float]],
                      category: Optional[str] = None,
                      lang: Optional[str] = None,
                      limit: int = 10) -> List[Rule]:
        """合并去重 → 分类/语言过滤 → 按分数排序 → top-k。"""
        best: Dict[str, Tuple[Rule, float]] = {}
        for rule, score in scored:
            if category and rule.category != category:
                continue
            if lang and rule.lang != lang:
                continue
            rid = rule.id
            if rid not in best or score > best[rid][1]:
                best[rid] = (rule, score)
        sorted_ = sorted(best.values(), key=lambda x: (x[1], x[0].confidence), reverse=True)
        return [r for r, _ in sorted_[:limit]]

    @staticmethod
    def _query_terms(query: str) -> Set[str]:
        """Return useful English query terms for coverage-based ranking."""
        stopwords = {
            "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
            "how", "in", "into", "is", "it", "of", "on", "or", "the", "to",
            "use", "using", "with", "without", "best", "practice", "practices",
            "rule", "rules", "guide", "guideline", "guidelines",
        }
        return {
            word.lower()
            for word in extract_english_words(query)
            if len(word) >= 3 and word.lower() not in stopwords
        }

    @staticmethod
    def _term_present(term: str, haystack: str) -> bool:
        """Match exact terms and simple plural/suffix variants."""
        if term in haystack:
            return True
        if term.endswith("y") and f"{term[:-1]}ies" in haystack:
            return True
        if f"{term}s" in haystack or f"{term}es" in haystack:
            return True
        return False

    def _apply_query_coverage(self, scored: List[Tuple[Rule, float]],
                              query: str) -> List[Tuple[Rule, float]]:
        """Demote results that only match one generic term from a multi-term query."""
        terms = self._query_terms(query)
        if len(terms) < 2:
            return scored

        adjusted: List[Tuple[Rule, float]] = []
        for rule, score in scored:
            tag_scope = {str(t).lower() for t in rule.tags}
            scope_hit = rule.category.lower() in terms or bool(tag_scope & terms)
            haystack = " ".join([
                rule.title,
                rule.content,
                rule.category,
                " ".join(map(str, rule.tags)),
            ]).lower()
            title_haystack = rule.title.lower()
            matched = sum(1 for term in terms if self._term_present(term, haystack))
            title_matched = sum(1 for term in terms if self._term_present(term, title_haystack))

            if matched == 0:
                multiplier = 0.20
            elif matched < min(2, len(terms)) and not scope_hit:
                multiplier = 0.05
            else:
                coverage = matched / len(terms)
                multiplier = 0.35 + 0.65 * coverage

            # Category/tag hits are often the strongest signal of technical scope.
            if scope_hit:
                multiplier += 0.10
            elif title_matched == 0:
                # Long reference pages can mention every query term in passing.
                # Prefer rules whose title or tags describe the requested topic.
                multiplier *= 0.55

            adjusted.append((rule, score * min(multiplier, 1.15)))

        return adjusted

    def search(self, query: str, search_type: str = "exact",
               category: Optional[str] = None, limit: int = 10,
               lang: Optional[str] = None,
               explain: bool = False) -> List[Rule]:
        """Strict search contract: exact, prefix, tag, or explicit smart."""
        query = query.strip()
        if not query or limit <= 0:
            return []
        if search_type == "smart":
            return self.smart_search(query, category=category, limit=limit,
                                     lang=lang, explain=explain)
        if search_type == "exact":
            rule = self.search_exact(query, record=False)
            results = [rule] if rule else []
        elif search_type == "prefix":
            results = self.search_prefix(query, limit=limit * 2, record=False)
            results.sort(key=lambda rule: rule.confidence, reverse=True)
        elif search_type == "tag":
            results = self.search_by_tag(query, limit=limit * 2, record=False)
        else:
            raise ValueError(f"unsupported search type: {search_type}")
        results = [
            rule for rule in results
            if (not category or rule.category == category)
            and (not lang or rule.lang == lang)
        ][:limit]
        self._record_results(results)
        return results

    def smart_search(self, query: str, category: Optional[str] = None,
                     limit: int = 10, lang: Optional[str] = None,
                     explain: bool = False) -> List[Rule]:
        """Hybrid relevance search using all retrieval strategies.

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
            category: 分类过滤（可选）
            limit: 最大返回数量
            lang: 语言过滤（可选）: zh | en | ja | ...
            explain: 是否返回评分细节（附加在 Rule 对象的 _score_details 等属性上）
        """
        query = query.strip()
        if not query or limit <= 0:
            return []
        scored: List[Tuple[Rule, float]] = []
        q_lower = query.lower()
        has_cjk_flag = has_cjk(query)

        # ── 1. 主策略 ──
        exact = self.search_exact(query, record=False)
        _primary_had_results = exact is not None
        if exact:
            scored.append((exact, exact.confidence))

        # ── 2. 标签搜索（query 整体作为标签） ──
        # 主策略已有结果时降权标签匹配，避免高频低质标签淹没精准前缀匹配
        _tag_weight = 0.75 if _primary_had_results else 0.90
        for r in self.search_by_tag(q_lower, limit * 2, record=False):
            scored.append((r, r.confidence * _tag_weight))

        # ── 3. 内容包含搜索（全查询） ──
        # 先收集标题匹配的规则 ID，避免内容匹配重复添加
        _title_matched_ids = set()
        for r in self._search_content(query, limit, match_mode="title_only", record=False):
            scored.append((r, r.confidence * 0.80))
            _title_matched_ids.add(r.id)
        for r in self._search_content(query, limit, match_mode="content_only", record=False):
            if r.id not in _title_matched_ids:
                scored.append((r, r.confidence * 0.50))

        # ── 4. 拆词搜索 ──
        term_limit = max(3, limit // 2)
        if has_cjk_flag:
            for gram in extract_cjk_ngrams(query, min_len=2):
                for r in self._search_content(gram, term_limit, record=False):
                    scored.append((r, r.confidence * 0.55))
            for word in extract_english_words(query):
                for r in self.search_prefix(word, term_limit, record=False):
                    scored.append((r, r.confidence * 0.55))
                for r in self.search_by_tag(word, term_limit, record=False):
                    scored.append((r, r.confidence * 0.50))
                for r in self._search_content(word, term_limit, record=False):
                    scored.append((r, r.confidence * 0.45))
        else:
            for word in q_lower.split():
                if len(word) < 2:
                    continue
                for r in self.search_prefix(word, term_limit, record=False):
                    scored.append((r, r.confidence * 0.55))
                for r in self.search_by_tag(word, term_limit, record=False):
                    scored.append((r, r.confidence * 0.50))
                for r in self._search_content(word, term_limit, record=False):
                    scored.append((r, r.confidence * 0.45))

        # ── 5. 分类命中加分 ──
        if q_lower in self._category_names:
            scored = [
                (r, s + 0.05) if r.category == q_lower else (r, s)
                for r, s in scored
            ]

        scored = self._apply_query_coverage(scored, query)
        results = self._scored_merge(scored, category=category, lang=lang, limit=limit)

        # ── 6. explain: 附加评分细节 ──
        if explain:
            from search.ranker import RuleRanker, ScoreBreakdown
            ranker = RuleRanker()
            for rule in results:
                # 计算评分解
                breakdown = ScoreBreakdown()
                breakdown.title = rule.confidence * 0.80  # 简化估算
                breakdown.tag = rule.confidence * 0.20 if rule.tags else 0.0
                breakdown.content = rule.confidence * 0.50
                # 查询覆盖度
                coverage = ranker.apply_coverage(
                    [], query  # 仅用于计算
                )

                # 提取匹配词条
                terms = RuleRanker.query_terms(query)
                haystack = " ".join([
                    rule.title, rule.content, rule.category,
                    " ".join(map(str, rule.tags)),
                ]).lower()
                matched = [t for t in terms if RuleRanker.term_present(t, haystack)]

                # 附加到 Rule 对象（消费者通过属性读取）
                rule._score_details = breakdown.to_dict()
                rule._matched_terms = matched
                rule._score = sum(breakdown.to_dict().values())
                rule._reason = f"matched {len(matched)} term(s)" if matched else "broad match"

        self._record_results(results)
        return results

    def _search_content(self, query: str, limit: int = 10,
                        match_mode: str = "anywhere", *, record: bool = True) -> List[Rule]:
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
                        cached = self._from_cache_or_store(rule.id)
                        matches.append(cached or rule)
                matches.sort(key=lambda r: r.confidence, reverse=True)
                results = matches[:limit]
                if record:
                    self._record_results(results)
                return results

        # 全量扫描（回退路径：title_only 或倒排索引不适用时）
        for rule in self._rules.values():
            in_title = q in rule.title.lower()
            in_content = q in rule.content.lower()
            ok = self._match_ok(in_title, in_content, match_mode)
            if ok:
                cached = self._from_cache_or_store(rule.id)
                matches.append(cached or rule)

        matches.sort(key=lambda r: r.confidence, reverse=True)
        results = matches[:limit]
        if record:
            self._record_results(results)
        return results

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

        热缓存条目超过 TTL 时自动降级。
        """
        if rule_id in self.hot_cache:
            rule = self.hot_cache[rule_id]
            if self.CACHE_TTL_SEC > 0 and rule.last_hit:
                age = (datetime.now() - rule.last_hit).total_seconds()
                if age > self.CACHE_TTL_SEC:
                    # 过期，从热缓存驱逐
                    self.hot_ids.discard(rule_id)
                    del self.hot_cache[rule_id]
                    self._eviction_count += 1
                    return self._rules.get(rule_id)
            return rule
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
            "cache_eviction_count": self._eviction_count,
            "cache_max_size": self.MAX_HOT_SIZE,
            "cache_ttl_sec": self.CACHE_TTL_SEC,
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
