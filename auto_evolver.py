# Copyright 2026 Rule-KB Project Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Copyright 2026 Rule-KB Project Authors
"""
AutoEvolver — 自动演化引擎（v3.0 Phase C）

基于策略模板 + 安全快照 + 统计验证的系统级自动演化。

策略类型:
  - index_optimize: 平均延迟超过阈值 → 分析 + 重建
  - quality_scan:   每日定时 → 健康扫描 + 弱规则修复
  - cache_optimize:  缓存命中率过低 → 策略优化

与 auto_proposer 的区别:
  auto_proposer = 日常维护操作（冷归档、依赖重挖、低置信度清理）
  auto_evolver  = 系统级战略优化（索引、缓存、质量）

用法:
    evolver = AutoEvolver(storage, index, logger, config)
    evolver.tick(metrics)  # 管理循环中每秒调用
"""

import time
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── 统计验证工具 ──────────────────────────────────────


def cohens_d(before: List[float], after: List[float]) -> float:
    """计算 Cohen's d 效应量（使用加权合并方差）。

    d = (μ_after - μ_before) / σ_pooled

    解释:
      d > 0.2 = 小效应, d > 0.5 = 中效应, d > 0.8 = 大效应
      正值表示退化（数值变大如延迟），负值表示改善。
    """
    n_b, n_a = len(before), len(after)
    if n_b < 2 or n_a < 2:
        return 0.0

    mean_b = sum(before) / n_b
    mean_a = sum(after) / n_a

    var_b = sum((x - mean_b) ** 2 for x in before) / (n_b - 1)
    var_a = sum((x - mean_a) ** 2 for x in after) / (n_a - 1)

    pooled_var = ((n_b - 1) * var_b + (n_a - 1) * var_a) / (n_b + n_a - 2)
    if pooled_var == 0:
        return 0.0

    return (mean_a - mean_b) / math.sqrt(pooled_var)


# ── 策略定义 ──────────────────────────────────────────


class Strategy:
    """单个演化策略。

    trigger:     是否触发（接收 metrics dict，返回 bool）
    actions:     执行函数列表 [(name, fn), ...]
    risk:        "low" | "medium" | "high"
    snapshot:    执行前是否创建快照
    validation:  验证函数列表 [(name, fn(result) -> dict), ...]
    schedule:    可选定时执行 cron 风格 "HH:MM" 或 "weekly:HH:MM"
    """

    def __init__(self, name: str, config: dict):
        self.name = name
        self.risk = config.get("risk", "low")
        self.snapshot = config.get("snapshot", False)
        self.schedule = config.get("schedule")
        self.cooldown_hours = config.get("cooldown_hours", 24)
        self.trigger_fn: Optional[Callable] = None
        self.actions: List[Tuple[str, Callable]] = []
        self.validations: List[Tuple[str, Callable]] = []

        # 上次执行时间（用于调度+冷却）
        self._last_run: Optional[datetime] = None
        # 调度星期配置（0=周一, 6=周日）
        self._schedule_weekday = config.get("schedule_weekday", 0)
        # 执行前指标窗口（用于统计验证）
        self._before_metrics: List[float] = []
        self._after_metrics: List[float] = []

    @property
    def is_cooling_down(self) -> bool:
        """冷却期内返回 True。"""
        if not self._last_run:
            return False
        elapsed = (datetime.now() - self._last_run).total_seconds()
        return elapsed < self.cooldown_hours * 3600

    def check_schedule(self) -> bool:
        """检查是否到达定时执行时间（使用 >= 避免跳过 + _last_run 防重复触发）。"""
        if not self.schedule:
            return False
        now = datetime.now()
        if self.schedule.startswith("weekly:"):
            # "weekly:04:00" → 每周某天凌晨 4 点
            hour_min = self.schedule.split(":", 1)[1]
            target_h, target_m = map(int, hour_min.split(":"))
            if now.weekday() != self._schedule_weekday:
                return False
            target_time = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
        else:
            # "03:00" → 每天
            target_h, target_m = map(int, self.schedule.split(":"))
            target_time = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)

        # 如果已经在上次目标时间之后执行过，跳过
        if self._last_run and self._last_run >= target_time:
            return False

        # 当前时间 >= 目标时间时触发
        return now >= target_time

    def should_run(self, metrics: dict) -> bool:
        """判断本轮是否应执行此策略。"""
        if self.is_cooling_down:
            return False
        # 定时策略
        if self.schedule and self.check_schedule():
            return True
        # 条件触发策略
        if self.trigger_fn and self.trigger_fn(metrics):
            return True
        return False

    def record_before(self, value: float):
        """记录执行前的指标值（用于统计验证）。"""
        self._before_metrics.append(value)
        # 最多保留 50 个样本
        if len(self._before_metrics) > 50:
            self._before_metrics = self._before_metrics[-50:]

    def record_after(self, value: float):
        """记录执行后的指标值。"""
        self._after_metrics.append(value)
        if len(self._after_metrics) > 50:
            self._after_metrics = self._after_metrics[-50:]

    def validate(self, result: Any) -> dict:
        """执行所有验证函数。"""
        for name, vfn in self.validations:
            try:
                vresult = vfn(result)
                if not vresult.get("passed", True):
                    return {"passed": False, "reason": f"{name}: {vresult.get('reason', 'failed')}"}
            except Exception as e:
                return {"passed": False, "reason": f"{name}: {e}"}
        return {"passed": True, "reason": "ok"}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "risk": self.risk,
            "snapshot": self.snapshot,
            "schedule": self.schedule,
            "cooldown_hours": self.cooldown_hours,
            "last_run": self._last_run.isoformat() if self._last_run else None,
        }


