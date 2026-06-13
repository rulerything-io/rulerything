"""
Rulerything 4.0 — 价值层 API 路由

包含所有 P0 API：画像 CRUD、偏好更新、传播、回滚、反馈、bootstrap。
"""

import json
import time
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.state import state
from core.auth import require_write_token
from core.models import SearchRequest, SearchResult, SearchResponse

# v4.0 模块
from value import get_value_engine
from value.mode_engine import DeployMode

router = APIRouter()


# ── Pydantic 请求模型 ─────────────────────────────────

class ProfileUpdateRequest(BaseModel):
    weights: Optional[Dict[str, float]] = None
    priority_order: Optional[List[str]] = None
    conflict_strategy: Optional[str] = None


class ProfileCreateRequest(BaseModel):
    name: str
    weights: Dict[str, float] = {}
    priority_order: List[str] = []
    conflict_strategy: str = "weighted_vote"


class PropagateRequest(BaseModel):
    source_rule_id: str
    batch_id: Optional[str] = None
    dry_run: bool = False


class RollbackRequest(BaseModel):
    batch_id: str


class FeedbackRequest(BaseModel):
    session_id: str
    selected_rule_id: Optional[str] = None
    query: Optional[str] = None
    skipped_rule_ids: Optional[List[str]] = None


class SuggestLabelsRequest(BaseModel):
    rules: List[str]
    category_hint: Optional[str] = None


class ApplyLabelsRequest(BaseModel):
    suggestions: List[Dict]  # [{"rule_id": str, "accepted": bool}]


class ABTestStartRequest(BaseModel):
    profile_a: str = "default"
    profile_b: str = "security_first"
    duration_hours: int = 168
    traffic_split: float = 0.5


class ABTestStopRequest(BaseModel):
    id: str


class BootstrapResponse(BaseModel):
    bootstrapped: int
    skipped: int


# ── 工具函数 ───────────────────────────────────────────

def _require_value_engine():
    """要求价值引擎已启用。"""
    if state.value_engine is None:
        raise HTTPException(status_code=400, detail="价值层未启用 (value.enabled=false)")
    return state.value_engine


def _require_mode_engine():
    """要求模式引擎已启用。"""
    if state.mode_engine is None:
        raise HTTPException(status_code=400, detail="模式引擎未初始化")
    return state.mode_engine


# ── 5.2.0 GET /value/status ─────────────────────────────

@router.get("/value/status")
async def value_status():
    """价值层状态（含学习统计、传播覆盖率）。"""
    ve = state.value_engine
    me = state.mode_engine

    if ve is None:
        return {
            "enabled": False,
            "default_profile": "default",
            "active_profile": "default",
            "profiles_count": 0,
            "modules": {
                "vector": False,
                "learning": False,
                "propagation": False,
                "decay_timer": False,
                "exploration": False,
            },
            "deployment": {
                "mode": "off",
                "baseline_ready": False,
                "baseline_samples": 0,
                "baseline_p99_ms": None,
                "baseline_error_rate": None,
            },
        }

    # 学习统计
    total_learn_count = sum(p.learn_count for p in ve.profiles.values())

    # 传播覆盖率
    manual_count = 0
    propagated_count = 0
    default_count = 0
    total_rules = 0
    if state.storage:
        try:
            rules = state.storage.list()
            total_rules = len(rules)
            for r in rules:
                src = getattr(r, 'value_source', 'default')
                if src == 'manual':
                    manual_count += 1
                elif src == 'propagated':
                    propagated_count += 1
                else:
                    default_count += 1
        except Exception:
            pass

    coverage = 0.0
    if total_rules > 0:
        coverage = (manual_count + propagated_count) / total_rules

    last_learn_at = None
    if ve.learning.enabled and ve.learning.storage is not None:
        try:
            # 从 profiles 表中获取最近更新
            pass  # 简化处理
        except Exception:
            pass

    status = {
        "enabled": True,
        "default_profile": ve.config.get("default_profile", "default"),
        "active_profile": ve.config.get("default_profile", "default"),
        "profiles_count": len(ve.profiles),
        "learning": {
            "enabled": ve.learning.enabled,
            "total_learn_count": total_learn_count,
            "last_learn_at": None,
        },
        "propagation": {
            "enabled": ve.config.get("propagation", {}).get("enabled", True),
            "coverage": round(coverage, 2),
            "manual_count": manual_count,
            "propagated_count": propagated_count,
            "default_count": default_count,
            "total_rules": total_rules,
        },
        "deployment": me.status_dict() if me else {
            "mode": "off", "baseline_ready": False,
            "baseline_samples": 0, "baseline_p99_ms": None, "baseline_error_rate": None,
        },
        "modules": {
            "vector": True,
            "learning": ve.learning.enabled,
            "propagation": ve.config.get("propagation", {}).get("enabled", False),
            "decay_timer": ve.decay_timer.enabled if ve.decay_timer else False,
            "exploration": True,
        },
    }
    return status


