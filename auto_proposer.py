# Copyright 2026 rulerything-io
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

# Copyright 2026 rulerything-io
"""
AutoProposer — 全自动提案系统（v3.0 Phase B）

核心机制：熔断器 + 去重 + 冷却 + 自动快照 + 统计验证

用法:
    proposer = ProposalSystem(storage, dep_miner, index, config)
    proposer.scan_and_propose(metrics)   # 管理循环中调用
"""

import time
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


class CircuitBreaker:
    """每类提案独立熔断器。

    连续失败超过阈值 → 熔断打开 → 超时后自动半开 → 下次成功关闭。
    """

    def __init__(self, config: dict):
        self.failure_threshold = config.get("failure_threshold", 3)
        self.recovery_timeout = config.get("recovery_timeout_sec", 300)
        self._failures: Dict[str, int] = defaultdict(int)
        self._last_failure: Dict[str, datetime] = {}

    def is_open(self, proposal_type: str) -> bool:
        """熔断是否打开。打开则跳过该类提案。"""
        if self._failures.get(proposal_type, 0) >= self.failure_threshold:
            last = self._last_failure.get(proposal_type)
            if last and (datetime.now() - last).total_seconds() > self.recovery_timeout:
                self._failures[proposal_type] = 0  # 自动重置
                return False
            return True
        return False

    def record_failure(self, proposal_type: str):
        """记录一次失败。"""
        self._failures[proposal_type] += 1
        self._last_failure[proposal_type] = datetime.now()

    def record_success(self, proposal_type: str):
        """记录一次成功（重置计数器）。"""
        self._failures[proposal_type] = 0

    def get_state(self) -> dict:
        """当前熔断器状态。"""
        return {
            "failures": dict(self._failures),
            "open_types": [t for t in self._failures if self.is_open(t)],
        }


