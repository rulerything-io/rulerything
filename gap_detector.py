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
GapDetector — 知识缺口检测引擎（v3.0 Phase B）

自动识别用户常问但系统没有规则覆盖的领域。

用法:
    detector = GapDetector(storage, config)
    gaps = detector.detect_gaps()
"""

import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


class GapDetector:
    """知识缺口检测引擎。

    流程:
      1. 获取近期高频查询
      2. TF-IDF 向量化并与规则标题计算余弦相似度
      3. 相似度 < 动态阈值 → 缺口候选
      4. 聚合相似缺口 → 按 frequency × novelty 排序
      5. 返回 top-N
    """

    def __init__(self, storage, config: dict):
        self.storage = storage
        self.config = config

        # 配置
        self.min_frequency = config.get("min_frequency", 5)
        self.max_rules_per_week = config.get("max_rules_per_week", 15)
        self.similarity_threshold = config.get("similarity_threshold", 0.3)
        self.schedule_interval_hours = config.get("schedule_interval_hours", 168)
        # CJK 新颖度偏置（可配置，0=禁用，默认 0.7）
        # CJK 查询通常更具体，更可能代表未覆盖领域
        self.novelty_cjk_bias = config.get("novelty_cjk_bias", 0.7)

        # 运行时状态
        self._last_run: Optional[datetime] = None
        self._history_pass_rates: List[float] = []
        self.stats = {
            "total_queries_analyzed": 0,
            "gap_candidates": 0,
            "gaps_found": 0,
            "last_gaps": [],
            "last_run_time": None,
        }

    # ── 主入口 ──────────────────────────────────────────

    def detect_gaps(self) -> List[dict]:
        """运行缺口检测。

        Returns:
            按 severity 排序的缺口列表
        """
        # 1. 获取近期查询
        queries = self.storage.get_recent_queries(days=7, min_freq=self.min_frequency)
        if not queries:
            return []

        self.stats["total_queries_analyzed"] = len(queries)

        # 2. 获取规则标题
        rules = self.storage.list()
        rule_titles = [r.title for r in rules]

        # 3. 计算每个查询与规则标题的相似度
        gap_candidates = []
        for q in queries:
            query_text = q.get("query", "")
            freq = q.get("frequency", 1)
            if not query_text.strip():
                continue

            max_sim = self._max_similarity(query_text, rule_titles)
            if max_sim < self._get_dynamic_threshold():
                gap_candidates.append({
                    "query": query_text,
                    "frequency": freq,
                    "max_similarity": round(max_sim, 4),
                    "novelty": self._compute_novelty(query_text),
                })

        if not gap_candidates:
            return []

        self.stats["gap_candidates"] = len(gap_candidates)

        # 4. 聚合相似缺口
        aggregated = self._aggregate_gaps(gap_candidates)

        # 5. 排序并截断
        aggregated.sort(
            key=lambda g: g["frequency"] * g["novelty"],
            reverse=True,
        )
        top_gaps = aggregated[:self.max_rules_per_week]

        # 6. 记录 pass rate 用于动态调阈值
        # 使用聚合后但未截断的 gap 数作为分子，避免 max_rules_per_week 人为压低通过率
        pass_rate = 1.0 - (len(aggregated) / max(len(gap_candidates), 1))
        self._history_pass_rates.append(pass_rate)
        if len(self._history_pass_rates) > 12:
            self._history_pass_rates.pop(0)

        # 更新统计
        self.stats["gaps_found"] = len(top_gaps)
        self.stats["last_gaps"] = [g["query"] for g in top_gaps[:10]]
        self.stats["last_run_time"] = datetime.now().isoformat()
        self._last_run = datetime.now()

        return top_gaps

    # ── 相似度计算 ─────────────────────────────────────

    def _compute_tfidf(self, texts: List[str]) -> Tuple[List[Dict[str, float]], List[str]]:
        """计算文本列表的 TF-IDF 向量。

        Returns:
            (vectors, vocabulary) — vectors 是每个文本的 {term: weight} 字典列表
        """
        if not texts:
            return [], []

        if HAS_SKLEARN:
            try:
                vectorizer = TfidfVectorizer(
                    analyzer="word",
                    token_pattern=r"(?u)\b\w+\b",
                    max_features=5000,
                )
                matrix = vectorizer.fit_transform(texts)
                vocab = vectorizer.get_feature_names_out()
                vectors = []
                for i in range(matrix.shape[0]):
                    row = matrix[i]
                    vec = {}
                    if hasattr(row, "tocoo"):
                        coo = row.tocoo()
                        for idx, val in zip(coo.col, coo.data):
                            vec[vocab[idx]] = val
                    vectors.append(vec)
                return vectors, list(vocab)
            except Exception:
                pass  # 降级到纯 Python

        # 纯 Python TF-IDF
        return self._pure_python_tfidf(texts)

    def _pure_python_tfidf(self, texts: List[str]) -> Tuple[List[Dict[str, float]], List[str]]:
        """纯 Python TF-IDF 实现（无外部依赖）。"""
        # 分词
        tokenized = []
        for text in texts:
            tokens = self._tokenize(text)
            tokenized.append(tokens)

        # 词频
        tfs = []
        for tokens in tokenized:
            counter = Counter(tokens)
            total = len(tokens) if tokens else 1
            tfs.append({w: c / total for w, c in counter.items()})

        # 文档频率
        df = Counter()
        for tokens in tokenized:
            for w in set(tokens):
                df[w] += 1

        # IDF
        n = len(texts)
        idf = {w: math.log(n / (1 + freq)) + 1 for w, freq in df.items()}

        # TF-IDF
        vectors = []
        for tf in tfs:
            vec = {w: tf[w] * idf.get(w, 1) for w in tf}
            vectors.append(vec)

        all_terms = sorted(idf.keys())
        return vectors, all_terms

    def _tokenize(self, text: str) -> List[str]:
        """分词：字母词 + CJK 二元组。"""
        tokens = []
        text_lower = text.lower()
        # 英文/数字词
        for word in re.findall(r'[a-z_+#.0-9]+', text_lower):
            if len(word) >= 2:
                tokens.append(word)
        # CJK 二元组
        cjk_seq = re.findall(r'[\u4e00-\u9fff]+', text)
        for seq in cjk_seq:
            for i in range(len(seq) - 1):
                tokens.append(seq[i:i + 2])
        return tokens

    def _cosine_similarity(self, vec_a: Dict[str, float],
                           vec_b: Dict[str, float]) -> float:
        """余弦相似度。"""
        intersection = set(vec_a) & set(vec_b)
        if not intersection:
            return 0.0
        dot = sum(vec_a[k] * vec_b[k] for k in intersection)
        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _max_similarity(self, query: str, titles: List[str]) -> float:
        """计算查询与所有规则标题的最大余弦相似度。"""
        if not titles or not query.strip():
            return 0.0

        query_vecs, _ = self._compute_tfidf([query])
        if not query_vecs:
            return 0.0
        query_vec = query_vecs[0]

        if HAS_SKLEARN:
            # sklearn 版本：一次向量化所有标题
            try:
                all_texts = [query] + titles
                all_vecs, _ = self._compute_tfidf(all_texts)
                if len(all_vecs) < 2:
                    return 0.0
                qv = all_vecs[0]
                max_sim = 0.0
                for tv in all_vecs[1:]:
                    sim = self._cosine_similarity(qv, tv)
                    if sim > max_sim:
                        max_sim = sim
                return max_sim
            except Exception:
                pass

        # 纯 Python：一次性计算所有标题的 TF-IDF（正确计算 IDF）
        max_sim = 0.0
        sample_titles = titles[:200]  # 限制计算量
        all_texts = [query] + sample_titles
        all_vecs, _ = self._compute_tfidf(all_texts)
        if len(all_vecs) >= 2:
            qv = all_vecs[0]
            for tv in all_vecs[1:]:
                sim = self._cosine_similarity(qv, tv)
                if sim > max_sim:
                    max_sim = sim
        return max_sim

    # ── 缺口处理 ───────────────────────────────────────

    def _compute_novelty(self, query: str) -> float:
        """计算查询的新颖度（与已有规则主题的差异程度）。

        基于查询中包含的非规则关键词比例。
        """
        tokens = self._tokenize(query)
        if not tokens:
            return 0.5

        # 启发式：含 CJK 的长查询更可能表示新主题（偏置系数可配置）
        cjk_ratio = sum(1 for t in tokens if re.match(r'^[\u4e00-\u9fff]{2}', t)) / len(tokens)
        return min(1.0, 0.3 + cjk_ratio * self.novelty_cjk_bias)

    def _aggregate_gaps(self, candidates: List[dict]) -> List[dict]:
        """聚合相似缺口：cosine > 0.8 的合并为一组。"""
        if not candidates:
            return []

        # 计算候选缺口间的相似度
        texts = [c["query"] for c in candidates]
        vecs, _ = self._compute_tfidf(texts)

        if not vecs:
            return candidates

        # 简单贪心聚类
        groups = []
        assigned = set()

        for i, (cand, vec) in enumerate(zip(candidates, vecs)):
            if i in assigned:
                continue
            group = [cand]
            assigned.add(i)

            for j, (other_cand, other_vec) in enumerate(zip(candidates, vecs)):
                if j in assigned:
                    continue
                sim = self._cosine_similarity(vec, other_vec)
                if sim > 0.8:
                    group.append(other_cand)
                    assigned.add(j)

            # 合并组
            merged = {
                "query": group[0]["query"],
                "frequency": sum(g["frequency"] for g in group),
                "max_similarity": min(g["max_similarity"] for g in group),
                "novelty": max(g["novelty"] for g in group),
                "group_size": len(group),
                "related_queries": [g["query"] for g in group[1:]],
            }
            groups.append(merged)

        return groups

    # ── 动态阈值 ───────────────────────────────────────

    def _get_dynamic_threshold(self) -> float:
        """获取动态相似度阈值。

        根据上周通过率自动调整：
          - 通过率 < 30% → 更严格（阈值 +15%）
          - 通过率 > 70% → 更宽松（阈值 -10%）
        """
        if not self._history_pass_rates:
            return self.similarity_threshold

        last_rate = self._history_pass_rates[-1]
        threshold = self.similarity_threshold

        if last_rate < 0.3:
            threshold *= 1.15
        elif last_rate > 0.7:
            threshold *= 0.9

        return max(0.15, min(0.5, threshold))

    # ── 查询方法 ───────────────────────────────────────

    def get_stats(self) -> dict:
        """缺口检测统计。"""
        return {
            **self.stats,
            "dynamic_threshold": round(self._get_dynamic_threshold(), 4),
            "history_pass_rates": [round(r, 3) for r in self._history_pass_rates],
            "config": {
                "min_frequency": self.min_frequency,
                "max_rules_per_week": self.max_rules_per_week,
                "base_threshold": self.similarity_threshold,
                "has_sklearn": HAS_SKLEARN,
            },
        }

    def get_coverage_stats(self) -> dict:
        """覆盖度统计。"""
        rules = self.storage.list()
        if not rules:
            return {"covered_categories": 0, "total_rules": 0}

        # 按分类统计
        cat_stats = defaultdict(lambda: {"count": 0, "avg_confidence": 0.0})
        for r in rules:
            cat_stats[r.category]["count"] += 1
            cat_stats[r.category]["avg_confidence"] += r.confidence

        for cat in cat_stats:
            c = cat_stats[cat]
            c["avg_confidence"] = round(c["avg_confidence"] / c["count"], 3)

        return {
            "total_rules": len(rules),
            "covered_categories": len(cat_stats),
            "categories": dict(cat_stats),
            "gaps_found": self.stats.get("gaps_found", 0),
        }
