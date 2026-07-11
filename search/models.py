"""
Rulerything — 搜索数据模型
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ScoreDetails:
    """评分细节。"""
    title: float = 0.0
    tag: float = 0.0
    content: float = 0.0
    query_coverage: float = 0.0

    def to_dict(self) -> dict:
        return {
            "title": round(self.title, 4),
            "tag": round(self.tag, 4),
            "content": round(self.content, 4),
            "query_coverage": round(self.query_coverage, 4),
        }


@dataclass
class SearchResultItem:
    """单条搜索结果（带可解释信息）。"""
    rule_id: str
    title: str
    category: str
    score: float
    confidence: float
    score_details: Optional[ScoreDetails] = None
    matched_terms: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "category": self.category,
            "score": round(self.score, 4),
            "confidence": self.confidence,
            "score_details": self.score_details.to_dict() if self.score_details else None,
            "matched_terms": self.matched_terms,
            "reason": self.reason,
        }


@dataclass
class SearchResult:
    """搜索结果集合。"""
    query: str
    total: int
    results: List[SearchResultItem]
    elapsed_ms: float = 0.0

    def to_dict(self, include_results: bool = True) -> dict:
        d = {
            "query": self.query,
            "total": self.total,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }
        if include_results:
            d["results"] = [r.to_dict() for r in self.results]
        return d