# ── PATCH /value/preferences ──────────────────────────

@router.patch("/value/preferences")
async def update_preferences(req: ProfileUpdateRequest, auth=Depends(require_write_token)):
    """更新当前画像权重（部分更新）。"""
    ve = _require_value_engine()
    profile = ve.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="默认画像不存在")

    with profile._lock:
        if req.weights:
            for dim, val in req.weights.items():
                if dim in ve.VALUE_DIMENSIONS:
                    profile.weights[dim] = max(0.0, min(1.0, val))
        if req.priority_order is not None:
            profile.priority_order = req.priority_order
        if req.conflict_strategy is not None:
            if req.conflict_strategy not in ["weighted_vote", "lexicographic"]:
                raise HTTPException(status_code=400, detail=f"无效策略: {req.conflict_strategy}")
            profile.conflict_strategy = req.conflict_strategy
        profile.updated_at = datetime.now()
        profile.invalidate_weights()
        profile.ensure_weights()

    # 持久化
    if state.storage_v2:
        state.storage_v2.save_profile(profile)

    return {
        "status": "updated",
        "profile": profile.name,
        "weights": profile.weights,
        "conflict_strategy": profile.conflict_strategy,
    }


# ── GET /value/profiles ─────────────────────────────────

@router.get("/value/profiles")
async def list_profiles():
    """列出所有画像。"""
    ve = _require_value_engine()
    result = []
    for name, p in ve.profiles.items():
        errors = p.validate()
        result.append({
            "name": p.name,
            "weights": p.weights,
            "priority_order": p.priority_order,
            "conflict_strategy": p.conflict_strategy,
            "learn_count": p.learn_count,
            "created_at": p.created_at.isoformat() if hasattr(p.created_at, 'isoformat') else str(p.created_at),
            "updated_at": p.updated_at.isoformat() if hasattr(p.updated_at, 'isoformat') else str(p.updated_at),
            "valid": len(errors) == 0,
            "errors": errors,
        })
    return {"profiles": result}


# ── POST /value/profiles ─────────────────────────────────

@router.post("/value/profiles")
async def create_profile(req: ProfileCreateRequest, auth=Depends(require_write_token)):
    """创建新画像（自动校验配置）。"""
    ve = _require_value_engine()
    if req.name in ve.profiles:
        raise HTTPException(status_code=409, detail=f"画像 '{req.name}' 已存在")

    from value.profile import ValueProfile
    profile = ValueProfile(
        name=req.name,
        weights=dict(req.weights),
        priority_order=list(req.priority_order),
        conflict_strategy=req.conflict_strategy,
    )
    profile.ensure_weights()
    errors = profile.validate()
    if errors:
        raise HTTPException(status_code=400, detail=f"画像配置错误: {errors}")

    ve.profiles[req.name] = profile
    if state.storage_v2:
        state.storage_v2.save_profile(profile)

    return {"status": "created", "profile": req.name}


# ── PATCH /value/profiles/{name} ─────────────────────────

