"""
Rulerything — 搜索 / 预热路由
"""

import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, Depends
from fastapi.responses import JSONResponse

from core.state import state
from core.auth import require_write_token
from core.models import SearchRequest, SearchResult, SearchResponse
from rule import Rule

router = APIRouter()


@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    """搜索规则。"""
    start = time.perf_counter()

    results = state.index.search(
        query=req.query,
        search_type=req.search_type,
        category=None if req.category == "all" else req.category,
        lang=req.lang,
        limit=10,
    )

    latency_ms = (time.perf_counter() - start) * 1000
    state.index.record_latency(latency_ms)

    # v3.0 查询日志（含 result_ids 用于 dep_miner 共现分析）
    if state.storage_v2:
        try:
            state.storage_v2.log_query(
                req.query, latency_ms, len(results), latency_ms < 1.0,
                result_ids=[r.id for r in results],
            )
        except Exception:
            state.logger.warn("search", "v3 查询日志写入失败")
    state.logger.query(
        query=req.query,
        search_type=req.search_type,
        latency_ms=latency_ms,
        result_count=len(results),
        result_ids=[r.id for r in results],
        cache_hit=latency_ms < 1.0,
        user_feedback=req.user_feedback,
    )

    # 熵引擎记录查询（Phase 1）
    state.entropy_engine.record_query(
        query=req.query,
        result_ids=[r.id for r in results],
        latency_ms=latency_ms,
        cache_hit=latency_ms < 1.0,
    )

    # 收集反馈
    if req.user_feedback is not None and results:
        context = f"用户对搜索结果{'满意' if req.user_feedback else '不满意'}"
        state.evolution.collect_feedback(results[0].id, req.user_feedback, context)

    # AI 兜底
    ai_delegated = False
    ai_query_id = ""
    if not results and state.ai_bridge and state.ai_bridge.is_enabled():
        try:
            search_context = {
                "fallback": True,
                "search_type": req.search_type,
                "categories": [req.category] if req.category != "all" else [],
                "results": [],
            }
            ai_result = state.ai_bridge.enhance_query(req.query, search_context=search_context)
            source = ai_result.get("source")
            if source == "delegated":
                ai_delegated = True
                ai_query_id = ai_result.get("query_id", "")
            elif source == "ai" and ai_result.get("content"):
                ai_content = ai_result["content"]
                ai_title = ai_result.get("title", req.query)[:60]
                results.append(Rule(
                    id=f"_ai_{int(time.time())}",
                    title=ai_title,
                    content=ai_content[:500],
                    category="ai",
                    tags=["ai-generated"],
                    confidence=ai_result.get("confidence", 0.5),
                ))
        except Exception:
            state.logger.warn("search", "AI 兜底调用失败")

    return SearchResponse(
        results=[
            SearchResult(
                title=r.title, content=r.content, id=r.id,
                confidence=r.confidence, category=r.category, tags=r.tags,
                lang=r.lang,
            )
            for r in results[:5]
        ],
        confidence=results[0].confidence if results else 0.0,
        rule_id=results[0].id if results else "",
        latency_ms=round(latency_ms, 2),
        ai_delegated=ai_delegated,
        ai_query_id=ai_query_id,
    )


@router.post("/warmup")
async def warmup(category: Optional[str] = Query(None), auth=Depends(require_write_token)):
    """预热缓存。"""
    result = state.index.warmup(category=category)
    state.logger.info("cache", "预热完成", **result)
    return {"status": "ok", **result}
