"""
Rulerything 4.0 — ε-贪心探索机制

防止马太效应：10% 概率将非首位的高分规则与第一名交换。
"""

import random
from typing import List


def maybe_explore(
    ranked_rules: List,
    epsilon: float = 0.10,
) -> List:
    """
    ε-贪心探索：以 ε 概率将非首位的高分规则与第一名交换，防止马太效应。
    探索范围: 前 10。epsilon=0 时恒等返回。
    """
    if epsilon <= 0 or len(ranked_rules) < 2:
        return ranked_rules

    if random.random() < epsilon and len(ranked_rules) >= 3:
        swap_candidates = range(1, min(10, len(ranked_rules)))
        if swap_candidates:
            j = random.choice(list(swap_candidates))
            ranked_rules = list(ranked_rules)
            ranked_rules[0], ranked_rules[j] = ranked_rules[j], ranked_rules[0]

    return ranked_rules
