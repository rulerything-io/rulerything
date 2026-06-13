"""
Rulerything 4.0 — 常量与维度注册表

一切皆规则，规则皆有值。
"""

from typing import Dict, Final, List


# ===== 价值维度注册表（可扩展） =====
# 修改此字典即可添加/删除维度，所有模块自动适配
VALUE_DIMENSIONS: Dict[str, str] = {
    "efficiency":    "执行效率优先",
    "correctness":   "正确性优先",
    "security":      "安全性优先",
    "simplicity":    "简洁性优先",
    "compatibility": "兼容性优先",
    "testability":   "可测试性优先",
    "documentation": "可文档化优先",
}

# ===== 默认值 =====
DEFAULT_VALUE: Final[float] = 0.5

# ===== 学习信号（集中定义，确保一致性） =====
class Signal:
    POSITIVE: Final[float] = 1.0             # 规则被采纳
    IMPLICIT_NEGATIVE: Final[float] = -0.3   # 同查询未被点的高分规则
    DECAY: Final[float] = -0.05              # 长期未命中衰减

# ===== 冲突策略枚举值 =====
CONFLICT_STRATEGIES: Final[List[str]] = ["weighted_vote", "lexicographic"]

# ===== 分类到默认价值向量的映射（冷启动 bootstrap 用） =====
CATEGORY_VALUE_TEMPLATES: Dict[str, Dict[str, float]] = {
    "security":      {"security": 0.90, "correctness": 0.80},
    "performance":   {"efficiency": 0.90, "simplicity": 0.40},
    "architecture":  {"simplicity": 0.70, "testability": 0.70},
    "compatibility": {"compatibility": 0.90},
    "testing":       {"testability": 0.90, "correctness": 0.70},
    "documentation": {"documentation": 0.85, "simplicity": 0.70},
    # 未匹配分类 → 全 0.5 default
}


def default_value_vector() -> Dict[str, float]:
    """返回所有注册维度的默认值。"""
    return {dim: DEFAULT_VALUE for dim in VALUE_DIMENSIONS}
