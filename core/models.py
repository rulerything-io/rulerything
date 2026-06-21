"""
Rulerything — Pydantic 请求/响应模型
"""

from typing import Dict, Optional, List, Any, Literal
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    search_type: Literal["exact", "prefix", "tag", "smart"] = "exact"
    category: str = Field(default="all", pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    lang: Optional[str] = Field(default=None, pattern=r"^[a-z]{2,8}(-[A-Za-z0-9]{2,8})?$")
    limit: int = Field(default=10, ge=1, le=100)
    user_feedback: Optional[bool] = None
    profile: Optional[str] = None        # v4.0 价值画像名称
    brief: bool = False                  # v4.0 简短决策追溯
    selected_rule_id: Optional[str] = None  # v4.0 用户采纳的规则 ID
    session_id: Optional[str] = None     # v4.0 session 标识（灰度分配/A/B 测试）


class SearchResult(BaseModel):
    title: str
    content: str
    id: str
    confidence: float
    category: str
    tags: list
    lang: str = "zh"
    # v4.0 价值层字段（仅在 value.enabled=true 时返回）
    value_vector: Optional[Dict[str, float]] = None
    value_confidence: Optional[float] = None
    value_source: Optional[str] = None
    value_provenance: Optional[str] = None


class SearchResponse(BaseModel):
    results: list[SearchResult]
    confidence: float
    rule_id: str
    latency_ms: float
    ai_delegated: bool = False
    ai_query_id: str = ""
    # v4.0 决策追溯链
    decision_trace: Optional[Dict[str, Any]] = None


class AddRuleRequest(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)?$")
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=100_000)
    category: str = Field(default="general", pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    tags: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    verifier: Literal["manual", "auto", "crowd"] = "manual"


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
    limit: int = Field(default=10, ge=1, le=100)
