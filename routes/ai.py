"""
Rulerything — AI Bridge / 自动提炼路由
"""

import hashlib
import logging
import time
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Query, Depends
from fastapi.responses import JSONResponse

from core.state import state
from core.auth import require_write_token
from core.models import AIConfigRequest, AIRespondRequest

router = APIRouter()


# ── AI 增强查询 ───────────────────────────────────────


@router.get("/ai/query")
async def ai_query(query: str = Query(..., min_length=1)):
    """AI 增强查询。"""
    if not state.ai_bridge or not state.ai_bridge.is_enabled():
        return {"source": "system", "content": "", "error": "AI Bridge 未启用"}
    try:
        result = state.ai_bridge.enhance_query(query)
        if state.auto_ingest and result.get("source") in ("ai", "cache"):
            validation = result.get("validation", {}) or {}
            try:
                v_result = validation.get("result", "unverifiable")
                state.auto_ingest.enqueue(query, result["content"], v_result)
            except Exception:
                logging.warning("ai_query: auto_ingest.enqueue 失败")
        return result
    except Exception as e:
        return {"source": "system", "error": str(e)}


@router.get("/ai/budget")
async def ai_budget_status():
    """AI 预算状态。"""
    if not state.ai_bridge:
        return {"enabled": False}
    return state.ai_bridge.get_budget_status()


@router.get("/ai/stats")
async def ai_stats():
    """AI 模块统计。"""
    result = {}
    if state.ai_bridge:
        result["ai_bridge"] = state.ai_bridge.get_stats()
    if state.auto_ingest:
        result["auto_ingest"] = state.auto_ingest.get_stats()
    if state.storage_v2:
        result["storage"] = state.storage_v2.get_ai_stats()
    return result


@router.post("/ai/feedback")
async def ai_feedback(rule_id: str = Query(...), positive: bool = Query(...), auth=Depends(require_write_token)):
    """提交用户对 AI 生成规则的反馈。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    state.storage_v2.record_ai_feedback(rule_id, positive)
    if state.auto_ingest:
        state.auto_ingest.confidence_adjuster.record_feedback(rule_id, positive)
    return {"status": "ok"}


@router.post("/ai/clear-cache")
async def ai_clear_cache(auth=Depends(require_write_token)):
    """清除 AI 缓存。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    try:
        state.storage_v2.ai_cache_cleanup(max_entries=0)
        return {"status": "ok", "cleared": True}
    except Exception as e:
        return {"error": str(e)}


@router.get("/ai/config")
async def ai_get_config():
    """获取当前 AI 配置（API key 脱敏显示）。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    base = dict(state.config.get("v3", {}).get("ai_bridge", {}))
    runtime = state.load_ai_config()
    merged = {**base, **runtime}
    if merged.get("api_key"):
        merged["api_key_preview"] = merged["api_key"][:6] + "****"
        merged["api_key"] = ""
    return merged


@router.post("/ai/config")
async def ai_set_config(req: AIConfigRequest, auth=Depends(require_write_token)):
    """更新 AI 配置并热加载（无需重启）。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    current = state.load_ai_config()
    updates = {k: v for k, v in req.dict(exclude_none=True).items()}
    merged = {**current, **updates}
    state.save_ai_config(merged)
    state.reinitialize_ai_modules()
    return {"status": "ok", "message": "AI 配置已更新并生效"}


@router.get("/ai/pending")
async def ai_pending(limit: int = Query(20, ge=1, le=100)):
    """获取待处理的 AI 委托查询。"""
    if not state.storage_v2:
        return {"queries": [], "error": "SQLite 存储未启用"}
    queries = state.storage_v2.get_pending_queries(status="pending", limit=limit)
    counts = state.storage_v2.get_pending_query_count()
    return {"queries": queries, "counts": counts}


