"""
自进化引擎 — 基于反馈驱动的保守进化系统

核心原则：每次只改一个方面，version +1，evolution_log 可追溯，支持回滚。
"""

import json
import os
import shutil
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

from rule import Rule


class EvolutionType(str, Enum):
    """进化类型枚举。"""
    CONFIDENCE_ADJUST = "confidence_adjust"   # 根据反馈调整置信度
    CONTENT_REFINE = "content_refine"         # 细化规则内容
    SPLIT_RULE = "split_rule"                 # 拆分为多条规则
    MERGE_RULES = "merge_rules"               # 合并相关规则
    DEPRECATE_RULE = "deprecate_rule"         # 标记过时
    ADD_EXAMPLE = "add_example"               # 增加示例

    def __str__(self):
        return self.value


@dataclass
class EvolutionRecord:
    """单次进化记录。"""
    rule_id: str
    evolution_type: str
    old_confidence: float
    new_confidence: float
    old_content: str
    new_content: str
    trigger_reason: str
    timestamp: str
    version: int

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class EvolutionError(Exception):
    """进化引擎异常。"""
    pass


class EvolutionEngine:
    """自进化引擎。

    用法：
        engine = EvolutionEngine(storage, index, logger)
        engine.collect_feedback("python/001", False, "太宽泛，不够具体")
        changes = engine.apply_pending_evolutions(dry_run=True)  # 预览
        changes = engine.apply_pending_evolutions()               # 执行
    """

    # 反馈上下文关键词 → 进化类型映射
    CONTEXT_RULES: List[Tuple[List[str], EvolutionType, Dict]] = [
        (["太宽泛", "不够具体", "太笼统", "不精确"], EvolutionType.SPLIT_RULE,
         {"hint": "规则过于宽泛，建议拆分为多条具体规则"}),
        (["有例外", "特殊情况", "不考虑", "忽略"], EvolutionType.CONTENT_REFINE,
         {"add_condition": True}),
        (["过时", "已淘汰", "不再适用", "旧版本"], EvolutionType.DEPRECATE_RULE,
         {}),
        (["示例", "例子", "不够", "不明白", "看不懂"], EvolutionType.ADD_EXAMPLE,
         {}),
        (["重复", "和另一条", "和XX相同", "冗余"], EvolutionType.MERGE_RULES,
         {}),
    ]

    def __init__(self, storage, index=None, logger=None,
                 data_dir: str = "data"):
        self.storage = storage
        self.index = index
        self.logger = logger
        self.data_dir = Path(data_dir)

        self.feedback_path = self.data_dir / "feedback.jsonl"
        self.archive_dir = self.data_dir / "archived"
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        # 待处理的进化队列: [(EvolutionType, rule_id, params)]
        self._pending: List[Tuple[EvolutionType, str, dict]] = []

    # ── 反馈收集 ─────────────────────────────────────

    def collect_feedback(self, rule_id: str, user_satisfied: bool,
                         context: str = ""):
        """收集用户反馈并调度进化。

        Args:
            rule_id: 规则 ID
            user_satisfied: 用户是否满意
            context: 用户反馈文本（用于触发进化调度）
        """
        feedback = {
            "rule_id": rule_id,
            "satisfied": user_satisfied,
            "context": context,
            "timestamp": datetime.now().isoformat(),
        }
        self._save_feedback(feedback)

        if self.logger:
            self.logger.info(
                "feedback",
                f"{'满意' if user_satisfied else '不满意'}: {rule_id}",
                rule_id=rule_id, satisfied=user_satisfied, context=context,
            )

        # 不满意时调度进化
        if not user_satisfied:
            self._schedule_evolution(rule_id, feedback)

    def _save_feedback(self, feedback: dict):
        """追加反馈到 JSONL 文件。"""
        with open(self.feedback_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(feedback, ensure_ascii=False) + "\n")

    # ── 进化调度 ─────────────────────────────────────

    def _schedule_evolution(self, rule_id: str, feedback: dict):
        """根据反馈内容匹配进化类型。"""
        rule = self.storage.get(rule_id)
        if rule is None:
            if self.logger:
                self.logger.warn("evolution", f"规则 {rule_id} 不存在，无法调度进化")
            return

        context = feedback.get("context", "").lower()

        # 遍历关键词规则
        for keywords, evo_type, params in self.CONTEXT_RULES:
            if any(kw in context for kw in keywords):
                self._pending.append((evo_type, rule_id, params))
                if self.logger:
                    self.logger.info(
                        "evolution",
                        f"调度进化: {rule_id} → {evo_type.value}",
                        rule_id=rule_id, evolution_type=evo_type.value,
                        reason=context,
                    )
                return

        # 默认：降低置信度
        self._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, rule_id, {"delta": -0.1})
        )
        if self.logger:
            self.logger.info(
                "evolution",
                f"调度进化(默认): {rule_id} → confidence_adjust",
                rule_id=rule_id, evolution_type="confidence_adjust",
                reason=context or "默认降权",
            )

    # ── 待处理队列 ───────────────────────────────────

    @property
    def pending_count(self) -> int:
        """待处理的进化数量。"""
        return len(self._pending)

    @property
    def pending_evolutions(self) -> List[dict]:
        """查看待处理进化列表（只读）。"""
        return [
            {"evolution_type": et.value, "rule_id": rid, "params": p}
            for et, rid, p in self._pending
        ]

    def clear_pending(self):
        """清空待处理队列。"""
        self._pending.clear()

    # ── Phase 2: 免疫系统集成 ───────────────────────

    def integrate_immune_system(self, immune_system):
        """与免疫系统集成：自动将抗体转为进化任务。"""
        for rule_id, antibodies in immune_system.b_cells.items():
            rule = self.storage.get(rule_id)
            if rule:
                for antibody in antibodies[:1]:  # 取第一条建议
                    self._pending.append((
                        EvolutionType.CONTENT_REFINE,
                        rule_id,
                        {"hint": antibody},
                    ))

    # ── 应用进化 ─────────────────────────────────────

    def dry_run(self) -> List[dict]:
        """预览所有待处理的进化（不执行）。"""
        return self._apply(True)

    def apply_pending_evolutions(self, dry_run: bool = False) -> List[dict]:
        """执行所有待处理的进化。

        Args:
            dry_run: True=仅预览，False=执行

        Returns:
            变更记录列表
        """
        changes = self._apply(dry_run)

        if not dry_run:
            self._pending.clear()

        return changes

    def _apply(self, dry_run: bool) -> List[dict]:
        """内部：遍历 pending 队列执行进化。"""
        changes = []

        for evo_type, rule_id, params in self._pending:
            rule = self.storage.get(rule_id)
            if rule is None:
                continue

            try:
                record = self._apply_one(rule, evo_type, params, dry_run)
                if record:
                    changes.append(record.to_dict())
            except EvolutionError as e:
                if self.logger:
                    self.logger.error(
                        component="evolution_engine",
                        error_type="evolution_error",
                        message=str(e),
                    )

        if not dry_run and changes:
            # 保存变更
            self._save_all()
            # 重建索引
            if self.index:
                self.index.build(self.storage.list())

        return changes

    def _apply_one(self, rule: Rule, evo_type: EvolutionType,
                   params: dict, dry_run: bool) -> Optional[EvolutionRecord]:
        """对单条规则执行一次进化。"""
        # 兼容字符串类型的进化类型
        if isinstance(evo_type, str):
            try:
                evo_type = EvolutionType(evo_type)
            except ValueError:
                raise EvolutionError(f"未知进化类型: {evo_type}")

        old_content = rule.content
        old_confidence = rule.confidence
        new_content = old_content
        new_confidence = old_confidence
        trigger_reason = params.get("hint", evo_type.value)

        if evo_type == EvolutionType.CONFIDENCE_ADJUST:
            delta = params.get("delta", -0.05)
            new_confidence = max(0.0, min(1.0, old_confidence + delta))

        elif evo_type == EvolutionType.CONTENT_REFINE:
            condition = params.get("hint", "存在例外情况")
            new_content = old_content + f"\n\n> 注意：{condition}"

        elif evo_type == EvolutionType.SPLIT_RULE:
            # 降低原规则置信度（标记待人工拆分）
            new_confidence = old_confidence * 0.7
            new_content = old_content + (
                f"\n\n---\n[待拆分] 此规则包含多个独立概念，"
                f"建议拆分为多条规则。{params.get('hint', '')}"
            )

        elif evo_type == EvolutionType.MERGE_RULES:
            new_confidence = old_confidence * 0.8
            hint = params.get("hint", "")
            new_content = old_content + (
                f"\n\n---\n[待合并] {hint}"
            )

        elif evo_type == EvolutionType.DEPRECATE_RULE:
            new_confidence = 0.0
            new_content = old_content + (
                f"\n\n---\n[已过时] {params.get('reason', '此规则已不再适用')}"
            )

        elif evo_type == EvolutionType.ADD_EXAMPLE:
            new_content = old_content + "\n\n示例：待补充"

        else:
            raise EvolutionError(f"未知进化类型: {evo_type}")

        # 构建记录
        record = EvolutionRecord(
            rule_id=rule.id,
            evolution_type=evo_type.value,
            old_confidence=old_confidence,
            new_confidence=new_confidence,
            old_content=old_content,
            new_content=new_content,
            trigger_reason=trigger_reason,
            timestamp=datetime.now().isoformat(),
            version=rule.version + (0 if dry_run else 1),
        )

        if not dry_run:
            # 归档旧版本
            self._archive_version(rule)
            # 应用变更
            rule.content = new_content
            rule.confidence = new_confidence
            if evo_type == EvolutionType.DEPRECATE_RULE:
                rule.expires_at = datetime.now()
            rule.evolve(
                f"v{rule.version}: {evo_type.value} — {trigger_reason}"
            )

            if self.logger:
                self.logger.evolution(
                    rule_id=rule.id,
                    evolution_type=evo_type.value,
                    old_confidence=old_confidence,
                    new_confidence=new_confidence,
                    trigger_reason=trigger_reason,
                )

        return record

    # ── 版本归档与回滚 ───────────────────────────────

    def _archive_version(self, rule: Rule):
        """将当前版本归档到 data/archived/。"""
        safe_name = rule.id.replace("/", "_").replace("\\", "_")
        archive_file = self.archive_dir / f"{safe_name}.v{rule.version}.json"
        with open(archive_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rule.to_dict(), ensure_ascii=False) + "\n")

    def _safe_name(self, rule_id: str) -> str:
        """将 rule_id 转为安全的文件名。"""
        return rule_id.replace("/", "_").replace("\\", "_")

    def list_archived_versions(self, rule_id: str) -> List[int]:
        """列出某个规则的所有已归档版本号。"""
        safe = self._safe_name(rule_id)
        versions = []
        for fpath in self.archive_dir.glob(f"{safe}.v*.json"):
            try:
                v = int(fpath.stem.split(".v")[1])
                versions.append(v)
            except (IndexError, ValueError):
                pass
        return sorted(versions)

    def rollback(self, rule_id: str, target_version: int) -> bool:
        """回滚到指定版本。

        Args:
            rule_id: 规则 ID
            target_version: 目标版本号

        Returns:
            True 表示回滚成功
        """
        safe = self._safe_name(rule_id)
        archive_file = self.archive_dir / f"{safe}.v{target_version}.json"
        if not archive_file.exists():
            if self.logger:
                self.logger.error("evolution", f"归档版本不存在: {archive_file}",
                                  component="evolution_engine")
            return False

        rule = self.storage.get(rule_id)
        if rule is None:
            if self.logger:
                self.logger.error("evolution", f"规则不存在: {rule_id}",
                                  component="evolution_engine")
            return False

        # 先归档当前版本
        self._archive_version(rule)

        # 从归档恢复
        with open(archive_file, "r", encoding="utf-8") as f:
            data = json.loads(f.readline().strip())
            restored = Rule.from_dict(data)

        # 更新 rule 字段
        rule.content = restored.content
        rule.confidence = restored.confidence
        rule.expires_at = restored.expires_at
        rule.evolve(f"v{rule.version}: 回滚到 v{target_version}")

        self._save_all()
        if self.index:
            self.index.build(self.storage.list())

        if self.logger:
            self.logger.evolution(
                rule_id=rule_id,
                evolution_type="rollback",
                old_confidence=0,
                new_confidence=rule.confidence,
                trigger_reason=f"回滚到 v{target_version}",
            )

        return True

    # ── 持久化 ───────────────────────────────────────

    def _save_all(self):
        """保存所有变更的规则分类。"""
        saved_categories = set()
        for rule in self.storage.list():
            if rule.category not in saved_categories:
                self.storage._save_category(rule.category)
                saved_categories.add(rule.category)

    # ── 统计 ─────────────────────────────────────────

    def stats(self) -> dict:
        """进化引擎统计。"""
        feedback_count = 0
        if self.feedback_path.exists():
            with open(self.feedback_path, "r", encoding="utf-8") as f:
                feedback_count = sum(1 for _ in f if _.strip())

        archive_count = len(list(self.archive_dir.glob("*.json")))

        return {
            "pending_evolutions": self.pending_count,
            "total_feedback": feedback_count,
            "archived_versions": archive_count,
        }
