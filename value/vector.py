"""
Rulerything 4.0 — 价值向量运算

集中实现所有向量运算，消除重复代码。
"""

from typing import Dict
from .const import VALUE_DIMENSIONS, DEFAULT_VALUE


def cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    """余弦相似度。缺失维度视作 0.0。"""
    all_dims = set(a) | set(b)
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for d in all_dims:
        va = a.get(d, 0.0)
        vb = b.get(d, 0.0)
        dot += va * vb
        norm_a += va * va
        norm_b += vb * vb
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a ** 0.5 * norm_b ** 0.5)


def weighted_sum(values: Dict[str, float], weights: Dict[str, float]) -> float:
    """
    加权和：Σ(weight[i] × value[i]) / Σ(weight[i])。

    所有 VALUE_DIMENSIONS 参与计算。
    未指定的 value 和 weight 都使用 DEFAULT_VALUE (0.5)。
    """
    total = 0.0
    weight_sum = 0.0
    for dim in VALUE_DIMENSIONS:
        v = values.get(dim, DEFAULT_VALUE)
        w = weights.get(dim, DEFAULT_VALUE)
        total += w * v
        weight_sum += w
    return total / weight_sum if weight_sum > 0 else DEFAULT_VALUE


def dot_product(a: Dict[str, float], b: Dict[str, float]) -> float:
    """点积。缺失维度视为 0.0。"""
    result = 0.0
    for d in set(a) & set(b):
        result += a[d] * b[d]
    return result
