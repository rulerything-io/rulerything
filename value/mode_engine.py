"""
Rulerything 4.0 — 部署模式引擎

控制 4.0 价值层流量分配：
- 判断 3.0/4.0 路径
- 静默对比
- 灰度分配
- 自动回滚
"""

import hashlib
import logging
import time
from enum import Enum
from typing import Dict, List, Optional, Tuple


class DeployMode(Enum):
    OFF = "off"
    SHADOW = "shadow"
    DUAL_WRITE = "dual_write"
    GRAYSCALE = "grayscale"
    FULL = "full"


class ModeEngine:
    """部署模式引擎 — 控制 4.0 价值层流量分配。"""

    def __init__(self, config: dict, storage):
        self.mode = DeployMode(config.get("mode", "off"))
        self.grayscale_config = config.get("grayscale", {})
        self.rollback_config = config.get("auto_rollback", {})
        self.storage = storage

        # 统计窗口
        self._error_window: List[Tuple[float, bool]] = []
        self._latency_window: List[Tuple[float, float]] = []
        # 基线从 shadow/dual_write 阶段自动采集
        self._baseline_error_rate: Optional[float] = None
        self._baseline_p99_latency: Optional[float] = None
        self._baseline_sample_count = 0
        self._baseline_min_samples = 1000  # 最少样本数才启用自动回滚

    def should_use_value_engine(
        self, session_id: Optional[str] = None
    ) -> Tuple[bool, bool]:
        """
        返回 (use_value_sorting, should_collect_signals)。

        use_value_sorting:   True → 4.0 路径（返回排序结果 + decision_trace）
        should_collect_signals: True → 采集学习信号（即使 3.0 路径）
        """
        if self.mode == DeployMode.OFF:
            return False, False
        if self.mode == DeployMode.SHADOW:
            return False, False  # 静默运行，对比日志
        if self.mode == DeployMode.DUAL_WRITE:
            return False, True   # 返回 3.0 结果，但采集学习信号
        if self.mode == DeployMode.GRAYSCALE:
            if session_id is None:
                return False, True
            return self._assign_grayscale(session_id), True
        if self.mode == DeployMode.FULL:
            return True, True
        return False, False

    def _assign_grayscale(self, session_id: str) -> bool:
        """确定性哈希分配 session → 3.0 或 4.0。"""
        percent = self.grayscale_config.get("percent", 5)
        h = hashlib.md5(session_id.encode()).hexdigest()
        bucket = int(h[:2], 16)  # 0-255
        return bucket < int(percent / 100 * 256)

    def record_result(self, is_error: bool, latency_ms: float):
        """记录每次请求结果。shadow/dual_write 阶段自动收集基线。"""
        now = time.time()
        self._error_window.append((now, is_error))
        self._latency_window.append((now, latency_ms))
        cutoff = now - 3600
        self._error_window = [(t, e) for t, e in self._error_window if t > cutoff]
        self._latency_window = [(t, l) for t, l in self._latency_window if t > cutoff]

        # shadow/dual_write 阶段自动采集基线
        if self.mode in (DeployMode.SHADOW, DeployMode.DUAL_WRITE):
            self._baseline_sample_count += 1
            if self._baseline_sample_count >= self._baseline_min_samples:
                recent = [l for t, l in self._latency_window]
                if recent:
                    self._baseline_p99_latency = sorted(recent)[int(len(recent) * 0.99)]
                errors = [e for t, e in self._error_window]
                self._baseline_error_rate = sum(errors) / len(errors) if errors else 0.0
                if self._baseline_p99_latency and self._baseline_error_rate is not None:
                    logging.info(
                        f"[ModeEngine] 基线采集完成: p99={self._baseline_p99_latency:.1f}ms, "
                        f"err_rate={self._baseline_error_rate:.4f}"
                    )

    def should_auto_rollback(self) -> Tuple[bool, str]:
        """检查是否应触发自动回滚。返回 (should_rollback, reason)。"""
        if not self.rollback_config.get("enabled", False):
            return False, ""
        if self.mode in (DeployMode.OFF, DeployMode.SHADOW):
            return False, ""
        # 基线未就绪 → 不触发
        if self._baseline_error_rate is None or self._baseline_p99_latency is None:
            return False, ""

        recent_errors = [e for t, e in self._error_window if t > time.time() - 300]
        if recent_errors:
            error_rate = sum(recent_errors) / len(recent_errors)
            threshold = self.rollback_config.get("error_rate_threshold", 2.0)
            if self._baseline_error_rate > 0 and error_rate > self._baseline_error_rate * threshold:
                return True, f"错误率 {error_rate:.2%} > 基线 {self._baseline_error_rate:.2%} × {threshold}"

        recent_lat = [l for t, l in self._latency_window if t > time.time() - 300]
        if recent_lat and self._baseline_p99_latency and self._baseline_p99_latency > 0:
            p99 = sorted(recent_lat)[int(len(recent_lat) * 0.99)]
            threshold = self.rollback_config.get("p99_latency_threshold", 1.5)
            if p99 > self._baseline_p99_latency * threshold:
                return True, f"p99 延迟 {p99:.1f}ms > 基线 {self._baseline_p99_latency:.1f}ms × {threshold}"

        return False, ""

    def get_rollback_config(self) -> dict:
        """返回回滚配置中指定的回退模式。"""
        return self.rollback_config

    def status_dict(self) -> dict:
        """返回模式引擎状态信息。"""
        return {
            "mode": self.mode.value,
            "baseline_ready": self._baseline_error_rate is not None,
            "baseline_samples": self._baseline_sample_count,
            "baseline_p99_ms": round(self._baseline_p99_latency, 1) if self._baseline_p99_latency else None,
            "baseline_error_rate": round(self._baseline_error_rate, 4) if self._baseline_error_rate is not None else None,
            "recent_requests_1h": len(self._latency_window),
        }