def _create_rule_from_ai_response(req: AIRespondRequest) -> str:
    """父 AI 提供结构化数据时直接创建规则（跳过 DraftGenerator）。"""
    from rule import Rule

    title = req.title.strip()
    content = req.response.strip()
    category = (req.category or "general").strip().lower()
    tags = [t.strip().lower() for t in (req.tags or []) if t.strip()]

    raw = f"{title}{content}{datetime.now().isoformat()}"
    suffix = hashlib.sha256(raw.encode()).hexdigest()[:8]
    rule_id = f"ai_{category}_{datetime.now():%Y%m%d_%H%M%S}_{suffix}"

    rule = Rule(
        id=rule_id, title=title, content=content,
        category=category, tags=tags,
        confidence=0.6,
    )
    state.storage_v2.add(rule)
    if state.index:
        state.index.add(rule)
    state.storage_v2.log_ingestion(
        query=f"ai_respond:{req.query_id}", rule_id=rule_id,
        title=title, category=category, status="created",
    )
    state.logger.info("ai_bridge", f"结构化规则创建: {rule_id} [{category}] {title[:30]}")
    return rule_id


@router.post("/ai/respond")
async def ai_respond(req: AIRespondRequest, auth=Depends(require_write_token)):
    """提交对委托查询的回答（由父 AI 调用）。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    ok = state.storage_v2.answer_pending_query(
        req.query_id, req.response,
        responder="parent-ai",
        error_message=req.error_message,
    )
    if not ok:
        return JSONResponse(status_code=404, content={"error": "查询不存在或已回答"})

    if req.title and len(req.title.strip()) >= 4:
        try:
            rule_id = _create_rule_from_ai_response(req)
            return {"status": "ok", "rule_id": rule_id, "source": "structured"}
        except Exception as e:
            return {"status": "ok", "warning": f"规则创建失败: {e}", "source": "structured"}

    if state.auto_ingest and not req.error_message:
        try:
            state.auto_ingest.enqueue(
                f"pending:{req.query_id}",
                req.response,
                "consistent",
            )
        except Exception:
            state.logger.warn("ai_bridge", "ai_respond: auto_ingest.enqueue 失败")
    return {"status": "ok", "source": "delegated"}


@router.get("/ai/query/status/{query_id}")
async def ai_query_status(query_id: str):
    """查询委托 AI 的处理状态。"""
    if not state.storage_v2:
        return {"status": "unknown", "error": "存储未启用"}
    queries = state.storage_v2.get_pending_queries(limit=100)
    for q in queries:
        if q.get("id") == query_id:
            return {"status": q.get("status", "pending"), "query_id": query_id}
    return {"status": "unknown", "query_id": query_id}


@router.post("/ai/conversation/start")
async def ai_conversation_start(auth=Depends(require_write_token)):
    """开始新的多轮对话。"""
    if not state.ai_bridge:
        return {"error": "AI Bridge 未启用"}
    cid = f"conv_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    return {"conversation_id": cid}


@router.post("/ai/conversation/query")
async def ai_conversation_query(auth=Depends(require_write_token), query: str = Query(...),
                                conversation_id: str = Query(...)):
    """多轮对话查询。"""
    if not state.ai_bridge or not state.ai_bridge.is_enabled():
        return {"error": "AI Bridge 未启用"}
    try:
        from core.models import SearchRequest
        req = SearchRequest(query=query)
        result = state.ai_bridge.enhance_query(req.query, conversation_id=conversation_id)
        return result
    except Exception as e:
        return {"error": str(e)}


@router.post("/ai/conversation/clear")
async def ai_conversation_clear(conversation_id: str = Query(...), auth=Depends(require_write_token)):
    """清除对话历史。"""
    if state.ai_bridge:
        state.ai_bridge.clear_conversation(conversation_id)
    return {"status": "ok"}


# ── 提炼日志 ──────────────────────────────────────────


@router.get("/ingest/logs")
async def ingest_logs(limit: int = Query(50, ge=1, le=500),
                      status: Optional[str] = Query(None)):
    """获取规则提炼日志。"""
    if not state.storage_v2:
        return {"logs": []}
    return {"logs": state.storage_v2.get_ingestion_logs(limit, status)}


@router.post("/ingest/run")
async def ingest_run(auth=Depends(require_write_token)):
    """手动触发一次提炼扫描。"""
    if not state.auto_ingest:
        return {"error": "auto_ingest 未启用"}
    created = state.auto_ingest.process_pending()
    return {"created": len(created), "rule_ids": created}