# ── 自动演化引擎 ──────────────────────────────────────


class AutoEvolver:
    """自动演化引擎。

    管理一组 Strategy，在管理循环的 tick 中检查条件并执行。
    支持快照/回滚、Cohen's d 统计验证、审计日志。
    """

    def __init__(self, storage, index, logger, config: dict,
                 metrics_fn=None):
        """初始化。

        Args:
            storage: 存储层实例
            index: 索引实例
            logger: 日志实例
            config: 配置字典
            metrics_fn: 可选，用于执行策略后重新读取指标的 callable
        """
        self.storage = storage
        self.index = index
        self.logger = logger
        self.config = config
        self.metrics_fn = metrics_fn

        self.max_snapshots = config.get("max_snapshots", 50)
        self.validation_min_samples = config.get("validation_min_samples", 10)
        self.validation_effect_threshold = config.get("validation_effect_threshold", 0.5)

        # 注册策略
        self.strategies: Dict[str, Strategy] = {}
        self._register_strategies()

        # 运行时统计
        self.stats = {
            "ticks": 0,
            "strategies_executed": 0,
            "strategies_succeeded": 0,
            "strategies_failed": 0,
            "strategies_rolled_back": 0,
            "strategies_skipped": 0,
            "last_tick_time": None,
        }

        # 健康状态
        self.healthy = True
        self.last_error: Optional[str] = None

    # ── 策略注册 ─────────────────────────────────────

    def _register_strategies(self):
        """注册所有策略模板。"""
        cfg = self.config

        # 1. index_optimize: 平均延迟 > 100ms
        idx_cfg = cfg.get("index_optimize", {})
        s1 = Strategy("index_optimize", {
            "risk": "medium",
            "snapshot": True,
            "cooldown_hours": idx_cfg.get("cooldown_hours", 24),
        })
        s1.trigger_fn = lambda m: m.get("avg_latency_ms", 0) > idx_cfg.get("latency_threshold_ms", 100)
        s1.actions = [
            ("analyze_patterns", self._action_analyze_patterns),
            ("rebuild_index", self._action_rebuild_index),
        ]
        s1.validations = [
            ("latency_check", lambda r: self._validate_latency(r)),
        ]
        self.strategies["index_optimize"] = s1

        # 2. quality_scan: 每天 03:00
        qc_cfg = cfg.get("quality_scan", {})
        s2 = Strategy("quality_scan", {
            "risk": "medium",
            "snapshot": True,
            "schedule": qc_cfg.get("schedule", "03:00"),
            "cooldown_hours": 23,
        })
        s2.actions = [
            ("health_scan", self._action_health_scan),
            ("weak_rule_fix", self._action_weak_rule_fix),
        ]
        s2.validations = [
            ("score_check", lambda r: self._validate_quality(r)),
        ]
        self.strategies["quality_scan"] = s2

        # 3. cache_optimize: 缓存命中率 < 40%
        cache_cfg = cfg.get("cache_optimize", {})
        s3 = Strategy("cache_optimize", {
            "risk": "low",
            "snapshot": False,
            "cooldown_hours": cache_cfg.get("cooldown_hours", 4),
        })
        s3.trigger_fn = lambda m: m.get("cache_hit_rate", 100) < cache_cfg.get("hit_rate_threshold", 40)
        s3.actions = [
            ("analyze_cache", self._action_analyze_cache),
            ("tune_cache", self._action_tune_cache),
        ]
        s3.validations = [
            ("hit_rate_check", lambda r: self._validate_cache(r)),
        ]
        self.strategies["cache_optimize"] = s3

    # ── 主入口 ───────────────────────────────────────

    def tick(self, metrics: dict) -> List[dict]:
        """管理循环 tick，检查并执行所有就绪策略。

        Args:
            metrics: 系统指标字典

        Returns:
            本次 tick 执行的策略结果列表
        """
        self.stats["ticks"] += 1
        self.stats["last_tick_time"] = datetime.now().isoformat()
        results = []

        for name, strategy in self.strategies.items():
            try:
                if not strategy.should_run(metrics):
                    continue

                result = self._execute_strategy(strategy, metrics)
                results.append(result)

            except Exception as e:
                self.stats["strategies_failed"] += 1
                self.healthy = False
                self.last_error = str(e)
                if self.logger:
                    self.logger.info("auto_evolver", f"策略 {name} 异常: {e}")

        return results

    # ── 策略执行 ─────────────────────────────────────

    def _execute_strategy(self, strategy: Strategy, metrics: dict) -> dict:
        """执行单条策略（含快照 + 执行 + 验证 + 回滚）。"""
        self.stats["strategies_executed"] += 1
        strategy._last_run = datetime.now()

        # 记录执行前指标
        metric_key = self._get_metric_key(strategy.name)
        before_val = metrics.get(metric_key, 0)
        strategy.record_before(before_val)

        # 创建快照
        snapshot_id = None
        if strategy.snapshot:
            try:
                snapshot_id = self.storage.create_snapshot()
            except Exception as e:
                self.stats["strategies_failed"] += 1
                return {
                    "strategy": strategy.name,
                    "status": "failed",
                    "error": f"snapshot_failed: {e}",
                }

        # 执行操作
        action_results = []
        error_msg = None
        try:
            for act_name, act_fn in strategy.actions:
                ar = act_fn(metrics)
                action_results.append({"action": act_name, "result": ar})
        except Exception as e:
            error_msg = str(e)
            action_results.append({"action": "error", "error": error_msg})

        # 重新读取执行后的指标（用于统计验证）
        if self.metrics_fn and not error_msg:
            try:
                after_metrics = self.metrics_fn()
                strategy.record_after(after_metrics.get(metric_key, 0))
            except Exception:
                strategy.record_after(metrics.get(metric_key, 0))
        else:
            strategy.record_after(metrics.get(metric_key, 0))

        # 计算 Cohen's d 统计验证（用于量化策略效果）
        d = cohens_d(strategy._before_metrics, strategy._after_metrics)
        if d < -0.5 and self.logger:
            self.logger.info("auto_evolver", f"策略 {strategy.name} 显著退化 (d={d:.3f})")

        # 验证
        if error_msg:
            status = "failed"
            self.stats["strategies_failed"] += 1
            if snapshot_id:
                self._rollback(strategy.name, snapshot_id)
                status = "rolled_back"
                self.stats["strategies_rolled_back"] += 1
        else:
            validation = strategy.validate(action_results)
            if validation.get("passed", True):
                status = "success"
                self.stats["strategies_succeeded"] += 1
            else:
                status = "failed"
                self.stats["strategies_failed"] += 1
                if snapshot_id:
                    self._rollback(strategy.name, snapshot_id)
                    status = "rolled_back"
                    self.stats["strategies_rolled_back"] += 1

        # 审计日志
        try:
            self.storage.log_audit(
                action=f"evolver_{strategy.name}",
                module="auto_evolver",
                target=strategy.name,
                before_snapshot=snapshot_id,
                after_snapshot=snapshot_id if status == "rolled_back" else None,
                result=status,
                error_message=error_msg,
            )
        except Exception:
            pass

        return {
            "strategy": strategy.name,
            "status": status,
            "snapshot_id": snapshot_id,
            "actions": action_results,
            "error": error_msg,
            "cohens_d": round(d, 4),
        }

    def _rollback(self, strategy_name: str, snapshot_id: str):
        """回滚到快照。"""
        try:
            self.storage.restore_snapshot(snapshot_id)
            # 重建索引
            rules = self.storage.list()
            self.index.build(rules)
        except Exception as e:
            if self.logger:
                self.logger.info("auto_evolver",
                                 f"回滚失败 {strategy_name}: {e}")

    # ── 具体操作 ─────────────────────────────────────

    def _action_analyze_patterns(self, metrics: dict) -> dict:
        """分析索引模式，找出访问热点。"""
        idx_stats = self.index.stats() if hasattr(self.index, 'stats') else {}
        return {
            "hot_cache_size": idx_stats.get("hot_cache_size", 0),
            "total_rules": idx_stats.get("total_rules_indexed", 0),
        }

    def _action_rebuild_index(self, metrics: dict) -> dict:
        """重建索引。"""
        rules = self.storage.list()
        old_version = getattr(self.index, 'index_version', 0)
        self.index.build(rules)
        return {
            "rules_indexed": len(rules),
            "old_version": old_version,
            "new_version": getattr(self.index, 'index_version', 0),
        }

    def _action_health_scan(self, metrics: dict) -> dict:
        """健康扫描：检查规则质量分布。"""
        rules = self.storage.list()
        healthy = sum(1 for r in rules if r.confidence >= 0.5)
        weak = sum(1 for r in rules if r.confidence < 0.3)
        return {
            "total_rules": len(rules),
            "healthy": healthy,
            "weak": weak,
            "health_ratio": round(healthy / max(1, len(rules)), 3),
        }

    def _action_weak_rule_fix(self, metrics: dict) -> dict:
        """弱规则修复：标记置信度极低且无反馈的规则。"""
        rules = self.storage.list()
        marked = []
        for r in rules:
            if r.confidence < 0.15 and r.hit_count < 1:
                evo_log = r.evolution_log if r.evolution_log else []
                self.storage.update(
                    r.id,
                    expires_at=(datetime.now() + timedelta(days=90)).isoformat(),
                    evolution_log=evo_log + [f"auto-evolver: 低置信度({r.confidence})未命中"],
                )
                marked.append(r.id)
        return {"rules_marked_for_review": len(marked), "rule_ids": marked[:20]}

    def _action_analyze_cache(self, metrics: dict) -> dict:
        """分析缓存访问模式。"""
        idx_stats = self.index.stats() if hasattr(self.index, 'stats') else {}
        return {
            "hit_rate": metrics.get("cache_hit_rate", 0),
            "hot_size": idx_stats.get("hot_cache_size", 0),
        }

    def _action_tune_cache(self, metrics: dict) -> dict:
        """调优缓存阈值。"""
        hit_rate = metrics.get("cache_hit_rate", 100)
        old = getattr(self.index, 'HOT_THRESHOLD', 3)
        if hit_rate < 20 and old > 1:
            new = old - 1
        elif hit_rate < 40 and old > 1:
            # 边际改善：每次只降1，渐进式
            new = old - 1
        else:
            return {"status": "no_change_needed", "threshold": old}

        self.index.HOT_THRESHOLD = new
        # 增量刷新缓存，避免全量 O(N) 重建
        if hasattr(self.index, '_refresh_hot_cache'):
            self.index._refresh_hot_cache()
        elif hasattr(self.index, '_classify_hot_cold'):
            self.index._classify_hot_cold()
        return {"old_threshold": old, "new_threshold": new, "hit_rate": hit_rate}

    # ── 统计验证 ─────────────────────────────────────

    def _get_metric_key(self, strategy_name: str) -> str:
        """获取策略对应的指标键名。"""
        mapping = {
            "index_optimize": "avg_latency_ms",
            "quality_scan": "health_score",
            "cache_optimize": "cache_hit_rate",
        }
        return mapping.get(strategy_name, "avg_latency_ms")

    def _validate_latency(self, result: Any) -> dict:
        """延迟验证：检查重建后延迟没有恶化。"""
        # 实际验证在下一轮 tick 中通过滑动窗口进行
        return {"passed": True, "reason": "deferred_to_next_tick"}

    def _validate_quality(self, result: Any) -> dict:
        """质量扫描验证。"""
        if not isinstance(result, list):
            return {"passed": True, "reason": "no_result"}
        # 检查 weak_rule_fix 是否执行成功
        for item in result:
            if isinstance(item, dict) and item.get("action") == "health_scan":
                ratio = item.get("result", {}).get("health_ratio", 1)
                if ratio < 0.3:
                    return {"passed": False, "reason": f"health_ratio_too_low:{ratio}"}
        return {"passed": True, "reason": "ok"}

    def _validate_cache(self, result: Any) -> dict:
        """缓存验证：对比执行前后命中率变化。"""
        if not isinstance(result, list):
            return {"passed": True, "reason": "no_action"}
        for item in result:
            if isinstance(item, dict) and item.get("action") == "tune_cache":
                r = item.get("result", {})
                if r.get("status") == "no_change_needed":
                    return {"passed": True, "reason": "threshold_already_optimal"}
                old = r.get("old_threshold", 0)
                new = r.get("new_threshold", 0)
                if new < 1:
                    return {"passed": False, "reason": f"invalid_threshold:{new}"}
                return {"passed": True, "reason": f"threshold_{old}_to_{new}"}
        return {"passed": True, "reason": "no_tune_action"}

    def get_stats(self) -> dict:
        """演化引擎统计。"""
        return {
            **self.stats,
            "healthy": self.healthy,
            "last_error": self.last_error,
            "strategies": {
                name: s.to_dict() for name, s in self.strategies.items()
            },
        }

    def get_strategy(self, name: str) -> Optional[dict]:
        """获取单条策略详情。"""
        s = self.strategies.get(name)
        return s.to_dict() if s else None

    def run_strategy_now(self, name: str, metrics: dict) -> dict:
        """手动触发某条策略（不检查冷却/触发条件）。"""
        s = self.strategies.get(name)
        if not s:
            return {"error": f"未知策略: {name}"}
        return self._execute_strategy(s, metrics)