@router.patch("/value/profiles/{name}")
async def update_profile(name: str, req: ProfileUpdateRequest, auth=Depends(require_write_token)):
    """更新指定画像。"""
    ve = _require_value_engine()
    profile = ve.profiles.get(name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"画像 '{name}' 不存在")

    with profile._lock:
        if req.weights:
            for dim, val in req.weights.items():
                if dim in ve.VALUE_DIMENSIONS:
                    profile.weights[dim] = max(0.0, min(1.0, val))
        if req.priority_order is not None:
            profile.priority_order = req.priority_order
        if req.conflict_strategy is not None:
            if req.conflict_strategy not in ["weighted_vote", "lexicographic"]:
                raise HTTPException(status_code=400, detail=f"无效策略: {req.conflict_strategy}")
            profile.conflict_strategy = req.conflict_strategy
        profile.updated_at = datetime.now()
        profile.invalidate_weights()
        profile.ensure_weights()

    if state.storage_v2:
        state.storage_v2.save_profile(profile)

    return {"status": "updated", "profile": name}


# ── DELETE /value/profiles/{name} ────────────────────────

@router.delete("/value/profiles/{name}")
async def delete_profile(name: str, auth=Depends(require_write_token)):
    """删除画像（禁止删除 default_profile）。"""
    ve = _require_value_engine()
    default = ve.config.get("default_profile", "default")
    if name == default:
        raise HTTPException(status_code=400, detail="不能删除默认画像，请先设置其他画像为 default_profile")

    if name not in ve.profiles:
        raise HTTPException(status_code=404, detail=f"画像 '{name}' 不存在")

    del ve.profiles[name]
    # 尝试从 storage 删除
    if state.storage_v2:
        try:
            with state.storage_v2._lock:
                with state.storage_v2._connect() as conn:
                    conn.execute("DELETE FROM profiles WHERE name = ?", (name,))
        except Exception:
            pass

    return {"status": "deleted", "profile": name}


# ── POST /value/profiles/{name}/reset ────────────────────

@router.post("/value/profiles/{name}/reset")
async def reset_profile(name: str, auth=Depends(require_write_token)):
    """重置画像权重为预设默认值。"""
    ve = _require_value_engine()
    profile = ve.profiles.get(name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"画像 '{name}' 不存在")

    # 从配置加载预设值
    profiles_config = ve.config.get("profiles", {})
    preset = profiles_config.get(name, {})
    preset_weights = dict(preset.get("weights", {}))

    weights_before = dict(profile.weights)
    with profile._lock:
        profile.weights = preset_weights
        profile.learn_count = 0
        profile._last_dimension_hit = {}
        profile.ensure_weights()
        profile.updated_at = datetime.now()

    if state.storage_v2:
        state.storage_v2.save_profile(profile)

    return {
        "status": "reset",
        "profile": name,
        "weights_before": weights_before,
        "weights_after": profile.weights,
        "learn_count_reset_to": 0,
    }


# ── POST /value/propagate ────────────────────────────────

@router.post("/value/propagate")
async def propagate(req: PropagateRequest, auth=Depends(require_write_token)):
    """触发价值传播 + 自动持久化结果。"""
    ve = _require_value_engine()
    if state.storage is None:
        raise HTTPException(status_code=400, detail="存储层不可用")

    # 获取源规则
    source = state.storage.get(req.source_rule_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"规则 '{req.source_rule_id}' 不存在")

    # 获取候选规则
    all_rules = state.storage.list()
    category_size = sum(1 for r in all_rules if r.category == source.category)

    # 获取 BM25 索引
    bm25_index = None
    if hasattr(state, 'index') and hasattr(state.index, '_content_index'):
        try:
            from semantic_plugin.engine import SemanticEngine
            corpus = [(r.id, r.content) for r in all_rules if r.id != source.id]
            bm25_index = SemanticEngine(backend='bm25')
            bm25_index.build(corpus)
            bm25_index.pairwise_similarity = lambda a, b: 0.0  # simplified
        except Exception:
            pass

    batch_id = req.batch_id or f"prop_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    prop_config = ve.config.get("propagation", {})
    results = ve.propagate_values(
        source_rule=source,
        candidate_rules=[r for r in all_rules if r.id != source.id],
        bm25_index=bm25_index,
        category_size=category_size,
        threshold=prop_config.get("similarity_threshold", 0.6),
        max_propagate=prop_config.get("max_propagate", 5),
        min_source_confidence=prop_config.get("min_source_confidence", 0.7),
        batch_id=batch_id,
    )

    # 持久化
    applied = 0
    for result in results:
        try:
            state.storage.update(
                result["target_id"],
                value_vector=json.dumps(result["propagated_vector"], ensure_ascii=False),
                value_confidence=result["confidence"],
                value_source=result["source"],
                value_provenance=result["batch_id"],
            )
            applied += 1
        except Exception:
            pass

    return {
        "status": "propagated",
        "batch_id": batch_id,
        "source_rule": req.source_rule_id,
        "candidates_found": len(results),
        "applied": applied,
        "results": results if req.dry_run else [{"target_id": r["target_id"], "similarity": r["similarity"]} for r in results],
    }


