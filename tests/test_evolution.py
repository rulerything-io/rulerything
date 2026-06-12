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

"""
进化引擎单元测试 — 反馈调度、进化应用、归档、回滚
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rule import Rule
from storage import RuleStorage
from index import EverythingStyleIndex
from evolution import (
    EvolutionEngine, EvolutionType, EvolutionRecord,
    EvolutionError,
)


def _make_rule(**overrides) -> Rule:
    """快速创建测试用规则。"""
    defaults = dict(
        id="test/001", title="Test Rule",
        content="This is a test rule for verification.",
        category="test", tags=["test"],
        confidence=0.8, hit_count=5,
        verifier="manual", version=1,
    )
    defaults.update(overrides)
    return Rule(**defaults)


def _create_storage(base_dir: str = None) -> RuleStorage:
    """创建指向临时目录的存储实例。"""
    if base_dir is None:
        base_dir = tempfile.mkdtemp()
    store = RuleStorage(base_dir)
    # 清理默认加载的 data/ 中的规则
    store._rules.clear()
    return store


# ── EvolutionType ─────────────────────────────────────

class TestEvolutionType:
    def test_enum_values(self):
        assert EvolutionType.CONFIDENCE_ADJUST.value == "confidence_adjust"
        assert EvolutionType.CONTENT_REFINE.value == "content_refine"
        assert EvolutionType.SPLIT_RULE.value == "split_rule"
        assert EvolutionType.MERGE_RULES.value == "merge_rules"
        assert EvolutionType.DEPRECATE_RULE.value == "deprecate_rule"
        assert EvolutionType.ADD_EXAMPLE.value == "add_example"

    def test_enum_count(self):
        assert len(EvolutionType) == 6

    def test_enum_str(self):
        assert str(EvolutionType.CONFIDENCE_ADJUST) == "confidence_adjust"


# ── EvolutionRecord ───────────────────────────────────

class TestEvolutionRecord:
    def test_to_dict(self):
        record = EvolutionRecord(
            rule_id="test/001",
            evolution_type="confidence_adjust",
            old_confidence=0.8, new_confidence=0.7,
            old_content="old", new_content="new",
            trigger_reason="测试",
            timestamp="2026-01-01T00:00:00",
            version=2,
        )
        d = record.to_dict()
        assert d["rule_id"] == "test/001"
        assert d["new_confidence"] == 0.7
        assert d["version"] == 2

    def test_to_json(self):
        record = EvolutionRecord(
            rule_id="test/001",
            evolution_type="content_refine",
            old_confidence=0.8, new_confidence=0.85,
            old_content="old", new_content="new",
            trigger_reason="refine",
            timestamp="2026-01-01T00:00:00",
            version=2,
        )
        data = json.loads(record.to_json())
        assert data["rule_id"] == "test/001"
        assert data["evolution_type"] == "content_refine"


# ── 反馈收集 ─────────────────────────────────────────

class TestFeedbackCollection:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.storage = _create_storage(self.tmpdir)
        self.engine = EvolutionEngine(
            self.storage, data_dir=self.tmpdir,
        )
        # 添加测试规则
        self.storage.add(_make_rule(id="test/001", confidence=0.8))

    def test_collect_feedback_saves_to_file(self):
        self.engine.collect_feedback("test/001", True, "有用")
        assert self.engine.feedback_path.exists()
        lines = self.engine.feedback_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["rule_id"] == "test/001"
        assert data["satisfied"] is True

    def test_satisfied_feedback_no_evolution(self):
        self.engine.collect_feedback("test/001", True, "很好用")
        assert self.engine.pending_count == 0

    def test_unsatisfied_feedback_triggers_evolution(self):
        self.engine.collect_feedback("test/001", False, "太宽泛，不够具体")
        assert self.engine.pending_count == 1
        evo_type, rule_id, params = self.engine._pending[0]
        assert evo_type == EvolutionType.SPLIT_RULE
        assert rule_id == "test/001"

    def test_nonexistent_rule_feedback(self):
        """不存在的规则不应导致报错。"""
        self.engine.collect_feedback("nonexistent", False, "有问题")
        # 不会调度进化，也不会崩溃
        assert self.engine.pending_count == 0

    def test_multiple_feedback_appended(self):
        self.engine.collect_feedback("test/001", False, "太宽泛")
        self.engine.collect_feedback("test/001", True, "不错")
        lines = self.engine.feedback_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


# ── 进化调度（关键词匹配） ────────────────────────────

class TestEvolutionScheduling:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.storage = _create_storage(self.tmpdir)
        self.storage.add(_make_rule(id="test/001"))
        self.engine = EvolutionEngine(
            self.storage, data_dir=self.tmpdir,
        )

    def _schedule(self, context: str):
        """触发调度并返回匹配的进化类型。"""
        self.engine.collect_feedback("test/001", False, context)
        if self.engine.pending_count > 0:
            return self.engine._pending[0][0]
        return None

    def test_split_rule_keywords(self):
        for kw in ["太宽泛", "不够具体", "太笼统", "不精确"]:
            self.engine._pending.clear()
            evo_type = self._schedule(kw)
            assert evo_type == EvolutionType.SPLIT_RULE, f"关键词 '{kw}' 应匹配 SPLIT_RULE"

    def test_content_refine_keywords(self):
        for kw in ["有例外", "特殊情况", "不考虑", "忽略"]:
            self.engine._pending.clear()
            evo_type = self._schedule(kw)
            assert evo_type == EvolutionType.CONTENT_REFINE, f"关键词 '{kw}' 应匹配 CONTENT_REFINE"

    def test_deprecate_keywords(self):
        for kw in ["过时", "已淘汰", "不再适用", "旧版本"]:
            self.engine._pending.clear()
            evo_type = self._schedule(kw)
            assert evo_type == EvolutionType.DEPRECATE_RULE, f"关键词 '{kw}' 应匹配 DEPRECATE_RULE"

    def test_add_example_keywords(self):
        for kw in ["示例", "例子", "不明白", "看不懂"]:
            self.engine._pending.clear()
            evo_type = self._schedule(kw)
            assert evo_type == EvolutionType.ADD_EXAMPLE, f"关键词 '{kw}' 应匹配 ADD_EXAMPLE"

    def test_merge_rules_keywords(self):
        for kw in ["重复", "冗余"]:
            self.engine._pending.clear()
            evo_type = self._schedule(kw)
            assert evo_type == EvolutionType.MERGE_RULES, f"关键词 '{kw}' 应匹配 MERGE_RULES"

    def test_default_confidence_adjust(self):
        """未匹配任何关键词时应默认降权。"""
        evo_type = self._schedule("随机无意义反馈")
        assert evo_type == EvolutionType.CONFIDENCE_ADJUST

    def test_default_confidence_delta(self):
        self.engine.collect_feedback("test/001", False, "随机无意义反馈")
        _, _, params = self.engine._pending[0]
        assert params.get("delta") == -0.1

    def test_keyword_first_match_wins(self):
        """多个关键词匹配时，第一个规则优先。"""
        # "太宽泛" 在 "有例外" 之前定义
        evo_type = self._schedule("太宽泛，但特殊情况也需考虑")
        assert evo_type == EvolutionType.SPLIT_RULE


# ── 进化应用 ─────────────────────────────────────────

class TestApplyEvolution:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.storage = _create_storage(self.tmpdir)
        self.storage.add(_make_rule(
            id="test/001", content="原始规则内容",
            confidence=0.8, version=1,
        ))
        self.engine = EvolutionEngine(
            self.storage, data_dir=self.tmpdir,
        )

    def test_confidence_adjust(self):
        self.engine._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, "test/001", {"delta": -0.1})
        )
        changes = self.engine.apply_pending_evolutions(dry_run=False)
        assert len(changes) == 1
        rule = self.storage.get("test/001")
        assert round(rule.confidence, 2) == 0.7  # 0.8 - 0.1
        assert rule.version == 2

    def test_confidence_adjust_clamped(self):
        """置信度应在 [0, 1] 范围内。"""
        self.engine._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, "test/001", {"delta": -10.0})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        rule = self.storage.get("test/001")
        assert rule.confidence == 0.0

    def test_content_refine(self):
        self.engine._pending.append(
            (EvolutionType.CONTENT_REFINE, "test/001",
             {"hint": "存在例外情况"})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        rule = self.storage.get("test/001")
        assert "> 注意：存在例外情况" in rule.content
        assert "原始规则内容" in rule.content

    def test_split_rule(self):
        self.engine._pending.append(
            (EvolutionType.SPLIT_RULE, "test/001",
             {"hint": "建议拆分"})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        rule = self.storage.get("test/001")
        assert "[待拆分]" in rule.content
        assert rule.confidence == 0.8 * 0.7  # 0.56

    def test_merge_rules(self):
        self.engine._pending.append(
            (EvolutionType.MERGE_RULES, "test/001",
             {"hint": "与另一条规则重复"})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        rule = self.storage.get("test/001")
        assert "[待合并]" in rule.content
        assert rule.confidence == 0.8 * 0.8  # 0.64

    def test_deprecate_rule(self):
        self.engine._pending.append(
            (EvolutionType.DEPRECATE_RULE, "test/001",
             {"reason": "此规则已不再适用"})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        rule = self.storage.get("test/001")
        assert "[已过时]" in rule.content
        assert rule.confidence == 0.0
        assert rule.is_expired

    def test_add_example(self):
        self.engine._pending.append(
            (EvolutionType.ADD_EXAMPLE, "test/001", {})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        rule = self.storage.get("test/001")
        assert "示例：待补充" in rule.content

    def test_evolution_log_updated(self):
        self.engine._pending.append(
            (EvolutionType.CONTENT_REFINE, "test/001",
             {"hint": "测试进化日志"})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        rule = self.storage.get("test/001")
        assert len(rule.evolution_log) == 1
        assert "content_refine" in rule.evolution_log[0]

    def test_unknown_evolution_type(self):
        """未知进化类型应抛异常。"""
        self.engine._pending.append(
            ("unknown_type", "test/001", {})
        )
        changes = self.engine.apply_pending_evolutions(dry_run=False)
        # 应该跳过未知类型，不崩溃
        assert len(changes) == 0


# ── Dry Run ───────────────────────────────────────────

class TestDryRun:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.storage = _create_storage(self.tmpdir)
        self.storage.add(_make_rule(
            id="test/001", content="原始内容",
            confidence=0.8, version=1,
        ))
        self.engine = EvolutionEngine(
            self.storage, data_dir=self.tmpdir,
        )

    def test_dry_run_does_not_modify_rule(self):
        self.engine._pending.append(
            (EvolutionType.CONTENT_REFINE, "test/001",
             {"hint": "测试"})
        )
        changes = self.engine.apply_pending_evolutions(dry_run=True)
        assert len(changes) == 1
        # 规则未被修改
        rule = self.storage.get("test/001")
        assert rule.content == "原始内容"
        assert rule.version == 1

    def test_dry_run_returns_valid_record(self):
        self.engine._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, "test/001", {"delta": -0.1})
        )
        changes = self.engine.apply_pending_evolutions(dry_run=True)
        assert changes[0]["old_confidence"] == 0.8
        assert round(changes[0]["new_confidence"], 2) == 0.7
        # dry_run 不改 version
        assert changes[0]["version"] == 1  # version 不加 1

    def test_dry_run_does_not_clear_pending(self):
        self.engine._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, "test/001", {"delta": -0.1})
        )
        self.engine.apply_pending_evolutions(dry_run=True)
        assert self.engine.pending_count == 1

    def test_apply_clears_pending(self):
        self.engine._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, "test/001", {"delta": -0.1})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        assert self.engine.pending_count == 0

    def test_dry_run_method(self):
        self.engine._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, "test/001", {"delta": -0.1})
        )
        changes = self.engine.dry_run()
        assert len(changes) == 1


# ── 归档 ─────────────────────────────────────────────

class TestArchive:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.storage = _create_storage(self.tmpdir)
        self.storage.add(_make_rule(
            id="test/001", content="v1 内容",
            confidence=0.8, version=1,
        ))
        self.engine = EvolutionEngine(
            self.storage, data_dir=self.tmpdir,
        )

    def test_archive_created_on_apply(self):
        self.engine._pending.append(
            (EvolutionType.CONTENT_REFINE, "test/001",
             {"hint": "测试归档"})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        archive_files = list(self.engine.archive_dir.glob("test_001.v*.json"))
        assert len(archive_files) == 1

    def test_archive_contains_original_data(self):
        self.engine._pending.append(
            (EvolutionType.CONTENT_REFINE, "test/001",
             {"hint": "测试归档"})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        archive_file = self.engine.archive_dir / "test_001.v1.json"
        assert archive_file.exists()
        data = json.loads(archive_file.read_text(encoding="utf-8"))
        assert data["content"] == "v1 内容"
        assert data["confidence"] == 0.8
        assert data["version"] == 1

    def test_archive_not_created_on_dry_run(self):
        self.engine._pending.append(
            (EvolutionType.CONTENT_REFINE, "test/001",
             {"hint": "测试归档"})
        )
        self.engine.apply_pending_evolutions(dry_run=True)
        archive_files = list(self.engine.archive_dir.glob("*.json"))
        assert len(archive_files) == 0

    def test_list_archived_versions(self):
        self.engine._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, "test/001", {"delta": -0.1})
        )
        self.engine.apply_pending_evolutions(dry_run=False)

        # 第二次进化
        self.engine._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, "test/001", {"delta": -0.1})
        )
        self.engine.apply_pending_evolutions(dry_run=False)

        versions = self.engine.list_archived_versions("test/001")
        assert versions == [1, 2]  # v1 和 v2 被归档

    def test_archive_safe_name_with_slashes(self):
        """rule_id 中的斜杠应被替换为下划线。"""
        self.storage.add(_make_rule(
            id="category/002", content="带斜杠的规则",
            confidence=0.8, version=1,
        ))
        self.engine._pending.append(
            (EvolutionType.CONTENT_REFINE, "category/002",
             {"hint": "测试斜杠归档"})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        archive_file = self.engine.archive_dir / "category_002.v1.json"
        assert archive_file.exists()


# ── 回滚 ─────────────────────────────────────────────

class TestRollback:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.storage = _create_storage(self.tmpdir)
        self.storage.add(_make_rule(
            id="test/001", title="Rollback Test",
            content="原始内容 v1", confidence=0.8, version=1,
        ))
        self.engine = EvolutionEngine(
            self.storage, data_dir=self.tmpdir,
        )
        # 执行一次进化以创建归档
        self.engine._pending.append(
            (EvolutionType.CONTENT_REFINE, "test/001",
             {"hint": "第一次进化"})
        )
        self.engine.apply_pending_evolutions(dry_run=False)

    def test_rollback_restores_content(self):
        self.engine.rollback("test/001", 1)
        rule = self.storage.get("test/001")
        assert rule.content == "原始内容 v1"
        assert rule.confidence == 0.8

    def test_rollback_increments_version(self):
        before_version = self.storage.get("test/001").version
        self.engine.rollback("test/001", 1)
        rule = self.storage.get("test/001")
        assert rule.version == before_version + 1

    def test_rollback_archives_current_version(self):
        current_content = self.storage.get("test/001").content
        self.engine.rollback("test/001", 1)
        # 当前版本应被归档
        archive_files = sorted(self.engine.archive_dir.glob("test_001.v*.json"))
        versions = [int(f.stem.split(".v")[1]) for f in archive_files]
        # 应有 v1（原始）和 v2（进化后的版本，回滚时归档）
        assert 1 in versions
        assert 2 in versions

    def test_rollback_nonexistent_version(self):
        result = self.engine.rollback("test/001", 99)
        assert result is False

    def test_rollback_nonexistent_rule(self):
        result = self.engine.rollback("nonexistent", 1)
        assert result is False

    def test_rollback_updates_evolution_log(self):
        self.engine.rollback("test/001", 1)
        rule = self.storage.get("test/001")
        logs = rule.evolution_log
        assert any("回滚" in log for log in logs)


# ── 持久化 ───────────────────────────────────────────

class TestPersistence:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.storage = _create_storage(self.tmpdir)
        self.storage.add(_make_rule(
            id="test/001", content="持久化测试",
            category="test", confidence=0.8, version=1,
        ))
        self.engine = EvolutionEngine(
            self.storage, data_dir=self.tmpdir,
        )

    def test_save_all_persists_changes(self):
        self.engine._pending.append(
            (EvolutionType.CONTENT_REFINE, "test/001",
             {"hint": "持久化测试"})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        # 重新加载存储
        storage2 = RuleStorage(self.tmpdir)
        rule = storage2.get("test/001")
        assert rule is not None
        assert "> 注意：持久化测试" in rule.content

    def test_feedback_persisted_across_instances(self):
        self.engine.collect_feedback("test/001", False, "太宽泛")
        engine2 = EvolutionEngine(
            self.storage, data_dir=self.tmpdir,
        )
        assert engine2.stats()["total_feedback"] == 1


# ── 统计 ─────────────────────────────────────────────

class TestStats:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.storage = _create_storage(self.tmpdir)
        self.storage.add(_make_rule(id="test/001"))
        self.engine = EvolutionEngine(
            self.storage, data_dir=self.tmpdir,
        )

    def test_stats_initial(self):
        s = self.engine.stats()
        assert s["pending_evolutions"] == 0
        assert s["total_feedback"] == 0
        assert s["archived_versions"] == 0

    def test_stats_after_feedback(self):
        self.engine.collect_feedback("test/001", True, "好")
        s = self.engine.stats()
        assert s["total_feedback"] == 1

    def test_stats_after_evolution(self):
        self.engine._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, "test/001", {"delta": -0.1})
        )
        self.engine.apply_pending_evolutions(dry_run=False)
        s = self.engine.stats()
        assert s["archived_versions"] == 1
        assert s["pending_evolutions"] == 0

    def test_pending_count_property(self):
        assert self.engine.pending_count == 0
        self.engine._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, "test/001", {})
        )
        assert self.engine.pending_count == 1

    def test_pending_evolutions_property(self):
        self.engine._pending.append(
            (EvolutionType.SPLIT_RULE, "test/001", {"hint": "测试"})
        )
        pending = self.engine.pending_evolutions
        assert len(pending) == 1
        assert pending[0]["evolution_type"] == "split_rule"
        assert pending[0]["rule_id"] == "test/001"

    def test_clear_pending(self):
        self.engine._pending.append(
            (EvolutionType.CONFIDENCE_ADJUST, "test/001", {})
        )
        self.engine.clear_pending()
        assert self.engine.pending_count == 0


# ── 空队列 ───────────────────────────────────────────

class TestEmptyQueue:
    def setup_method(self):
        self.engine = EvolutionEngine(
            _create_storage(tempfile.mkdtemp()),
            data_dir=tempfile.mkdtemp(),
        )

    def test_apply_empty(self):
        changes = self.engine.apply_pending_evolutions(dry_run=False)
        assert changes == []

    def test_dry_run_empty(self):
        changes = self.engine.dry_run()
        assert changes == []

    def test_stats_empty(self):
        s = self.engine.stats()
        assert s["pending_evolutions"] == 0


# ── EvolutionError ───────────────────────────────────

class TestEvolutionError:
    def test_exception_raised(self):
        try:
            raise EvolutionError("测试错误")
        except EvolutionError as e:
            assert "测试错误" in str(e)

    def test_exception_inherits_exception(self):
        assert issubclass(EvolutionError, Exception)
