"""
Rulerything — 规则 CRUD / 去重 / 进化路由
"""

from typing import Optional

from fastapi import APIRouter, Query, Depends
from fastapi.responses import JSONResponse

from core.state import state
from core.auth import require_write_token
from core.models import AddRuleRequest, RollbackRequest, RuleListItem
from rule import Rule

router = APIRouter()


@router.post("/dedup/dry-run")
async def dedup_dry_run(auth=Depends(require_write_token)):
    """预览去重结果。"""
    return {"duplicates": state.storage.dedup_dry_run()}


@router.post("/dedup/apply")
async def dedup_apply(auth=Depends(require_write_token)):
    """执行去重（增量更新索引）。"""
    results = state.storage.dedup_apply()
    # 增量更新受去重影响的规则，避免全量重建
    for r in results:
        rule_id = r["rule_id"]
        state.index.remove(rule_id)
        updated = state.storage.get(rule_id)
        if updated:
            state.index.add(updated)
    state.logger.info("dedup", f"去重完成: {len(results)} 条规则被标记", count=len(results))
    return {"applied": len(results), "details": results}


@router.post("/evolve")
async def trigger_evolution(dry_run: bool = False, auth=Depends(require_write_token)):
    """触发进化。"""
    changes = state.evolution.apply_pending_evolutions(dry_run=dry_run)
    return {"applied": len(changes), "dry_run": dry_run, "changes": changes}


@router.post("/add-rule")
async def add_rule(req: AddRuleRequest, auth=Depends(require_write_token)):
    """添加一条新规则。"""
    rule = Rule(
        id=req.id, title=req.title, content=req.content,
        category=req.category, tags=req.tags,
        confidence=req.confidence, verifier=req.verifier,
    )
    ok, msg = state.storage.add(rule)
    if ok:
        state.logger.info("api", f"添加规则 {rule.id}", rule_id=rule.id)
        return {"ok": True, "msg": "ok"}
    else:
        return JSONResponse(status_code=409, content={"ok": False, "msg": msg})


@router.post("/rollback")
async def rollback(req: RollbackRequest, auth=Depends(require_write_token)):
    """回滚规则到指定版本。"""
    ok = state.evolution.rollback(req.rule_id, req.target_version)
    return {"success": ok, "rule_id": req.rule_id, "target_version": req.target_version}


@router.get("/evolution/stats")
async def evolution_stats():
    """进化引擎统计。"""
    return state.evolution.stats()


@router.get("/evolution/pending")
async def evolution_pending():
    """查看待处理进化。"""
    return {
        "pending_count": state.evolution.pending_count,
        "pending": state.evolution.pending_evolutions,
    }


@router.get("/evolution/versions/{rule_id}")
async def evolution_versions(rule_id: str):
    """查看规则的归档版本。"""
    versions = state.evolution.list_archived_versions(rule_id)
    return {"rule_id": rule_id, "archived_versions": versions}


@router.get("/rules")
async def list_rules(category: Optional[str] = Query(None)):
    """列出所有规则。"""
    rules = state.storage.list(category=category)
    return [
        RuleListItem(
            id=r.id, title=r.title, content=r.content,
            category=r.category, tags=r.tags,
            confidence=r.confidence, version=r.version,
            hit_count=r.hit_count,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in sorted(rules, key=lambda x: x.id)
    ]
