"""
Rulerything 4.0 — 价值层单元测试
"""

import sys
import pytest
from dataclasses import dataclass
from typing import Dict, List


# ── Mock Rule ──────────────────────────────────────────

@dataclass
class MockRule:
    id: str
    title: str
    content: str = ""
    confidence: float = 0.5
    category: str = "general"
    value_vector: Dict[str, float] = None
    value_confidence: float = 0.5
    value_source: str = "default"
    value_provenance: str = None

    def __post_init__(self):
        if self.value_vector is None:
            self.value_vector = {
                "efficiency": 0.5, "correctness": 0.5, "security": 0.5,
                "simplicity": 0.5, "compatibility": 0.5, "testability": 0.5,
                "documentation": 0.5,
            }


# ── Test: 懒加载工厂 ─────────────────────────────────

class TestLazyLoading:
    def test_disabled_returns_none(self):
        """enabled=false 时 get_value_engine 返回 None。"""
        from value import get_value_engine
        import value as v
        v._engine = None
        engine = get_value_engine({"value": {"enabled": False}}, None)
        assert engine is None

    def test_disabled_zero_submodules(self):
        """enabled=false 时不导入 value/ 子模块。"""
        import subprocess
        import sys as _sys
        code = """
import sys
from value import get_value_engine
engine = get_value_engine({"value": {"enabled": False}}, None)
subs = [m for m in sys.modules if m.startswith("value.") and m != "value"]
print(f"engine=None:{engine is None},subs:{len(subs)}")
"""
        result = subprocess.run([_sys.executable, "-c", code],
                                capture_output=True, text=True)
        assert "engine=None:True" in result.stdout
        assert "subs:0" in result.stdout

    def test_enabled_loads_modules(self):
        """enabled=true 时子模块正确导入。"""
        from value import get_value_engine, ValueEngine
        import value as v
        v._engine = None
        config = {
            "value": {
                "enabled": True, "default_profile": "default",
                "profiles": {
                    "default": {
                        "weights": {"security": 0.8},
                        "conflict_strategy": "weighted_vote",
                        "priority_order": [],
                    }
                },
                "learning": {"enabled": False},
                "propagation": {"enabled": False},
                "decision_trace": {"enabled": True},
            }
        }
        engine = get_value_engine(config, None)
        assert engine is not None
        assert isinstance(engine, ValueEngine)
        assert "default" in engine.profiles
        assert hasattr(engine, 'sort_rules')
        assert hasattr(engine, 'learning')


# ── Test: 常量 ────────────────────────────────────────

class TestConst:
    def test_value_dimensions_7(self):
        from value.const import VALUE_DIMENSIONS
        assert len(VALUE_DIMENSIONS) == 7
        assert "security" in VALUE_DIMENSIONS
        assert "efficiency" in VALUE_DIMENSIONS

    def test_signal_values(self):
        from value.const import Signal
        assert Signal.POSITIVE == 1.0
        assert Signal.IMPLICIT_NEGATIVE == -0.3
        assert Signal.DECAY == -0.05

    def test_default_value_vector_all_05(self):
        from value.const import default_value_vector, VALUE_DIMENSIONS
        vec = default_value_vector()
        assert len(vec) == 7
        for v in vec.values():
            assert v == 0.5

    def test_category_templates(self):
        from value.const import CATEGORY_VALUE_TEMPLATES
        assert "security" in CATEGORY_VALUE_TEMPLATES
        assert CATEGORY_VALUE_TEMPLATES["security"]["security"] == 0.90


# ── Test: 向量运算 ────────────────────────────────────

class TestVector:
    def test_cosine_similarity_identical(self):
        from value.vector import cosine_similarity
        a = {"security": 0.9, "efficiency": 0.3}
        sim = cosine_similarity(a, a)
        assert abs(sim - 1.0) < 1e-6

    def test_cosine_similarity_orthogonal(self):
        from value.vector import cosine_similarity
        sim = cosine_similarity({"a": 1}, {"b": 1})
        assert sim == 0.0

    def test_weighted_sum_all_default(self):
        from value.vector import weighted_sum
        score = weighted_sum({"security": 0.5}, {"security": 0.5})
        assert abs(score - 0.5) < 1e-6

    def test_weighted_sum_biased(self):
        from value.vector import weighted_sum
        score = weighted_sum(
            {"security": 0.9, "efficiency": 0.1},
            {"security": 0.9, "efficiency": 0.1},
        )
        # High weight on high-value dimension, low weight on low-value
        # = (0.9*0.9 + 0.1*0.1 + 0.5*0.5*5) / (0.9+0.1+0.5*5)
        # = (0.81+0.01+1.25) / 3.5 = 2.07/3.5 ≈ 0.591
        assert 0.5 < score < 0.7

    def test_dot_product(self):
        from value.vector import dot_product
        result = dot_product({"a": 0.8, "b": 0.2}, {"a": 0.9, "b": 0.1})
        assert abs(result - (0.8*0.9 + 0.2*0.1)) < 1e-6


