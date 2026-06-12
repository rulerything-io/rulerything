"""
Rulerything — v3.0 高级功能路由

包括：依赖关系、提案系统、知识缺口、冷规则、自动演化、告警管理
"""

from typing import Optional

from fastapi import APIRouter, Query, Depends
from fastapi.responses import JSONResponse

from core.state import state
from core.auth import require_write_token
from core.background import get_metrics

router = APIRouter()


# ── 依赖关系 ──────────────────────────────────────────


@router.get("/deps/graph")
async def deps_graph():
    """获取依赖图数据（D3.js 格式）。"""
    if not state.dep_miner:
        return {"nodes": [], "edges": []}
    return state.dep_miner.get_graph_data()


@router.get("/deps/chain/{rule_id:path}")
async def deps_chain(rule_id: str, max_depth: int = Query(3, ge=1, le=10)):
    """获取某规则的影响链。"""
    if not state.dep_miner:
        return {"error": "dep_miner 未启用"}
    return {"rule_id": rule_id, "chain": state.dep_miner.get_impact_chain(rule_id, max_depth)}


@router.get("/deps/conflicts")
async def deps_conflicts():
    """获取检测到的冲突规则对。"""
    if not state.dep_miner:
        return {"conflicts": []}
    return {"conflicts": state.dep_miner.get_relations(relation_type="conflicts")}


@router.post("/deps/refresh")
async def deps_refresh(auth=Depends(require_write_token)):
    """触发依赖关系重新挖掘。"""
    if not state.dep_miner:
        return {"error": "dep_miner 未启用"}
    try:
        state.dep_miner.clear_relations()
        state.dep_miner.mine_all()
        return {"status": "ok", "stats": state.dep_miner.get_stats()}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/deps/relations")
async def deps_relations(rule_id: Optional[str] = Query(None),
                         relation_type: Optional[str] = Query(None)):
    """获取规则关系列表。"""
    if not state.dep_miner:
        return {"relations": []}
    return {"relations": state.dep_miner.get_relations(rule_id, relation_type)}


# ── 提案系统 ──────────────────────────────────────────


@router.get("/proposals")
async def list_proposals(status: Optional[str] = Query(None),
                         module: Optional[str] = Query(None),
                         limit: int = Query(50, ge=1, le=500)):
    """列出提案。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    return {"proposals": state.storage_v2.list_proposals(status, module, limit)}


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str):
    """获取提案详情。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    p = state.storage_v2.get_proposal(proposal_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "提案不存在"})
    return {"proposal": p}


@router.post("/proposals/{proposal_id}/cancel")
async def cancel_proposal(proposal_id: str, auth=Depends(require_write_token)):
    """取消待处理提案。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    p = state.storage_v2.get_proposal(proposal_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "提案不存在"})
    if p["status"] != "pending":
        return JSONResponse(status_code=400,
                            content={"error": f"提案状态为 {p['status']}，无法取消"})
    ok = state.storage_v2.update_proposal_status(proposal_id, "cancelled")
    return {"status": "ok" if ok else "failed"}


# ── 知识缺口 ──────────────────────────────────────────


@router.get("/coverage/gaps")
async def coverage_gaps():
    """检测知识缺口。"""
    if not state.gap_detector:
        return {"gaps": [], "error": "gap_detector 未启用"}
    try:
        gaps = state.gap_detector.detect_gaps()
        return {"gaps": gaps, "count": len(gaps)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/coverage/stats")
async def coverage_stats():
    """覆盖度统计。"""
    if not state.gap_detector:
        return {"error": "gap_detector 未启用"}
    return state.gap_detector.get_coverage_stats()


# ── 冷规则管理 ────────────────────────────────────────


@router.get("/cold/list")
async def cold_list():
    """列出冷存储中的规则。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    return {"rules": [r.to_dict() for r in state.storage_v2.list_cold()]}


@router.post("/cold/archive")
async def cold_archive(days: int = Query(365, ge=30), auth=Depends(require_write_token)):
    """将长时间未命中的规则归档到冷存储。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    archived = state.storage_v2.archive_cold_rules(days=days)
    return {"archived": len(archived), "rule_ids": archived}


@router.post("/cold/unfreeze/{rule_id:path}")
async def cold_unfreeze(rule_id: str, auth=Depends(require_write_token)):
    """从冷存储解冻规则。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    ok = state.storage_v2.unfreeze_rule(rule_id)
    if ok:
        state.index.build(state.storage.list())
        return {"status": "ok", "rule_id": rule_id}
    return JSONResponse(status_code=404, content={"error": f"规则 {rule_id} 不在冷存储中"})


# ── 自动演化引擎 ──────────────────────────────────────


@router.get("/evolver/stats")
async def evolver_stats():
    """自动演化引擎统计。"""
    if not state.auto_evolver:
        return {"error": "auto_evolver 未启用"}
    return state.auto_evolver.get_stats()


@router.get("/evolver/strategy/{name}")
async def evolver_strategy(name: str):
    """获取某条策略的详情。"""
    if not state.auto_evolver:
        return {"error": "auto_evolver 未启用"}
    s = state.auto_evolver.get_strategy(name)
    if not s:
        return {"error": f"未知策略: {name}"}
    return {"strategy": s}


@router.post("/evolver/run/{name}")
async def evolver_run(name: str, auth=Depends(require_write_token)):
    """手动触发某条策略。"""
    if not state.auto_evolver:
        return {"error": "auto_evolver 未启用"}
    result = state.auto_evolver.run_strategy_now(name, get_metrics())
    return {"result": result}


# ── 告警系统 ──────────────────────────────────────────


@router.get("/alerts/health")
async def alerts_health():
    """告警系统健康状态。"""
    if not state.alert_manager:
        return {"enabled": False}
    return state.alert_manager.health_check()


@router.post("/alerts/test")
async def alerts_test(auth=Depends(require_write_token)):
    """发送测试告警。"""
    if not state.alert_manager:
        return {"error": "alert_manager 未启用"}
    ok = state.alert_manager.send("system", "info", "这是一条测试告警 (v3.0)")
    return {"sent": ok}
