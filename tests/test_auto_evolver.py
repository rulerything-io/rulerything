"""
v3.0 自动演化引擎单元测试 — AutoEvolver
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from rule import Rule
from storage_v2 import RuleStorageV2
from index import EverythingStyleIndex
from logger import RuleLogger


@pytest.fixture
def env():
    """创建完整测试环境（存储 + 索引 + 日志）。"""
    tmpdir = tempfile.mkdtemp()
    store = RuleStorageV2(tmpdir)
    index = EverythingStyleIndex()
    logger = RuleLogger(tmpdir, level="WARNING")
    # 添加一些规则让引擎有东西可用
    for i in range(5):
        store.add(Rule(
            id=f"test/{i:03d}", title=f"Rule {i}",
            content=f"Content {i}", category="test",
            tags=["test"], confidence=0.5 + i * 0.1,
        ))
    return {
        "store": store,
        "index": index,
        "logger": logger,
        "tmpdir": tmpdir,
    }


class TestAutoEvolverInit:
    def test_import(self):
        """AutoEvolver 可导入。"""
        from auto_evolver import AutoEvolver
        assert AutoEvolver is not None

    def test_init_with_minimal_config(self, env):
        """最小配置初始化不报错。"""
        from auto_evolver import AutoEvolver
        evolver = AutoEvolver(
            env["store"], env["index"], env["logger"],
            {"enabled": True},
        )
        assert evolver is not None
        stats = evolver.get_stats()
        assert isinstance(stats, dict)
        assert "strategies" in stats

    def test_init_disabled(self, env):
        """显式关闭时初始化正常。"""
        from auto_evolver import AutoEvolver
        evolver = AutoEvolver(
            env["store"], env["index"], env["logger"],
            {"enabled": False},
        )
        stats = evolver.get_stats()
        assert isinstance(stats, dict)


class TestAutoEvolverTick:
    def test_tick_with_metrics(self, env):
        """tick() 在任意指标下不崩溃。"""
        from auto_evolver import AutoEvolver
        evolver = AutoEvolver(
            env["store"], env["index"], env["logger"],
            {"enabled": True},
        )
        metrics = {
            "cache_hit_rate": 0.5,
            "avg_latency_ms": 10,
            "total_rules": 5,
            "hot_cache_size": 3,
            "cold_count": 0,
            "health_score": 1.0,
        }
        results = evolver.tick(metrics)
        # 不应抛出异常
        assert isinstance(results, list)

    def test_tick_with_zero_metrics(self, env):
        """全零指标的边界情况。"""
        from auto_evolver import AutoEvolver
        evolver = AutoEvolver(
            env["store"], env["index"], env["logger"],
            {"enabled": True},
        )
        metrics = {k: 0 for k in ["cache_hit_rate", "avg_latency_ms",
                                   "total_rules", "hot_cache_size",
                                   "cold_count", "health_score"]}
        results = evolver.tick(metrics)
        assert isinstance(results, list)


class TestAutoEvolverStrategies:
    def test_get_strategy(self, env):
        from auto_evolver import AutoEvolver
        evolver = AutoEvolver(
            env["store"], env["index"], env["logger"],
            {"enabled": True},
        )
        # 获取不存在的策略应返回 None
        s = evolver.get_strategy("nonexistent")
        assert s is None

    def test_run_strategy_now(self, env):
        from auto_evolver import AutoEvolver
        evolver = AutoEvolver(
            env["store"], env["index"], env["logger"],
            {"enabled": True},
        )
        metrics = {
            "cache_hit_rate": 0.5,
            "avg_latency_ms": 10,
            "total_rules": 5,
            "hot_cache_size": 3,
            "cold_count": 0,
            "health_score": 1.0,
        }
        # 手动运行一条不存在的策略应返回错误
        result = evolver.run_strategy_now("nonexistent_strategy", metrics)
        assert isinstance(result, dict)
