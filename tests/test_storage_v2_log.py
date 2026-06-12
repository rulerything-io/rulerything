"""
v3.0 日志与配置存储层测试 — storage_v2_log.py (LogMixin)

覆盖: 查询日志, 指标记录, 运行时配置
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from storage_v2 import RuleStorageV2


@pytest.fixture
def store():
    """指向临时目录的 SQLite 存储。"""
    tmp = tempfile.mkdtemp()
    s = RuleStorageV2(tmp)
    yield s


class TestQueryLog:
    """查询日志记录与检索。"""

    def test_log_query(self, store):
        store.log_query("test query", 2.5, 5, True, result_ids=["r/001", "r/002"])
        queries = store.get_recent_queries(days=1, min_freq=1)
        assert any(q["query"] == "test query" for q in queries)

    def test_query_result_ids(self, store):
        store.log_query("q1", 1.0, 2, True, result_ids=["r/001", "r/002"])
        store.log_query("q2", 2.0, 1, False, result_ids=["r/003"])
        results = store.get_recent_query_results(days=1)
        assert len(results) >= 2

    def test_rotate_query_log(self, store):
        # 写入数据后轮换不应报错
        store.log_query("test", 1.0, 1, False)
        store.rotate_query_log()


class TestMetrics:
    """指标记录。"""

    def test_log_and_get_metrics(self, store):
        store.log_metric("cache_hit_rate", 0.85)
        store.log_metric("cache_hit_rate", 0.90)
        metrics = store.get_metrics("cache_hit_rate", hours=24)
        assert len(metrics) >= 2
        assert all("timestamp" in m for m in metrics)
        assert all("value" in m for m in metrics)

    def test_empty_metrics(self, store):
        metrics = store.get_metrics("nonexistent", hours=1)
        assert metrics == []


class TestConfig:
    """运行时配置读写。"""

    def test_set_and_get_config(self, store):
        store.set_config("test_key", "test_value")
        val = store.get_config("test_key")
        assert val == "test_value"

    def test_get_missing_config(self, store):
        val = store.get_config("nonexistent_key")
        assert val is None

    def test_get_missing_with_default(self, store):
        val = store.get_config("nonexistent", "default_val")
        assert val == "default_val"

    def test_overwrite_config(self, store):
        store.set_config("key1", "v1")
        store.set_config("key1", "v2")
        assert store.get_config("key1") == "v2"

    def test_config_isolation(self, store):
        store.set_config("only", "isolated")
        other = RuleStorageV2(tempfile.mkdtemp())
        assert other.get_config("only") is None
