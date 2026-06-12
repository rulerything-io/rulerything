"""
Rulerything — Pydantic 请求/响应模型
"""

from typing import Optional, List
from pydantic import BaseModel


class SearchRequest(BaseModel):
    query: str
    search_type: str = "exact"
    category: str = "all"
    lang: Optional[str] = None  # 语言过滤: zh | en | ja | ...
    user_feedback: Optional[bool] = None


class SearchResult(BaseModel):
    title: str
    content: str
    id: str
    confidence: float
    category: str
    tags: list
    lang: str = "zh"


class SearchResponse(BaseModel):
    results: list[SearchResult]
    confidence: float
    rule_id: str
    latency_ms: float
    ai_delegated: bool = False
    ai_query_id: str = ""


class AddRuleRequest(BaseModel):
    id: str
    title: str
    content: str
    category: str = "general"
    tags: list = []
    confidence: float = 0.5
    verifier: str = "manual"


class RollbackRequest(BaseModel):
    rule_id: str
    target_version: int


class RuleListItem(BaseModel):
    id: str
    title: str
    content: str
    category: str
    tags: list
    confidence: float
    version: int
    hit_count: int
    created_at: Optional[str] = None


class AIConfigRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None
    enabled: Optional[bool] = None
    use_parent_ai: Optional[bool] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    daily_limit_usd: Optional[float] = None
    per_call_limit_usd: Optional[float] = None
    confidence_threshold: Optional[float] = None
    cache_ttl_hours: Optional[float] = None
    cache_max_entries: Optional[int] = None
    max_new_rules_per_session: Optional[int] = None
    max_new_rules_per_day: Optional[int] = None
    ingest_model: Optional[str] = None


class AIRespondRequest(BaseModel):
    query_id: str
    response: str
    error_message: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None


class ImmuneScanRequest(BaseModel):
    auto_cleanup: bool = False


class ImmuneClearRequest(BaseModel):
    rule_ids: Optional[list] = None
    all_nk_targets: bool = False


class AckRequest(BaseModel):
    type: str


class QueryRequest(BaseModel):
    query_text: str
    sort_by: str = "title"
    category: Optional[str] = None
    use_semantic: bool = False
    limit: int = 10
