"""
Rulerything — Phase 1/2/3 高级引擎路由

包括：熵引擎（Phase 1）、规则免疫系统（Phase 2）、自适应系统（Phase 3）
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from core.state import state
from core.auth import require_write_token
from core.models import (
    ImmuneScanRequest, ImmuneClearRequest,
    AckRequest, QueryRequest, AddRuleRequest,
)

router = APIRouter()


# ═══════════════════════════════════════════════════════
# Phase 1: 熵引擎
# ═══════════════════════════════════════════════════════


@router.get("/entropy/report")
async def entropy_report():
    """获取系统熵报告。"""
    if not state.config.get("entropy", {}).get("enabled", True):
        return {"status": "disabled"}
    return state.entropy_engine.get_report()


@router.get("/entropy/suggestions")
async def entropy_suggestions():
    """获取优化建议。"""
    if not state.config.get("entropy", {}).get("enabled", True):
        return {"suggestions": []}

    metrics = {
        'cache_hit_rate': state.entropy_engine.get_report().get(
            'cache_hit_rate', state.index.stats().get('cache_hit_rate', 0)
        ),
        'avg_query_latency_ms': state.entropy_engine.get_report().get('avg_latency_ms', 0),
        'conflict_count': (
            len(state.adaptive_system.immune_system.regulatory_t_cells)
            if state.adaptive_system and state.adaptive_system.immune_system
            else 0
        ),
        'low_quality_ratio': (
            len(state.adaptive_system.immune_system.nk_targets)
            / max(1, len(state.adaptive_system.rules))
            if state.adaptive_system and state.adaptive_system.immune_system
            else 0
        ),
        'preheat_accuracy': 0,
    }
    suggestions = state.entropy_engine.suggest_optimizations(metrics)
    return {
        "suggestions": [
            {"type": s.type, "target": s.target,
             "description": s.description,
             "estimated_cost": s.estimated_cost,
             "predicted_improvement": s.predicted_improvement}
            for s in suggestions
        ],
        "current_entropy": state.entropy_engine.get_report().get('estimated_system_entropy', 0),
    }


@router.post("/entropy/ack")
async def entropy_ack(req: AckRequest, auth=Depends(require_write_token)):
    """标记优化建议已执行。"""
    from entropy_engine import OptimizationAction
    action = OptimizationAction(type=req.type, target="")
    state.entropy_engine.mark_executed(action)
    return {"status": "acknowledged", "type": req.type}


# ═══════════════════════════════════════════════════════
# Phase 2: 规则免疫系统
# ═══════════════════════════════════════════════════════


@router.post("/immune/scan")
async def immune_scan(req: ImmuneScanRequest, auth=Depends(require_write_token)):
    """扫描所有规则的健康状态。"""
    if not state.immune_system:
        return {"status": "disabled", "message": "免疫系统未启用"}
    rules = state.storage.list()
    results = state.immune_system.batch_scan(rules, auto_cleanup=req.auto_cleanup)
    return {
        "healthy": len(results["healthy"]),
        "weakened": len(results["weakened"]),
        "infected": len(results["infected"]),
        "dead": len(results["dead"]),
        "nk_targets": list(state.immune_system.nk_targets),
        "summary": state.immune_system.get_health_summary(),
    }


@router.post("/immune/clear")
async def immune_clear(req: ImmuneClearRequest, auth=Depends(require_write_token)):
    """NK 清除低质量规则。"""
    if not state.immune_system:
        return {"status": "disabled", "message": "免疫系统未启用"}
    if req.all_nk_targets:
        cleared = state.immune_system.nk_clear()
    else:
        cleared = state.immune_system.nk_clear(req.rule_ids)
    for rid in cleared:
        state.storage.hard_delete(rid)
    state.index.build(state.storage.list())
    return {"cleared": cleared}


@router.get("/health/{rule_id}")
async def rule_health(rule_id: str):
    """查看单条规则的健康详情。"""
    if not state.immune_system:
        return {"status": "disabled", "message": "免疫系统未启用"}
    rule = state.storage.get(rule_id)
    if not rule:
        return {"error": f"规则 {rule_id} 不存在"}
    report = state.immune_system.evaluate_health(rule)
    return {
        "rule_id": report.rule_id,
        "status": report.status.value,
        "score": report.score,
        "dimensions": report.dimensions,
        "conflicts": report.conflicts,
        "antibodies": report.antibodies,
    }


# ═══════════════════════════════════════════════════════
# Phase 3: AdaptiveRuleSystem
# ═══════════════════════════════════════════════════════


@router.post("/query")
async def phase3_query(req: QueryRequest, auth=Depends(require_write_token)):
    """Phase 3 统一查询。"""
    if not state.adaptive_system:
        return JSONResponse(
            status_code=400,
            content={"error": "AdaptiveRuleSystem 未启用"},
        )
    results = state.adaptive_system.query(
        query_text=req.query_text,
        sort_by=req.sort_by,
        category=req.category,
        use_semantic=req.use_semantic,
        limit=req.limit,
    )
    return {
        "results": [
            {
                "id": r.id, "title": r.title, "content": r.content,
                "category": r.category, "tags": r.tags,
                "confidence": r.confidence, "hit_count": r.hit_count,
            }
            for r in results
        ],
        "total": len(results),
    }


@router.get("/status")
async def phase3_status():
    """Phase 3 完整系统状态。"""
    if not state.adaptive_system:
        return JSONResponse(
            status_code=400,
            content={"error": "AdaptiveRuleSystem 未启用"},
        )
    return state.adaptive_system.get_full_status()


@router.get("/cache/stats")
async def cache_stats():
    """Phase 3 缓存统计。"""
    if not state.adaptive_system or not state.adaptive_system.cache:
        return JSONResponse(
            status_code=400,
            content={"error": "Phase 3 缓存未启用"},
        )
    cache = state.adaptive_system.cache
    return {
        "size": len(cache.cache),
        "max_size": cache.max_size,
        "heat_entries": len(cache.heat),
        "threshold": cache.preheat_threshold,
        "decay_half_life": cache.decay_half_life,
    }


@router.post("/index/incremental")
async def index_incremental(req: AddRuleRequest, auth=Depends(require_write_token)):
    """Phase 3 增量添加规则到增强索引。"""
    if not state.adaptive_system:
        return JSONResponse(
            status_code=400,
            content={"error": "AdaptiveRuleSystem 未启用"},
        )
    from rule import Rule
    rule = Rule(
        id=req.id, title=req.title, content=req.content,
        category=req.category, tags=req.tags,
        confidence=req.confidence, verifier=req.verifier,
    )
    state.adaptive_system.index.add(rule)
    state.adaptive_system.rules[rule.id] = rule
    return {"ok": True, "rule_id": rule.id}


@router.post("/optimize")
async def phase3_optimize(auth=Depends(require_write_token)):
    """Phase 3 熵驱动系统优化。"""
    if not state.adaptive_system:
        return JSONResponse(
            status_code=400,
            content={"error": "AdaptiveRuleSystem 未启用"},
        )
    return state.adaptive_system.optimize()
