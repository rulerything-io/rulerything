"""
Rulerything — 规则排名器

职责：统一计算最终评分，输出可解释的评分细节。

从 index.py 提取，保持行为一致。
"""

from typing import Dict, List, Optional, Set, Tuple

from search.analyzer import QueryAnalyzer, extract_english_words


# ── 评分权重（可配置） ────────────────────────────────────

DEFAULT_WEIGHTS = {
    "title_exact": 1.00,
    "title_prefix": 0.85,
    "tag_exact": 0.90,
    "title_match": 0.80,
    "content_match": 0.50,
    "term_prefix": 0.55,
    "term_tag": 0.50,
    "term_content": 0.45,
    "category_bonus": 0.05,
}


class ScoreBreakdown:
    """评分分解。"""

    def __init__(self):
        self.title: float = 0.0
        self.tag: float = 0.0
        self.content: float = 0.0
        self.query_coverage: float = 0.0
        self.category_bonus: float = 0.0

    @property
    def total(self) -> float:
        return round(self.title + self.tag + self.content +
                     self.query_coverage + self.category_bonus, 4)

    def to_dict(self) -> dict:
        return {
            "title": round(self.title, 4),
            "tag": round(self.tag, 4),
            "content": round(self.content, 4),
            "query_coverage": round(self.query_coverage, 4),
            "category_bonus": round(self.category_bonus, 4),
        }


class ScoredRule:
    """带评分的规则。"""

    def __init__(self, rule, score: float,
                 breakdown: Optional[ScoreBreakdown] = None,
                 matched_terms: Optional[List[str]] = None,
                 reason: str = ""):
        self.rule = rule
        self.score = score
        self.breakdown = breakdown or ScoreBreakdown()
        self.matched_terms = matched_terms or []
        self.reason = reason


class RuleRanker:
    """规则排名器 — 统一评分与排序。"""

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}

    # ── 查询词条提取 ────────────────────────────────────

    @staticmethod
    def query_terms(query: str) -> Set[str]:
        """提取查询中的有效搜索词条。"""
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
    def term_present(term: str, haystack: str) -> bool:
        """匹配词条（含简单形态变化）。"""
        if term in haystack:
            return True
        if term.endswith("y") and f"{term[:-1]}ies" in haystack:
            return True
        if f"{term}s" in haystack or f"{term}es" in haystack:
            return True
        return False

    # ── 覆盖率评分 ──────────────────────────────────────

    def apply_coverage(self, scored_rules: List[ScoredRule],
                       query: str) -> List[ScoredRule]:
        """基于查询词条覆盖率调整评分。"""
        terms = self.query_terms(query)
        if len(terms) < 2:
            return scored_rules

        for sr in scored_rules:
            rule = sr.rule
            tag_scope = {str(t).lower() for t in rule.tags}
            scope_hit = rule.category.lower() in terms or bool(tag_scope & terms)

            haystack = " ".join([
                rule.title, rule.content, rule.category,
                " ".join(map(str, rule.tags)),
            ]).lower()
            title_haystack = rule.title.lower()

            matched = sum(1 for term in terms if self.term_present(term, haystack))
            title_matched = sum(1 for term in terms if self.term_present(term, title_haystack))

            if matched == 0:
                multiplier = 0.20
            elif matched < min(2, len(terms)) and not scope_hit:
                multiplier = 0.05
            else:
                coverage = matched / len(terms)
                multiplier = 0.35 + 0.65 * coverage

            if scope_hit:
                multiplier += 0.10
            elif title_matched == 0:
                multiplier *= 0.55

            sr.breakdown.query_coverage = round(multiplier - 1.0, 4) if multiplier > 1.0 else 0.0
            sr.score = round(sr.score * min(multiplier, 1.15), 4)

            # matched_terms
            matched_list = [t for t in terms if self.term_present(t, haystack)]
            sr.matched_terms = matched_list

        return scored_rules

    # ── 合并去重排序 ────────────────────────────────────

    def merge_and_sort(self, scored_rules: List[ScoredRule],
                       category: Optional[str] = None,
                       lang: Optional[str] = None,
                       limit: int = 10) -> List[ScoredRule]:
        """合并去重 → 分类/语言过滤 → 按分数排序 → top-k。"""
        best: Dict[str, ScoredRule] = {}
        for sr in scored_rules:
            if category and sr.rule.category != category:
                continue
            if lang and sr.rule.lang != lang:
                continue
            rid = sr.rule.id
            if rid not in best or sr.score > best[rid].score:
                best[rid] = sr

        sorted_sr = sorted(best.values(), key=lambda x: (x.score, x.rule.confidence), reverse=True)
        return sorted_sr[:limit]

    def to_explain_dict(self, scored_rule: ScoredRule) -> dict:
        """输出可解释评分结构。"""
        return {
            "rule_id": scored_rule.rule.id,
            "title": scored_rule.rule.title,
            "category": scored_rule.rule.category,
            "score": round(scored_rule.score, 4),
            "score_details": scored_rule.breakdown.to_dict(),
            "matched_terms": scored_rule.matched_terms,
            "reason": scored_rule.reason,
        }