# ── Test: 画像 ────────────────────────────────────────

class TestProfile:
    def test_create_and_ensure_weights(self):
        from value.profile import ValueProfile
        p = ValueProfile(name="test", weights={"security": 0.8})
        p.ensure_weights()
        assert len(p.weights) == 7
        assert p.weights["security"] == 0.8
        assert p.weights["efficiency"] == 0.5

    def test_validate_ok(self):
        from value.profile import ValueProfile
        p = ValueProfile(name="test")
        p.ensure_weights()
        errors = p.validate()
        assert errors == []

    def test_validate_bad_strategy(self):
        from value.profile import ValueProfile
        p = ValueProfile(name="test", conflict_strategy="unknown")
        errors = p.validate()
        assert len(errors) == 1
        assert "未知冲突策略" in errors[0]

    def test_validate_bad_priority_dim(self):
        from value.profile import ValueProfile
        p = ValueProfile(name="test", priority_order=["nonexistent"])
        errors = p.validate()
        assert len(errors) == 1

    def test_load_profiles_from_config(self):
        from value.profile import load_profiles
        config = {
            "profiles": {
                "default": {"weights": {"security": 0.8}},
                "custom": {"weights": {"efficiency": 0.9}},
            }
        }
        profiles = load_profiles(config, None)
        assert "default" in profiles
        assert "custom" in profiles
        assert profiles["default"].weights["security"] == 0.8
        assert profiles["custom"].weights["efficiency"] == 0.9


# ── Test: 加权排序 ────────────────────────────────────

class TestWeighting:
    def test_value_weighted_score_formula(self):
        from value.weighting import value_weighted_score
        score = value_weighted_score(
            {"security": 0.9, "efficiency": 0.3},
            {"security": 0.8, "efficiency": 0.2},
            1.0,
        )
        assert 0 < score < 1

    def test_sort_rules_weighted_vote(self):
        from value.weighting import sort_rules
        from value.profile import ValueProfile
        profile = ValueProfile(name="test", weights={"security": 0.9, "efficiency": 0.2})
        profile.ensure_weights()
        rules = [
            MockRule(id="sec/001", title="Security", confidence=0.9,
                     value_vector={"security": 0.9, "efficiency": 0.3}),
            MockRule(id="perf/001", title="Performance", confidence=0.9,
                     value_vector={"security": 0.3, "efficiency": 0.9}),
        ]
        sorted_rules = sort_rules(rules, profile)
        assert sorted_rules[0].id == "sec/001"  # security 优先

    def test_sort_rules_lexicographic(self):
        from value.weighting import sort_rules
        from value.profile import ValueProfile
        profile = ValueProfile(
            name="test",
            weights={"security": 0.9, "efficiency": 0.2},
            conflict_strategy="lexicographic",
            priority_order=["security", "efficiency"],
        )
        profile.ensure_weights()
        rules = [
            MockRule(id="sec/001", title="Security", confidence=0.9,
                     value_vector={"security": 0.9, "efficiency": 0.3}),
            MockRule(id="perf/001", title="Performance", confidence=0.9,
                     value_vector={"security": 0.3, "efficiency": 0.9}),
        ]
        sorted_rules = sort_rules(rules, profile)
        assert sorted_rules[0].id == "sec/001"


# ── Test: 冲突检测 ────────────────────────────────────

class TestConflict:
    def test_detect_conflict_found(self):
        from value.conflict import detect_conflicts
        conflicts = detect_conflicts(
            {"security": 0.9, "efficiency": 0.2},
            {"security": 0.2, "efficiency": 0.9},
        )
        assert len(conflicts) >= 1
        assert conflicts[0]["dimension"] in ("security", "efficiency")

    def test_detect_no_conflict(self):
        from value.conflict import detect_conflicts
        conflicts = detect_conflicts(
            {"security": 0.8, "efficiency": 0.6},
            {"security": 0.7, "efficiency": 0.5},
        )
        assert len(conflicts) == 0

    def test_resolve_weighted_vote(self):
        from value.conflict import detect_conflicts, resolve_conflicts
        from value.profile import ValueProfile
        conflicts = detect_conflicts(
            {"security": 0.9, "efficiency": 0.2},
            {"security": 0.2, "efficiency": 0.9},
        )
        profile = ValueProfile(name="test", weights={"security": 0.9})
        resolved = resolve_conflicts(conflicts, profile, "rule_a", "rule_b")
        assert len(resolved) > 0
        # security weight > 0.5, so rule_a should win security conflicts
        sec_winners = [r["winner"] for r in resolved if r["dimension"] == "security"]
        if sec_winners:
            assert sec_winners[0] == "rule_a"