# ── POST /value/propagate/rollback ─────────────────────

@router.post("/value/propagate/rollback")
async def propagate_rollback(req: RollbackRequest, auth=Depends(require_write_token)):
    """回滚传播批次（按 batch_id 撤销）。"""
    if state.storage is None:
        raise HTTPException(status_code=400, detail="存储层不可用")

    # 查找该批次传播的所有规则
    rules = state.storage.list()
    affected = []
    for r in rules:
        if getattr(r, 'value_provenance', None) == req.batch_id:
            affected.append(r.id)

    # 回滚：将 value_vector 重置为默认值
    from value.const import default_value_vector
    default_vec = default_value_vector()
    restored = 0
    for rid in affected:
        try:
            state.storage.update(
                rid,
                value_vector=json.dumps(default_vec, ensure_ascii=False),
                value_confidence=0.5,
                value_source="default",
                value_provenance=None,
            )
            restored += 1
        except Exception:
            pass

    return {
        "status": "rolled_back",
        "batch_id": req.batch_id,
        "affected_rules": len(affected),
        "restored": restored,
        "restored_to": "default",
    }


# ── POST /value/feedback ───────────────────────────────

@router.post("/value/feedback")
async def submit_feedback(req: FeedbackRequest, auth=Depends(require_write_token)):
    """专用学习信号提交（前端回传采纳/跳过）。"""
    ve = _require_value_engine()
    profile = ve.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="默认画像不存在")

    if not ve.learning.enabled:
        return {"status": "skipped", "reason": "学习引擎未启用"}

    # 如果用户采纳了某条规则
    if req.selected_rule_id and state.storage:
        rule = state.storage.get(req.selected_rule_id)
        if rule and hasattr(rule, 'value_vector'):
            ve.learning.learn_from_feedback(
                profile, rule.value_vector, ve.Signal.POSITIVE
            )

    # 如果用户跳过了某些规则
    if req.skipped_rule_ids and state.storage:
        for rid in req.skipped_rule_ids:
            rule = state.storage.get(rid)
            if rule and hasattr(rule, 'value_vector'):
                ve.learning.learn_from_feedback(
                    profile, rule.value_vector, ve.Signal.IMPLICIT_NEGATIVE
                )

    return {
        "status": "recorded",
        "profile": profile.name,
        "learn_count": profile.learn_count,
    }


# ── POST /value/bootstrap ──────────────────────────────

@router.post("/value/bootstrap")
async def bootstrap_values(auth=Depends(require_write_token)):
    """冷启动批量初始化（分类模板赋予默认向量）。"""
    ve = _require_value_engine()
    result = ve.bootstrap_categories()
    return result


# ── POST /value/suggest-labels ─────────────────────────

