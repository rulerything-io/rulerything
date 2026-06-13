"""
Rulerything 4.0 — 信号衰减定时器

后台线程对长期未命中的维度施加 Signal.DECAY (−0.05)。
仅衰减 last_hit > half_life 的维度，活跃维度不受影响。
"""

import time
import threading
from typing import Dict, Optional


class DecayTimer:
    """
    后台线程对长期未命中的维度施加 Signal.DECAY (−0.05)。
    仅衰减 last_hit > half_life 的维度，活跃维度不受影响。
    """

    def __init__(self, learning_config: dict, learning_engine, profiles: dict):
        self.enabled = learning_config.get("enabled", False)
        self.half_life = learning_config.get("decay_half_life", 86400)  # 默认 24h
        self._engine = learning_engine
        self._profiles = profiles  # 从 ValueEngine 传入，遍历所有已加载画像
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """启动后台衰减线程。"""
        if not self.enabled or self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止后台衰减线程。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self):
        interval = max(60, self.half_life / 10)  # 最少 60 秒
        while not self._stop_event.wait(interval):
            self._apply_decay()

    def _apply_decay(self):
        """对长期未命中的维度施加衰减信号。

        关键修复：仅衰减 last_hit 超过 half_life 的维度。
        活跃维度不受影响——消除"刚被点击就被衰减"的竞态。
        """
        from .const import Signal
        now = time.time()
        for profile in self._profiles.values():
            last_hit = getattr(profile, '_last_dimension_hit', {})
            for dim, weight in list(profile.weights.items()):
                if weight <= 0.5:
                    continue
                # 仅衰减超过半衰期未被命中的维度
                if dim in last_hit and (now - last_hit[dim]) < self.half_life:
                    continue
                dummy_vector = {d: 0.5 for d in profile.weights}
                dummy_vector[dim] = 1.0
                try:
                    self._engine.learn_from_feedback(
                        profile, dummy_vector, Signal.DECAY
                    )
                except Exception:
                    import logging
                    logging.exception(f"Decay failed for {profile.name}/{dim}")