# ── Test: 探索机制 ────────────────────────────────────

class TestExploration:
    def test_epsilon_zero_identity(self):
        from value.exploration import maybe_explore
        rules = [MockRule(id=f"r/{i}", title=f"R{i}") for i in range(5)]
        result = maybe_explore(rules, epsilon=0.0)
        assert result[0].id == "r/0"
        assert result[4].id == "r/4"

    def test_epsilon_one_may_swap(self):
        import random
        random.seed(42)
        from value.exploration import maybe_explore
        rules = [MockRule(id=f"r/{i}", title=f"R{i}") for i in range(5)]
        result = maybe_explore(rules, epsilon=1.0)
        # With epsilon=1.0, very likely to swap
        changed = result[0].id != "r/0"
        assert changed, "epsilon=1.0 should almost always swap"


# ── Test: 决策追溯 ────────────────────────────────────

class TestDecisionTrace:
    def test_generate_brief(self):
        from value.decision_trace import generate_decision_trace
        from value.profile import ValueProfile
        profile = ValueProfile(name="test")
        profile.ensure_weights()
        rules = [
            MockRule(id="a/001", title="A", confidence=0.9,
                     value_vector={"security": 0.9, "efficiency": 0.3}),
            MockRule(id="b/001", title="B", confidence=0.8,
                     value_vector={"security": 0.3, "efficiency": 0.9}),
        ]
        trace = generate_decision_trace(rules[0], rules, profile, [], brief=True)
        assert trace["selected_rule_id"] == "a/001"
        assert "brief_differences" in trace

    def test_generate_full(self):
        from value.decision_trace import generate_decision_trace
        from value.profile import ValueProfile
        profile = ValueProfile(name="test")
        profile.ensure_weights()
        rules = [
            MockRule(id="a/001", title="A", confidence=0.9,
                     value_vector={"security": 0.9, "efficiency": 0.3}),
            MockRule(id="b/001", title="B", confidence=0.8,
                     value_vector={"security": 0.3, "efficiency": 0.9}),
        ]
        trace = generate_decision_trace(rules[0], rules, profile, [])
        assert trace["selected_rule_id"] == "a/001"
        assert len(trace["decision_tree"]) >= 1
        assert trace["scores"] != {}
        assert len(trace["alternatives"]) >= 1


# ── Test: 隐式学习 ────────────────────────────────────

class TestLearning:
    def test_learn_from_positive(self):
        from value.learning import ValueLearningEngine
        from value.profile import ValueProfile
        engine = ValueLearningEngine({"enabled": True, "learning_rate": 0.1}, None)
        profile = ValueProfile(name="test")
        profile.ensure_weights()
        old_w = profile.weights["security"]
        engine.learn_from_feedback(
            profile, {"security": 0.9, "efficiency": 0.3}, 1.0
        )
        assert profile.learn_count == 1
        assert profile.weights["security"] != old_w

    def test_learn_disabled_noop(self):
        from value.learning import ValueLearningEngine
        from value.profile import ValueProfile
        engine = ValueLearningEngine({"enabled": False}, None)
        profile = ValueProfile(name="test")
        profile.ensure_weights()
        old_w = dict(profile.weights)
        engine.learn_from_feedback(profile, {"security": 0.9}, 1.0)
        assert profile.weights == old_w

    def test_invalid_signal_raises(self):
        from value.learning import ValueLearningEngine
        from value.profile import ValueProfile
        engine = ValueLearningEngine({"enabled": True}, None)
        profile = ValueProfile(name="test")
        with pytest.raises(ValueError):
            engine.learn_from_feedback(profile, {"a": 0.5}, 999.0)


# ── Test: 传播 ────────────────────────────────────────