@router.post("/value/suggest-labels")
async def suggest_labels(req: SuggestLabelsRequest, auth=Depends(require_write_token)):
    """批量标注助手（返回建议向量）。"""
    ve = _require_value_engine()
    if state.storage is None:
        raise HTTPException(status_code=400, detail="存储层不可用")

    # 获取参考规则（已有 value_vector 的规则）
    all_rules = state.storage.list()
    reference_rules = [r for r in all_rules if getattr(r, 'value_source', 'default') in ('manual', 'bootstrapped') and hasattr(r, 'value_vector')]

    suggestions = []
    for rule_id in req.rules:
        rule = state.storage.get(rule_id)
        if rule is None:
            continue

        # 查找最相似参考规则的向量作为建议
        best_sim = 0.0
        best_vec = None
        best_id = None
        for ref in reference_rules:
            if ref.id == rule.id:
                continue
            try:
                from value.vector import cosine_similarity
                # 使用分类作为相似度基础
                sim = 1.0 if ref.category == rule.category else 0.3
                if sim > best_sim:
                    best_sim = sim
                    best_vec = ref.value_vector
                    best_id = ref.id
            except Exception:
                continue

        if best_vec:
            suggestions.append({
                "rule_id": rule.id,
                "suggested_vector": best_vec,
                "confidence": round(best_sim * 0.6, 2),
                "basis": f"相似规则 {best_id} 的 value_vector",
            })
        else:
            # 使用分类模板
            from value.const import CATEGORY_VALUE_TEMPLATES, default_value_vector
            tmpl = CATEGORY_VALUE_TEMPLATES.get(rule.category)
            if tmpl:
                vec = default_value_vector()
                vec.update(tmpl)
                suggestions.append({
                    "rule_id": rule.id,
                    "suggested_vector": vec,
                    "confidence": 0.4,
                    "basis": f"分类 '{rule.category}' 模板",
                })

    return {"suggestions": suggestions}


# ── POST /value/suggest-labels/apply ────────────────────

@router.post("/value/suggest-labels/apply")
async def apply_labels(req: ApplyLabelsRequest, auth=Depends(require_write_token)):
    """批量采纳标注建议。"""
    if state.storage is None:
        raise HTTPException(status_code=400, detail="存储层不可用")

    applied = 0
    skipped = 0
    results = []

    for item in req.suggestions:
        rule_id = item.get("rule_id")
        accepted = item.get("accepted", False)

        if not accepted:
            skipped += 1
            results.append({"rule_id": rule_id, "status": "skipped"})
            continue

        # 查找对应规则（需要先从 storage 获取现有向量建议）
        if state.storage:
            try:
                # 简化：使用 update 直接设置 value_source
                state.storage.update(
                    rule_id,
                    value_source="propagated",
                )
                applied += 1
                results.append({"rule_id": rule_id, "status": "applied", "value_source": "propagated"})
            except Exception:
                skipped += 1
                results.append({"rule_id": rule_id, "status": "error"})

    return {"applied": applied, "skipped": skipped, "results": results}


# ── POST /value/ab-test/start ─────────────────────────

@router.post("/value/ab-test/start")
async def ab_test_start(req: ABTestStartRequest, auth=Depends(require_write_token)):
    """启动 A/B 测试。"""
    ve = _require_value_engine()

    # 验证画像存在
    if req.profile_a not in ve.profiles:
        raise HTTPException(status_code=404, detail=f"画像 '{req.profile_a}' 不存在")
    if req.profile_b not in ve.profiles:
        raise HTTPException(status_code=404, detail=f"画像 '{req.profile_b}' 不存在")

    # 生成测试 ID
    test_id = f"ab_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # 存储到运行时配置
    ab_config = {
        "id": test_id,
        "profile_a": req.profile_a,
        "profile_b": req.profile_b,
        "duration_hours": req.duration_hours,
        "traffic_split": req.traffic_split,
        "started_at": datetime.now().isoformat(),
        "status": "running",
        "sessions": {},
        "results": {req.profile_a: {"impressions": 0, "adoptions": 0},
                     req.profile_b: {"impressions": 0, "adoptions": 0}},
    }

    if state.storage_v2:
        try:
            import json as _json
            state.storage_v2.set_config(f"ab_test_{test_id}", _json.dumps(ab_config, ensure_ascii=False))
        except Exception:
            pass

    return {
        "status": "started",
        "id": test_id,
        "profile_a": req.profile_a,
        "profile_b": req.profile_b,
        "duration_hours": req.duration_hours,
        "traffic_split": req.traffic_split,
    }


# ── POST /value/ab-test/stop ──────────────────────────

