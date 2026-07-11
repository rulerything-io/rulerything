"""
Rulerything — 搜索子系统

搜索职责拆分：
- analyzer:  查询解析（分词、归一化、CJK 处理、意图识别）
- ranker:    规则排名器（评分、覆盖度调整、可解释输出）
- filter:    规则过滤器（category、lang、status）
- models:    搜索相关数据模型

向后兼容：
    from search.analyzer import QueryAnalyzer   # 独立使用
    from search.ranker import RuleRanker         # 独立使用
    from search.filter import RuleFilter         # 独立使用
"""

from search.analyzer import QueryAnalyzer
from search.models import SearchResult, SearchResultItem, ScoreDetails
from search.ranker import RuleRanker, ScoredRule, ScoreBreakdown
from search.filter import RuleFilter

__all__ = [
    "QueryAnalyzer", "SearchResult", "SearchResultItem", "ScoreDetails",
    "RuleRanker", "ScoredRule", "ScoreBreakdown", "RuleFilter",
]