class TestPropagation:
    def test_low_confidence_skips(self):
        from value.propagation import propagate_values
        source = MockRule(id="s/001", title="S", value_confidence=0.3)
        result = propagate_values(source, [], None, min_source_confidence=0.7)
        assert result == []

    def test_non_manual_source_skips(self):
        from value.propagation import propagate_values
        source = MockRule(id="s/001", title="S", value_confidence=0.9,
                          value_source="default")
        result = propagate_values(source, [], None)
        assert result == []

    def test_bm25_similarity_jaccard_fallback(self):
        from value.propagation import _bm25_similarity
        a = MockRule(id="a/001", title="A",
                     content="使用参数化查询防止 SQL 注入攻击")
        b = MockRule(id="b/001", title="B",
                     content="SQL 注入防护使用参数化查询")
        sim = _bm25_similarity(None, a, b)
        assert sim > 0

    def test_bm25_similarity_no_match(self):
        from value.propagation import _bm25_similarity
        a = MockRule(id="a/001", title="A", content="Python 异步编程")
        b = MockRule(id="b/001", title="B", content="Docker 容器部署 Kubernetes")
        sim = _bm25_similarity(None, a, b)
        assert sim >= 0


# ── Test: 模式引擎 ────────────────────────────────────

class TestModeEngine:
    def test_off_mode(self):
        from value.mode_engine import ModeEngine
        me = ModeEngine({"mode": "off"}, None)
        assert me.should_use_value_engine() == (False, False)

    def test_shadow_mode(self):
        from value.mode_engine import ModeEngine, DeployMode
        me = ModeEngine({"mode": "shadow"}, None)
        assert me.should_use_value_engine() == (False, False)

    def test_dual_write_mode(self):
        from value.mode_engine import ModeEngine
        me = ModeEngine({"mode": "dual_write"}, None)
        use, collect = me.should_use_value_engine()
        assert use is False
        assert collect is True

    def test_full_mode(self):
        from value.mode_engine import ModeEngine
        me = ModeEngine({"mode": "full"}, None)
        assert me.should_use_value_engine() == (True, True)

    def test_grayscale_assigns_session(self):
        from value.mode_engine import ModeEngine
        me = ModeEngine({"mode": "grayscale", "grayscale": {"percent": 100}}, None)
        use, collect = me.should_use_value_engine("test-session")
        assert use is True

    def test_grayscale_zero_percent(self):
        from value.mode_engine import ModeEngine
        me = ModeEngine({"mode": "grayscale", "grayscale": {"percent": 0}}, None)
        use, collect = me.should_use_value_engine("test-session")
        assert use is False

    def test_auto_rollback_no_baseline(self):
        from value.mode_engine import ModeEngine
        me = ModeEngine({"mode": "full", "auto_rollback": {"enabled": True}}, None)
        should, reason = me.should_auto_rollback()
        assert should is False  # No baseline yet

    def test_record_and_status(self):
        from value.mode_engine import ModeEngine
        me = ModeEngine({"mode": "shadow"}, None)
        me.record_result(False, 10.0)
        me.record_result(False, 15.0)
        status = me.status_dict()
        assert status["mode"] == "shadow"
        assert status["recent_requests_1h"] == 2


# ── Test: 影子引擎 ────────────────────────────────────

class TestShadow:
    def test_compare_and_log(self):
        from value.shadow import ShadowEngine
        engine = ShadowEngine(None, None)
        diff = engine.compare_and_log(
            "test query",
            ["a/001", "b/001"],
            ["b/001", "a/001"],
            "default",
        )
        assert diff["order_changed"] is True
        assert diff["v3_top5"] == ["a/001", "b/001"]
        assert diff["v4_top5"] == ["b/001", "a/001"]

    def test_get_stats_empty(self):
        from value.shadow import ShadowEngine
        engine = ShadowEngine(None, None)
        stats = engine.get_stats()
        assert stats["total"] == 0


# ── Test: DecayTimer ─────────────────────────────────

class TestDecay:
    def test_start_stop(self):
        from value.decay import DecayTimer
        from value.profile import ValueProfile
        profiles = {"test": ValueProfile(name="test")}
        timer = DecayTimer({"enabled": True, "decay_half_life": 3600}, None, profiles)
        timer.start()
        assert timer._thread is not None
        assert timer._thread.is_alive()
        timer.stop()
        assert timer._thread is None

    def test_disabled_noop(self):
        from value.decay import DecayTimer
        timer = DecayTimer({"enabled": False}, None, {})
        timer.start()
        assert timer._thread is None


# ── Test: Rule 扩展字段 ───────────────────────────────

