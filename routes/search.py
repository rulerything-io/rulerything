"""
Rulerything — 搜索 / 预热路由（v4.0 集成）
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

# v4.0 模块（延迟导入）
from value.mode_engine import DeployMode

router = APIRouter()


def _filter_value_fields(result_dict: dict, value_enabled: bool) -> dict:
    """
    当 value.enabled=false 时，从响应中移除所有 value_* 字段和 decision_trace。
    确保 3.0 客户端看到的响应与升级前完全一致。
    """
    if not value_enabled:
        result_dict.pop("decision_trace", None)
        for item in result_dict.get("results", []):
            item.pop("value_vector", None)
            item.pop("value_confidence", None)
            item.pop("value_source", None)
            item.pop("value_provenance", None)
    return result_dict


async def _collect_learning_signals(
    query: str,
    results: list,
    selected_rule_id: Optional[str],  # 用户采纳的规则（前端回传）
    value_engine,
    profile,
):
    """搜索结果返回后自动采集隐式学习信号。"""
    if value_engine is None or not value_engine.learning.enabled:
        return
    if profile is None:
        return

    top3 = results[:3]

    if selected_rule_id:
        # 被采纳的规则 → POSITIVE
        for r in results:
            if r.id == selected_rule_id:
                value_engine.learning.learn_from_feedback(
                    profile, r.value_vector, value_engine.Signal.POSITIVE
                )
                break
        # 前 3 中未被采纳 → IMPLICIT_NEGATIVE
        for r in top3:
            if r.id != selected_rule_id:
                value_engine.learning.learn_from_feedback(
                    profile, r.value_vector, value_engine.Signal.IMPLICIT_NEGATIVE
                )


@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    """搜索规则。"""
    start = time.perf_counter()
    cache_hits_before = state.index.cache_hit_count
    value_engine = state.value_engine
    mode_engine = state.mode_engine
    profile = None  # 提前声明，避免条件分支未定义

    # ── 1. 确定模式 ────────────────────────────────
    session_id = req.session_id or getattr(req, 'session_id', None)
    if mode_engine:
        use_value_sorting, should_collect = mode_engine.should_use_value_engine(session_id)
    else:
        use_value_sorting, should_collect = False, False

    # ── 2. 执行搜索 ──────────────────────────────────
    if use_value_sorting and value_engine:
        # 4.0 路径：价值排序 + 决策追溯
        profile = value_engine.get_profile(req.profile)
        if profile is None:
            # 回退到 3.0 路径
            results = state.index.search(
                query=req.query,
                search_type=req.search_type,
                category=None if req.category == "all" else req.category,
                lang=req.lang,
                limit=req.limit,
            )
            trace = None
        else:
            raw_results = state.index.search(
                query=req.query,
                search_type=req.search_type,
                category=None if req.category == "all" else req.category,
                lang=req.lang,
                limit=min(100, req.limit * 5),  # 取更多候选供价值排序
            )
            sorted_results = value_engine.sort_rules(raw_results, profile)
            explored = value_engine.maybe_explore(
                sorted_results, value_engine.learning.exploration_epsilon
            )

            # 冲突检测（前 50 条候选）
            resolved_conflicts = []
            if len(explored) >= 2:
                conflicts = value_engine.detect_conflicts(
                    explored[0].value_vector, explored[1].value_vector
                )
                if conflicts:
                    resolved_conflicts = value_engine.resolve_conflicts(
                        conflicts, profile, explored[0].id, explored[1].id,
                        rule_a_value=explored[0].value_vector,
                        rule_b_value=explored[1].value_vector,
                    )

            trace = None
            if explored:
                trace = value_engine.generate_decision_trace(
                    explored[0], explored, profile, resolved_conflicts,
                    brief=req.brief,
                )
            results = explored
    else:
        # 3.0 路径
        results = state.index.search(
            query=req.query,
            search_type=req.search_type,
            category=None if req.category == "all" else req.category,
            lang=req.lang,
            limit=req.limit,
        )
        trace = None

        # Shadow/Dual-write：静默运行 4.0 排序
        if mode_engine and mode_engine.mode in (DeployMode.SHADOW, DeployMode.DUAL_WRITE):
            if value_engine and state.shadow_engine:
                profile = value_engine.get_profile("default")
                if profile:
                    v4_sorted = value_engine.sort_rules(list(results), profile)
                    state.shadow_engine.compare_and_log(
                        req.query, [r.id for r in results],
                        [r.id for r in v4_sorted], profile.name
                    )

    # AI 兜底
    ai_delegated = False
    ai_query_id = ""
    ai_error = False
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
            ai_error = True
            state.logger.warn("search", "AI 兜底调用失败")

    # 对外结果、日志数量和 limit 契约保持一致。
    results = results[:req.limit]
    latency_ms = (time.perf_counter() - start) * 1000
    cache_hit = state.index.cache_hit_count > cache_hits_before
    if hasattr(state.index, 'record_latency'):
        state.index.record_latency(latency_ms)

    if state.storage_v2:
        try:
            state.storage_v2.log_query(
                req.query, latency_ms, len(results), cache_hit,
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
        cache_hit=cache_hit,
        user_feedback=req.user_feedback,
    )
    if state.entropy_engine:
        state.entropy_engine.record_query(
            query=req.query,
            result_ids=[r.id for r in results],
            latency_ms=latency_ms,
            cache_hit=cache_hit,
        )

    if req.user_feedback is not None and results and not results[0].id.startswith("_ai_"):
        context = f"用户对搜索结果{'满意' if req.user_feedback else '不满意'}"
        state.evolution.collect_feedback(results[0].id, req.user_feedback, context)

    # ── 3. 采集学习信号 ──────────────────────────────
    if should_collect and value_engine and profile is not None:
        await _collect_learning_signals(
            req.query, results, req.selected_rule_id, value_engine, profile
        )

    # ── 4. 监控指标 ──────────────────────────────
    if mode_engine:
        mode_engine.record_result(is_error=ai_error, latency_ms=latency_ms)

    # ── 5. 构建响应 ──────────────────────────────
    response = SearchResponse(
        results=[
            SearchResult(
                title=r.title, content=r.content, id=r.id,
                confidence=r.confidence, category=r.category, tags=r.tags,
                lang=r.lang,
                value_vector=getattr(r, 'value_vector', None),
                value_confidence=getattr(r, 'value_confidence', None),
                value_source=getattr(r, 'value_source', None),
                value_provenance=getattr(r, 'value_provenance', None),
            )
            for r in results
        ],
        confidence=results[0].confidence if results else 0.0,
        rule_id=results[0].id if results else "",
        latency_ms=round(latency_ms, 2),
        ai_delegated=ai_delegated,
        ai_query_id=ai_query_id,
        decision_trace=trace,
    )

    # 当 use_value_sorting 为 False 时，过滤 value 字段
    if not use_value_sorting:
        response = _filter_value_fields(response.model_dump(), value_enabled=False)
        response = SearchResponse(**response)

    # ── 6. 自动回滚检查 ──────────────────────────────
    if mode_engine:
        should_rollback, reason = mode_engine.should_auto_rollback()
        if should_rollback:
            state.logger.info("v4", f"[AutoRollback] 触发自动回滚: {reason}")
            from value.mode_engine import DeployMode
            rollback_to = mode_engine.rollback_config.get("rollback_to_mode", "off")
            mode_engine.mode = DeployMode(rollback_to)
            state.logger.info("v4", f"已自动回滚到模式: {rollback_to}")

    return response


@router.post("/warmup")
async def warmup(category: Optional[str] = Query(None), auth=Depends(require_write_token)):
    """预热缓存。"""
    result = state.index.warmup(category=category)
    state.logger.info("cache", "预热完成", **result)
    return {"status": "ok", **result}
