"""
Rulerything 4.0 — 决策追溯链

回答三个问题：
  1. 为什么选这条？
  2. 冲突怎么解决的？
  3. 价值从哪来？
"""

from typing import Dict, List, Optional
from .weighting import value_weighted_score


def generate_decision_trace(
    selected_rule,
    candidates: List,
    profile: "ValueProfile",
    resolved_conflicts: List[Dict],
    brief: bool = False,
) -> Dict:
    """
    生成决策追溯链。
    """
    trace = {
        "selected_rule_id": selected_rule.id,
        "selected_rule_title": selected_rule.title,
        "profile_used": profile.name,
        "strategy_used": profile.conflict_strategy,
        "decision_tree": [],
        "scores": {},
    }

    if brief:
        if len(candidates) >= 2:
            runner_up = candidates[1]
            diffs = []
            for dim in sorted(set(selected_rule.value_vector) | set(runner_up.value_vector)):
                sv = selected_rule.value_vector.get(dim, 0.5)
                rv = runner_up.value_vector.get(dim, 0.5)
                if abs(sv - rv) >= 0.1:
                    diffs.append({"dim": dim, "winner": sv, "runner_up": rv})
            trace["brief_differences"] = diffs
        return trace

    # 完整模式 — 仅计算前 10 条候选，避免大结果集内存膨胀
    max_trace_candidates = 10
    trace_candidates = candidates[:max_trace_candidates]

    trace["decision_tree"].append({
        "step": 1,
        "action": "采纳画像",
        "detail": f"使用价值画像 '{profile.name}'，策略: {profile.conflict_strategy}",
    })

    active_dims = {d: w for d, w in profile.weights.items() if w > 0.5}
    if active_dims:
        trace["decision_tree"].append({
            "step": 2,
            "action": "活跃维度",
            "detail": ", ".join(
                f"{d}({w:.0%})" for d, w in sorted(
                    active_dims.items(), key=lambda x: -x[1]
                )
            ),
        })

    for c in trace_candidates:
        trace["scores"][c.id] = round(
            value_weighted_score(c.value_vector, profile.weights, c.confidence), 3
        )

    if len(candidates) >= 2:
        runner_up = candidates[1]
        dim_comparison = []
        for dim in sorted(set(selected_rule.value_vector) | set(runner_up.value_vector)):
            sv = selected_rule.value_vector.get(dim, 0.5)
            rv = runner_up.value_vector.get(dim, 0.5)
            if abs(sv - rv) >= 0.1:
                dim_comparison.append({
                    "dimension": dim,
                    "selected_value": sv,
                    "runner_up_value": rv,
                    "advantage": "selected" if sv > rv else "runner_up",
                })
        trace["decision_tree"].append({
            "step": 3,
            "action": "逐维比较",
            "detail": f"选中规则 vs {runner_up.id}",
            "dimensions": dim_comparison,
        })

    trace["alternatives"] = [
        {
            "id": c.id,
            "title": c.title,
            "score": round(value_weighted_score(
                c.value_vector, profile.weights, c.confidence
            ), 3),
        }
        for c in candidates[1:4]
    ]

    trace["conflicts_resolved"] = [
        {
            "dimension": rc["dimension"],
            "winner": rc.get("winner", "unknown"),
            "resolution": rc.get("resolution", "加权裁决"),
        }
        for rc in resolved_conflicts[:5]
    ]

    return trace