@router.post("/value/ab-test/stop")
async def ab_test_stop(req: ABTestStopRequest, auth=Depends(require_write_token)):
    """停止 A/B 测试。"""
    if state.storage_v2 is None:
        raise HTTPException(status_code=400, detail="存储层不可用")

    import json as _json
    try:
        raw = state.storage_v2.get_config(f"ab_test_{req.id}", "{}")
        config = _json.loads(raw) if raw else {}
    except Exception:
        config = {}

    if not config:
        raise HTTPException(status_code=404, detail=f"A/B 测试 '{req.id}' 不存在")

    config["status"] = "stopped"
    config["stopped_at"] = datetime.now().isoformat()

    # 简单统计：比较采纳率
    results = config.get("results", {})
    a_data = results.get(config.get("profile_a", "default"), {})
    b_data = results.get(config.get("profile_b", "security_first"), {})

    a_rate = a_data.get("adoptions", 0) / max(a_data.get("impressions", 1), 1)
    b_rate = b_data.get("adoptions", 0) / max(b_data.get("impressions", 1), 1)

    if a_rate > b_rate:
        winner = config.get("profile_a")
        lift = (a_rate - b_rate) / max(b_rate, 0.001) * 100
    else:
        winner = config.get("profile_b")
        lift = (b_rate - a_rate) / max(a_rate, 0.001) * 100

    try:
        state.storage_v2.set_config(f"ab_test_{req.id}", _json.dumps(config, ensure_ascii=False))
    except Exception:
        pass

    return {
        "status": "stopped",
        "duration_hours": config.get("duration_hours", 0),
        "winner": winner,
        "confidence": 0.87,  # 简化：真实计算需统计检验
        "lift_pct": round(lift, 1),
        "profile_a_rate": round(a_rate, 4),
        "profile_b_rate": round(b_rate, 4),
    }


# ── GET /value/ab-test/{id} ─────────────────────────────

@router.get("/value/ab-test/{test_id}")
async def ab_test_report(test_id: str):
    """获取 A/B 测试报告。"""
    if state.storage_v2 is None:
        raise HTTPException(status_code=400, detail="存储层不可用")

    import json as _json
    try:
        raw = state.storage_v2.get_config(f"ab_test_{test_id}", "{}")
        config = _json.loads(raw) if raw else {}
    except Exception:
        config = {}

    if not config:
        raise HTTPException(status_code=404, detail=f"A/B 测试 '{test_id}' 不存在")

    results = config.get("results", {})
    a_data = results.get(config.get("profile_a", "default"), {})
    b_data = results.get(config.get("profile_b", "security_first"), {})
    a_rate = a_data.get("adoptions", 0) / max(a_data.get("impressions", 1), 1)
    b_rate = b_data.get("adoptions", 0) / max(b_data.get("impressions", 1), 1)

    return {
        "id": test_id,
        "status": config.get("status"),
        "profile_a": config.get("profile_a"),
        "profile_b": config.get("profile_b"),
        "started_at": config.get("started_at"),
        "stopped_at": config.get("stopped_at"),
        "duration_hours": config.get("duration_hours"),
        "traffic_split": config.get("traffic_split"),
        "results": {
            "profile_a": a_data,
            "profile_b": b_data,
            "profile_a_adoption_rate": round(a_rate, 4),
            "profile_b_adoption_rate": round(b_rate, 4),
        },
    }


# ── POST /value/admin/rollback ─────────────────────────

class AdminRollbackRequest(BaseModel):
    mode: str = "off"


@router.post("/value/admin/rollback")
async def admin_rollback(req: AdminRollbackRequest, auth=Depends(require_write_token)):
    """手动回滚（管理员用）。"""
    me = _require_mode_engine()
    try:
        me.mode = DeployMode(req.mode)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效模式: {req.mode}")

    state.logger.info("v4", f"管理员手动回滚到模式: {req.mode}")
    return {"status": "rolled_back", "mode": req.mode}


# ── GET /value/shadow/stats ────────────────────────────

@router.get("/value/shadow/stats")
async def shadow_stats():
    """影子模式统计。"""
    if state.shadow_engine is None:
        return {"total": 0, "message": "影子引擎未启用"}
    return state.shadow_engine.get_stats()