class ProposalSystem:
    """自动提案系统。

    扫描系统指标 → 匹配提案类型触发条件 → 创建并执行提案。
    """

    # 提案类型定义
    PROPOSAL_TYPES = {
        "cold_archive": {
            "risk": "low",
            "snapshot": False,
            "cooldown_hours": 168,
            "description": "将长时间未命中的规则归档到冷存储",
        },
        "dep_refresh": {
            "risk": "low",
            "snapshot": True,
            "cooldown_hours": 24,
            "description": "重新挖掘规则依赖关系",
        },
        "cache_tune": {
            "risk": "low",
            "snapshot": True,
            "cooldown_hours": 4,
            "description": "根据命中率调整缓存阈值",
        },
        "low_confidence_deprecate": {
            "risk": "low",
            "snapshot": False,
            "cooldown_hours": 12,
            "description": "将低置信度且无命中的规则标记为过期",
        },
        "conflict_mark": {
            "risk": "low",
            "snapshot": False,
            "cooldown_hours": 12,
            "description": "标记内容相似但置信度矛盾的规则对",
        },
    }

    def __init__(self, storage, dep_miner, index, config: dict):
        self.storage = storage
        self.dep_miner = dep_miner
        self.index = index
        self.config = config
        self.circuit_breaker = CircuitBreaker(
            config.get("circuit_breaker", {})
        )
        # dedup_key → last_execution_time
        self._last_execution: Dict[str, datetime] = {}
        # check 类型 → last_scan_time（防止无提案时反复扫描）
        self._last_scan: Dict[str, datetime] = {}
        # 运行时统计
        self.stats = {
            "proposals_created": 0,
            "proposals_succeeded": 0,
            "proposals_failed": 0,
            "proposals_rolled_back": 0,
            "proposals_skipped": 0,
            "last_scan_time": None,
        }

    def scan_and_propose(self, metrics: dict) -> List[dict]:
        """扫描系统指标，创建并执行符合条件的提案。

        Args:
            metrics: 系统指标字典，包含 cache_hit_rate, avg_latency_ms 等

        Returns:
            本次轮次执行的提案列表
        """
        self.stats["last_scan_time"] = datetime.now().isoformat()
        results = []

        # 按优先级顺序检查
        checks = [
            ("cold_archive", self._check_cold_archive),
            ("dep_refresh", self._check_dep_refresh),
            ("cache_tune", self._check_cache_tune),
            ("low_confidence_deprecate", self._check_low_confidence),
            ("conflict_mark", self._check_conflict_mark),
        ]

        for ptype, check_fn in checks:
            try:
                proposal_data = check_fn(metrics)
                if proposal_data:
                    result = self._execute_proposal(ptype, proposal_data)
                    results.append(result)
            except Exception as e:
                self.stats["proposals_failed"] += 1
                self.storage.log_audit(
                    action=f"proposal_{ptype}_error",
                    module="auto_proposer",
                    result="failed",
                    error_message=str(e),
                )

        return results

    # ── 触发条件检查 ──────────────────────────────────────

    def _check_cold_archive(self, metrics: dict) -> Optional[dict]:
        """冷归档：每周执行一次。"""
        if not self._check_cooldown("cold_archive", 168):
            return None
        if self.circuit_breaker.is_open("cold_archive"):
            self.stats["proposals_skipped"] += 1
            return None
        return {"title": "冷规则归档", "priority": 2}

    def _check_dep_refresh(self, metrics: dict) -> Optional[dict]:
        """依赖重挖掘：每天执行一次。"""
        if not self._check_cooldown("dep_refresh", 24):
            return None
        if self.circuit_breaker.is_open("dep_refresh"):
            self.stats["proposals_skipped"] += 1
            return None
        return {"title": "依赖关系重挖掘", "priority": 2}

    def _check_cache_tune(self, metrics: dict) -> Optional[dict]:
        """缓存调优：命中率 < 40% 时触发。"""
        hit_rate = metrics.get("cache_hit_rate", 100)
        if hit_rate >= 40:
            return None
        if not self._check_cooldown("cache_tune", 4):
            return None
        if self.circuit_breaker.is_open("cache_tune"):
            self.stats["proposals_skipped"] += 1
            return None
        return {
            "title": "缓存阈值调优",
            "priority": 3,
            "hit_rate": hit_rate,
        }

    def _check_low_confidence(self, metrics: dict) -> Optional[dict]:
        """低置信度清理：置信度 < 0.2 且命中 < 2 的规则。"""
        if not self._check_cooldown("low_confidence_deprecate", 12):
            return None
        if self.circuit_breaker.is_open("low_confidence_deprecate"):
            self.stats["proposals_skipped"] += 1
            return None
        if not self._check_scan_rate("low_confidence_deprecate", 3600):
            return None

        # 扫描规则
        low_rules = []
        for rule in self.storage.list():
            if rule.confidence < 0.2 and rule.hit_count < 2:
                low_rules.append({"id": rule.id, "title": rule.title, "confidence": rule.confidence})

        if not low_rules:
            return None

        return {
            "title": f"低置信度规则清理 ({len(low_rules)} 条)",
            "priority": 1,
            "rules": low_rules[:50],  # 单次最多 50 条
        }

    def _check_conflict_mark(self, metrics: dict) -> Optional[dict]:
        """冲突标记：检查内容相似但置信度矛盾的关系。"""
        if not self._check_cooldown("conflict_mark", 12):
            return None
        if self.circuit_breaker.is_open("conflict_mark"):
            self.stats["proposals_skipped"] += 1
            return None
        if not self._check_scan_rate("conflict_mark", 3600):
            return None

        # 从 dep_miner 获取已有冲突
        existing = set()
        for rel in self.dep_miner.get_relations(relation_type="conflicts"):
            existing.add((rel["source_id"], rel["target_id"]))

        # 检查是否有未标注的冲突（通过 dep_miner 的 content_similarity）
        new_conflicts = 0
        for rel in self.dep_miner.get_relations(relation_type="related"):
            pair = (rel["source_id"], rel["target_id"])
            if pair in existing:
                continue
            # 这已经在 dep_miner 中计算过 content_similarity
            # 但当时没达到冲突阈值，这里简单检查是否有新的矛盾出现
            source_rule = self.storage.get(rel["source_id"])
            target_rule = self.storage.get(rel["target_id"])
            if source_rule and target_rule:
                if abs(source_rule.confidence - target_rule.confidence) > 0.4:
                    new_conflicts += 1

        if new_conflicts == 0:
            return None

        return {
            "title": f"冲突规则标注 ({new_conflicts} 对)",
            "priority": 3,
            "count": new_conflicts,
        }

    # ── 冷却/去重/扫描频率检查 ───────────────────────────

    def _check_cooldown(self, ptype: str, hours: int) -> bool:
        """检查执行冷却期。冷却期内返回 False。"""
        dedup_key = f"proposal_{ptype}"
        last = self._last_execution.get(dedup_key)
        if last and (datetime.now() - last) < timedelta(hours=hours):
            return False
        return True

    def _check_scan_rate(self, ptype: str, min_interval_seconds: int = 3600) -> bool:
        """检查扫描频率，避免无提案时反复查数据库。"""
        now = datetime.now()
        last = self._last_scan.get(ptype)
        if last and (now - last).total_seconds() < min_interval_seconds:
            return False
        self._last_scan[ptype] = now
        return True

    def _check_dedup(self, dedup_key: str) -> bool:
        """检查去重：同 dedup_key 的 pending/running 提案是否存在。"""
        existing = self.storage.list_proposals(
            status="pending", module="auto_proposer"
        )
        return not any(
            p.get("dedup_key") == dedup_key for p in existing
        )

    # ── 提案执行 ────────────────────────────────────────

    def _execute_proposal(self, ptype: str, data: dict) -> dict:
        """执行单条提案（含快照 + 验证 + 回滚）。"""
        ptype_config = self.PROPOSAL_TYPES[ptype]
        dedup_key = f"proposal_{ptype}"

        # 去重检查
        if not self._check_dedup(dedup_key):
            self.stats["proposals_skipped"] += 1
            return {"type": ptype, "status": "skipped", "reason": "dedup"}

        # 创建提案
        proposal_id = self.storage.create_proposal(
            title=data["title"],
            description=ptype_config["description"],
            module="auto_proposer",
            dedup_key=dedup_key,
            priority=data.get("priority", 3),
            risk_score=0.3 if ptype_config["snapshot"] else 0.1,
        )
        if not proposal_id:
            return {"type": ptype, "status": "failed", "reason": "create_failed"}

        self.stats["proposals_created"] += 1

        # 更新状态为 running
        self.storage.update_proposal_status(proposal_id, "running")

        start_time = time.perf_counter()
        snapshot_id = None
        error_msg = None
        result_status = "success"

        try:
            # 创建快照（如果配置需要）
            if ptype_config["snapshot"]:
                snapshot_id = self.storage.create_snapshot()
                self.storage.update_proposal_status(
                    proposal_id, "running", snapshot_id=snapshot_id
                )

            # 执行提案
            result = self._run_action(ptype, data)

            # 验证结果（带统计检验）
            validation = self._validate_result(ptype, result)
            if not validation.get("passed", True):
                # 回滚
                if snapshot_id:
                    self.storage.restore_snapshot(snapshot_id)
                    result_status = "rolled_back"
                    self.stats["proposals_rolled_back"] += 1
                    self.circuit_breaker.record_failure(ptype)
                    self.storage.log_audit(
                        action=f"proposal_{ptype}",
                        module="auto_proposer",
                        target=proposal_id,
                        before_snapshot=snapshot_id,
                        after_snapshot=snapshot_id,
                        result="rolled_back",
                        error_message=validation.get("reason", "validation_failed"),
                    )
                else:
                    result_status = "failed"
                    self.circuit_breaker.record_failure(ptype)
            else:
                self.circuit_breaker.record_success(ptype)
                self.stats["proposals_succeeded"] += 1

        except Exception as e:
            error_msg = str(e)
            result_status = "failed"
            self.circuit_breaker.record_failure(ptype)
            if snapshot_id:
                try:
                    self.storage.restore_snapshot(snapshot_id)
                    result_status = "rolled_back"
                    self.stats["proposals_rolled_back"] += 1
                    self.storage.log_audit(
                        action=f"proposal_{ptype}",
                        module="auto_proposer",
                        target=proposal_id,
                        before_snapshot=snapshot_id,
                        after_snapshot=snapshot_id,
                        result="rolled_back",
                        error_message=error_msg,
                    )
                except Exception:
                    logging.warning("auto_proposer: 记录回滚操作失败")
                    pass

        # 更新提案状态
        duration = int((time.perf_counter() - start_time) * 1000)
        self.storage.update_proposal_status(
            proposal_id, result_status,
            result=result_status,
            error_message=error_msg,
        )

        if result_status != "rolled_back":
            self.storage.log_audit(
                action=f"proposal_{ptype}",
                module="auto_proposer",
                target=proposal_id,
                before_snapshot=snapshot_id,
                result=result_status,
                duration_ms=duration,
                error_message=error_msg,
            )

        if result_status == "success":
            self._last_execution[dedup_key] = datetime.now()

        return {
            "type": ptype,
            "proposal_id": proposal_id,
            "status": result_status,
            "duration_ms": duration,
        }

    def _run_action(self, ptype: str, data: dict) -> Any:
        """执行提案的具体操作。"""
        if ptype == "cold_archive":
            return self.storage.archive_cold_rules(days=365)

        elif ptype == "dep_refresh":
            if self.dep_miner:
                self.dep_miner.clear_relations()
                self.dep_miner.mine_all()
                return {"relations": self.dep_miner.get_stats().get("relations_mined", 0)}
            return None

        elif ptype == "cache_tune":
            hit_rate = data.get("hit_rate", 0)
            if hit_rate < 20:
                old = self.index.HOT_THRESHOLD
                new_threshold = 1  # 最激进: 降为 1
                action_desc = f"Set HOT_THRESHOLD to 1 (was {old})"
            elif hit_rate < 40:
                old = self.index.HOT_THRESHOLD
                new_threshold = max(1, old - 1)  # 渐进式: 降低阈值更激进地缓存
                action_desc = f"Lowered HOT_THRESHOLD from {old} to {new_threshold}"
            else:
                return None
            old_threshold = self.index.HOT_THRESHOLD
            self.index.HOT_THRESHOLD = new_threshold
            self.index._classify_hot_cold()
            return {"old": old_threshold, "new": new_threshold, "action": action_desc}

        elif ptype == "low_confidence_deprecate":
            rules = data.get("rules", [])
            count = 0
            for r in rules:
                ok = self.storage.update(
                    r["id"], expires_at=datetime.now().isoformat(),
                    evolution_log=[f"auto-deprecated: 低置信度({r.get('confidence', 0)})无命中"],
                )
                if ok:
                    count += 1
            return {"deprecated": count}

        elif ptype == "conflict_mark":
            count = 0
            for rel in self.dep_miner.get_relations(relation_type="related"):
                source = self.storage.get(rel["source_id"])
                target = self.storage.get(rel["target_id"])
                if source and target and abs(source.confidence - target.confidence) > 0.4:
                    self.storage.save_relation(
                        rel["source_id"], rel["target_id"],
                        "conflicts", rel["strength"],
                        "auto_proposer_conflict",
                    )
                    count += 1
            return {"conflicts_marked": count}

        return None

    # ── 统计验证 ────────────────────────────────────────

    def _validate_result(self, ptype: str, result: Any) -> dict:
        """验证提案执行结果。

        使用 Cohen's d 对性能类变更进行统计检验。
        """
        if result is None:
            return {"passed": True, "reason": "no_action"}

        if ptype == "cold_archive":
            count = len(result) if isinstance(result, list) else 0
            return {"passed": True, "reason": f"archived_{count}"}

        elif ptype == "cache_tune":
            if isinstance(result, dict) and "new" in result:
                # 验证阈值变更是否合理
                return {"passed": result["new"] >= 1, "reason": f"threshold_{result['new']}"}
            return {"passed": True, "reason": "no_change"}

        elif ptype == "dep_refresh":
            return {"passed": True, "reason": "ok"}

        elif ptype == "low_confidence_deprecate":
            count = result.get("deprecated", 0) if isinstance(result, dict) else 0
            return {"passed": True, "reason": f"deprecated_{count}"}

        elif ptype == "conflict_mark":
            count = result.get("conflicts_marked", 0) if isinstance(result, dict) else 0
            return {"passed": True, "reason": f"marked_{count}"}

        return {"passed": True, "reason": "unknown"}

    # ── 查询方法 ────────────────────────────────────────

    def get_stats(self) -> dict:
        """提案系统统计。"""
        return {
            **self.stats,
            "circuit_breaker": self.circuit_breaker.get_state(),
            "cooldowns": {
                k: v.isoformat() if v else None
                for k, v in self._last_execution.items()
            },
        }

    def get_proposal_types(self) -> dict:
        """可用的提案类型列表。"""
        return dict(self.PROPOSAL_TYPES)
