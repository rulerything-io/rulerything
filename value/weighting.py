"""
Rulerything 4.0 — 动态加权排序（双策略）
"""

from typing import Dict, List
from .const import VALUE_DIMENSIONS, DEFAULT_VALUE
from .vector import weighted_sum


def value_weighted_score(
    rule_value: Dict[str, float],
    user_weights: Dict[str, float],
    quality_confidence: float,
) -> float:
    """
    加权和评分。

    使用 rule.confidence（规则质量置信度，非 value_confidence）作为乘数。
    value_confidence 仅用于传播门槛，不参与排序评分。

    迭代 VALUE_DIMENSIONS，未指定的用户权重视为 DEFAULT_VALUE (0.5)。
    所有维度参与计算，无遗漏。
    """
    return weighted_sum(rule_value, user_weights) * quality_confidence


def sort_rules(
    rules: List,
    profile: "ValueProfile",
    confidence_field: str = "confidence",
    value_field: str = "value_vector",
) -> List:
    """
    按画像策略排序。

    - weighted_vote: 按 value_weighted_score 降序
    - lexicographic: 按 priority_order 维度逐级比较（用元组键
      而非 pairwise 比较，避免"同规则自比"bug）

    在 lexicographic 模式下，排序键为：
      (dim1_value, dim2_value, ..., weighted_score)
    其中 dim1 > dim2 > ... 按 priority_order。
    """
    profile.ensure_weights()
    errors = profile.validate()
    if errors:
        import logging
        for err in errors:
            logging.warning(f"ValueProfile '{profile.name}' 配置错误: {err}")

    if profile.conflict_strategy == "lexicographic" and profile.priority_order:
        def key_fn(r):
            vec = getattr(r, value_field)
            priority_key = tuple(
                vec.get(dim, DEFAULT_VALUE) for dim in profile.priority_order
            )
            return (
                priority_key,
                value_weighted_score(
                    vec,
                    profile.weights,
                    getattr(r, confidence_field),
                ),
            )
        return sorted(rules, key=key_fn, reverse=True)

    # weighted_vote（也是未知策略的默认回退）
    return sorted(
        rules,
        key=lambda r: value_weighted_score(
            getattr(r, value_field),
            profile.weights,
            getattr(r, confidence_field),
        ),
        reverse=True,
    )
