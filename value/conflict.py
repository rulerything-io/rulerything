"""
Rulerything 4.0 — 价值冲突检测与裁决

扩展性：全库 O(n²) 冲突检测在 > 10000 条规则时不可行。
实际使用分桶策略：
- 每次搜索仅对返回的前 50 条候选规则进行冲突检测（O(1225) 次比较）
- 全库冲突扫描作为离线任务，由熵引擎定时调度
- 实时查询路径仅需 < 5ms（50 条规则的冲突检测）
"""

from typing import Dict, List, Optional


def detect_conflicts(
    rule_a_value: Dict[str, float],
    rule_b_value: Dict[str, float],
    threshold: float = 0.4,
) -> List[Dict]:
    """
    检测两条规则之间的价值冲突。

    冲突定义：在同一维度上，一条 >0.7，另一条 <0.3。
    threshold 控制检测灵敏度，取值范围 (0, 1)。
    """
    conflicts = []
    all_dims = set(rule_a_value) | set(rule_b_value)

    for dim in all_dims:
        va = rule_a_value.get(dim, 0.5)
        vb = rule_b_value.get(dim, 0.5)

        if va > 0.7 and vb < 0.3:
            conflicts.append({
                "dimension": dim,
                "severity": round(va - vb, 2),
                "rule_a_value": va,
                "rule_b_value": vb,
                "verdict": "prefer_a",
            })
        elif vb > 0.7 and va < 0.3:
            conflicts.append({
                "dimension": dim,
                "severity": round(vb - va, 2),
                "rule_a_value": va,
                "rule_b_value": vb,
                "verdict": "prefer_b",
            })

    return sorted(conflicts, key=lambda c: c["severity"], reverse=True)


def _resolve_lexicographic(
    conflict: Dict,
    profile: "ValueProfile",
    rule_a_id: str,
    rule_b_id: str,
    rule_a_value: Optional[Dict[str, float]] = None,
    rule_b_value: Optional[Dict[str, float]] = None,
) -> str:
    """
    Lexicographic 裁决：按 priority_order 逐维比较真实值。

    对于冲突维度从 conflict 中取值，非冲突维度从完整向量中取值。
    rule_a_value/rule_b_value 缺省时回退到从 conflict dict 取值（兼容旧调用）。
    """
    for dim in profile.priority_order:
        if dim == conflict["dimension"]:
            va = conflict["rule_a_value"]
            vb = conflict["rule_b_value"]
        else:
            # 从完整向量中取值，避免 conflict dict 只有冲突维度值的问题
            va = (rule_a_value or {}).get(dim, 0.5)
            vb = (rule_b_value or {}).get(dim, 0.5)
        if abs(va - vb) < 0.05:
            continue
        return rule_a_id if va > vb else rule_b_id
    # 所有优先级维度平局 → 回退到 weighted_vote
    return rule_a_id if profile.weights.get(conflict["dimension"], 0.5) >= 0.5 else rule_b_id


def resolve_conflicts(
    conflicts: List[Dict],
    profile: "ValueProfile",
    rule_a_id: str,
    rule_b_id: str,
    rule_a_value: Optional[Dict[str, float]] = None,
    rule_b_value: Optional[Dict[str, float]] = None,
) -> List[Dict]:
    """
    根据画像策略裁决冲突，返回含 winner 的结果。

    rule_a_value/rule_b_value 是两条规则的完整价值向量，
    仅在 lexicographic 策略中用于非冲突维度的比较。
    """
    resolved = []
    for conflict in conflicts:
        dim = conflict["dimension"]
        user_weight = profile.weights.get(dim, 0.5)

        if profile.conflict_strategy == "lexicographic" and profile.priority_order:
            winner = _resolve_lexicographic(
                conflict, profile, rule_a_id, rule_b_id,
                rule_a_value=rule_a_value,
                rule_b_value=rule_b_value,
            )
            resolution = "优先级裁决"
        else:
            # weighted_vote（包括 user_weight == 0.5 时的默认行为）
            verdict_prefers_a = conflict["verdict"] == "prefer_a"
            if user_weight >= 0.5:
                winner = rule_a_id if verdict_prefers_a else rule_b_id
            else:
                winner = rule_b_id if verdict_prefers_a else rule_a_id
            resolution = f"用户权重 {user_weight:.1f}"

        resolved.append({
            "dimension": dim,
            "severity": conflict["severity"],
            "winner": winner,
            "resolution": resolution,
        })

    return resolved
