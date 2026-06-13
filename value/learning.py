"""
Rulerything 4.0 — 隐式学习引擎

从用户行为隐式学习价值偏好。信号定义见 const.py Signal 类。
冷启动加速 + 线程安全 + 持久化。
"""

import time
from datetime import datetime
from threading import Lock
from typing import Dict, Optional
from .const import VALUE_DIMENSIONS, Signal


class ValueLearningEngine:
    """从用户行为隐式学习价值偏好。"""

    VALID_SIGNALS = {Signal.POSITIVE, Signal.IMPLICIT_NEGATIVE, Signal.DECAY}

    def __init__(self, config: dict, storage=None):
        self.enabled = config.get("enabled", False)
        self.lr = config.get("learning_rate", 0.05)
        raw_min = config.get("min_feedback_for_adapt", 1)
        self.min_feedback = raw_min if isinstance(raw_min, (int, float)) and raw_min >= 0 else 1
        self.optimistic_init = config.get("optimistic_init", True)
        self.exploration_epsilon = config.get("exploration_epsilon", 0.10)
        self.storage = storage  # 必须是线程安全的（WAL 模式 SQLite 满足）

    def learn_from_feedback(
        self,
        profile: "ValueProfile",
        rule_value_vector: Dict[str, float],
        signal: float,
    ) -> "ValueProfile":
        if not self.enabled:
            return profile

        # 信号范围校验
        if signal not in self.VALID_SIGNALS:
            raise ValueError(
                f"无效信号值: {signal}。有效值: {self.VALID_SIGNALS}"
            )

        # 冷启动加速
        effective_lr = self.lr * 2.0 if (self.optimistic_init and profile.learn_count < 20) else self.lr

        with profile._lock:
            for dim in VALUE_DIMENSIONS:
                current_w = profile.weights.get(dim, 0.5)
                value_dim = rule_value_vector.get(dim, 0.5)
                delta = effective_lr * (signal - current_w) * value_dim
                new_w = max(0.0, min(1.0, current_w + delta))
                profile.weights[dim] = round(new_w, 3)

            profile.learn_count += 1
            profile.updated_at = datetime.now()

            # 记录每个维度的命中时间（供 DecayTimer 使用）
            if not hasattr(profile, '_last_dimension_hit'):
                profile._last_dimension_hit = {}
            for dim in VALUE_DIMENSIONS:
                value_dim = rule_value_vector.get(dim, 0.5)
                if abs(value_dim - 0.5) > 0.1:
                    profile._last_dimension_hit[dim] = time.time()

        # 持久化（如果 storage 可用）
        if self.storage is not None:
            try:
                self.storage.save_profile(profile)
            except Exception:
                import logging
                logging.exception("保存 profile 失败，学习成果仅存在于内存中")

        return profile
