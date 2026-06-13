"""
Rulerything 4.0 — 价值画像

支持多 profile + 预设场景。线程安全。
"""

from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Dict, List, Optional
from .const import VALUE_DIMENSIONS, DEFAULT_VALUE, CONFLICT_STRATEGIES


@dataclass
class ValueProfile:
    """用户价值画像 — 支持多 profile + 预设场景。"""
    name: str
    weights: Dict[str, float] = field(default_factory=dict)
    priority_order: List[str] = field(default_factory=list)
    conflict_strategy: str = "weighted_vote"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    learn_count: int = 0

    # 内置锁，确保并发安全
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)
    # 优化：权重完整性标记，避免 ensure_weights() 重复遍历
    _weights_complete: bool = False
    # 各维度最后命中时间（DecayTimer 使用）
    _last_dimension_hit: Dict[str, float] = field(default_factory=dict, repr=False, compare=False)

    def ensure_weights(self):
        """确保所有维度都有值，缺失的用默认值填充。仅首次调用时遍历。"""
        if self._weights_complete:
            return
        for dim in VALUE_DIMENSIONS:
            if dim not in self.weights:
                self.weights[dim] = DEFAULT_VALUE
        self._weights_complete = True

    def invalidate_weights(self):
        """标记权重需重新校验（用于外部修改 weights 后）。"""
        self._weights_complete = False

    def validate(self) -> List[str]:
        """返回配置错误列表。空列表 = 配置有效。"""
        errors = []
        if self.conflict_strategy not in CONFLICT_STRATEGIES:
            errors.append(f"未知冲突策略: {self.conflict_strategy}，可选: {CONFLICT_STRATEGIES}")
        for dim in self.priority_order:
            if dim not in VALUE_DIMENSIONS:
                errors.append(f"priority_order 包含未注册维度: {dim}")
        return errors


def load_profiles(config: dict, storage=None) -> Dict[str, ValueProfile]:
    """
    从配置加载预定义画像，如果 storage 中有持久化版本则覆盖。

    返回 {profile_name: ValueProfile} 字典。
    """
    profiles_config = config.get("profiles", {})
    profiles = {}

    for name, cfg in profiles_config.items():
        weights = dict(cfg.get("weights", {}))
        profile = ValueProfile(
            name=name,
            weights=weights,
            priority_order=list(cfg.get("priority_order", [])),
            conflict_strategy=cfg.get("conflict_strategy", "weighted_vote"),
        )
        profile.ensure_weights()
        profiles[name] = profile

    # 尝试从 storage 加载持久化画像（覆盖配置默认值）
    if storage is not None:
        try:
            persisted = storage.list_profiles()
            for p in persisted:
                if p.name in profiles:
                    # 保留配置中的结构，仅覆盖 weights 和学习统计
                    profiles[p.name].weights = p.weights
                    profiles[p.name].learn_count = p.learn_count
                    profiles[p.name].updated_at = p.updated_at
                    profiles[p.name].invalidate_weights()
                    profiles[p.name].ensure_weights()
                else:
                    profiles[p.name] = p
        except Exception:
            import logging
            logging.exception("加载持久化画像失败，仅使用配置预设")

    return profiles