class TestRuleExtension:
    def test_default_value_fields(self):
        from rule import Rule
        r = Rule(id="test/001", title="Test", content="Content")
        assert "security" in r.value_vector
        assert r.value_confidence == 0.5
        assert r.value_source == "default"
        assert r.value_provenance is None

    def test_to_dict_roundtrip(self):
        from rule import Rule
        r = Rule(id="test/001", title="Test", content="Content",
                 value_vector={"security": 0.95, "correctness": 0.85},
                 value_source="manual")
        d = r.to_dict()
        assert "value_vector" in d
        assert d["value_source"] == "manual"
        restored = Rule.from_dict(d)
        assert restored.value_vector["security"] == 0.95
        assert restored.value_source == "manual"

    def test_from_dict_string_vector(self):
        """从 SQLite 读取的 JSON 字符串 value_vector 能被正确解析。"""
        from rule import Rule
        d = {
            "id": "test/001", "title": "Test", "content": "C",
            "value_vector": '{"security": 0.9, "efficiency": 0.3}',
            "value_confidence": 0.7,
        }
        r = Rule.from_dict(d)
        assert r.value_vector["security"] == 0.9
        assert r.value_vector["efficiency"] == 0.3
        # Missing dims should be set to defaults
        assert r.value_vector.get("correctness", 0.5) == 0.5

    def test_from_dict_no_value_fields(self):
        """旧数据无 value 字段时使用默认值。"""
        from rule import Rule
        d = {"id": "test/001", "title": "Test", "content": "C"}
        r = Rule.from_dict(d)
        assert r.value_confidence == 0.5
        assert r.value_source == "default"
        assert r.value_vector["security"] == 0.5


# ── Test: 序列化守卫 ─────────────────────────────────

class TestSerializationGuard:
    def test_filter_value_fields_disabled(self):
        from routes.search import _filter_value_fields
        data = {
            "results": [
                {"id": "a", "value_vector": {"s": 0.9}, "value_source": "manual"},
                {"id": "b", "value_vector": {"s": 0.5}},
            ],
            "decision_trace": {"selected": "a"},
        }
        result = _filter_value_fields(data, value_enabled=False)
        assert "decision_trace" not in result
        for item in result["results"]:
            assert "value_vector" not in item
            assert "value_source" not in item

    def test_keep_value_fields_enabled(self):
        from routes.search import _filter_value_fields
        data = {
            "results": [{"id": "a", "value_vector": {"s": 0.9}}],
            "decision_trace": {"selected": "a"},
        }
        result = _filter_value_fields(data, value_enabled=True)
        assert "decision_trace" in result
        assert "value_vector" in result["results"][0]


# ── Test: ValueEngine 集成 ─────────────────────────────

class TestValueEngine:
    def test_get_profile(self):
        from value import get_value_engine
        import value as v
        v._engine = None
        config = {
            "value": {
                "enabled": True, "default_profile": "default",
                "profiles": {
                    "default": {"weights": {"security": 0.8},
                                "conflict_strategy": "weighted_vote",
                                "priority_order": []},
                    "custom": {"weights": {"efficiency": 0.9},
                               "conflict_strategy": "weighted_vote",
                               "priority_order": []},
                },
                "learning": {"enabled": False},
                "propagation": {"enabled": False},
                "decision_trace": {"enabled": True},
            }
        }
        engine = get_value_engine(config, None)
        default = engine.get_profile()
        assert default.name == "default"
        custom = engine.get_profile("custom")
        assert custom.name == "custom"
        missing = engine.get_profile("nonexistent")
        assert missing is None

    def test_bootstrap_categories(self):
        from value import get_value_engine
        import value as v
        v._engine = None

        class MockStorage:
            def __init__(self):
                self.rules = [
                    MockRule(id="sec/001", title="S", category="security",
                             value_source="default"),
                    MockRule(id="perf/001", title="P", category="performance",
                             value_source="default"),
                    MockRule(id="arch/001", title="A", category="architecture",
                             value_source="default"),
                    MockRule(id="gen/001", title="G", category="general",
                             value_source="default"),
                ]
            def list(self):
                return self.rules
            def update(self, rid, **kw):
                pass

        config = {
            "value": {
                "enabled": True, "default_profile": "default",
                "profiles": {"default": {"weights": {}, "conflict_strategy": "weighted_vote", "priority_order": []}},
                "learning": {"enabled": False},
                "propagation": {"enabled": False},
                "decision_trace": {"enabled": True},
            }
        }
        engine = get_value_engine(config, MockStorage())
        result = engine.bootstrap_categories()
        assert result["bootstrapped"] == 3  # sec, perf, arch matched
        assert result["skipped"] == 1  # general unmatched


# ── Run ───────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
