"""
v3.0 AI 存储层测试 — storage_v2_ai.py (AIMixin)

覆盖: AI 缓存, AI 反馈, AI 统计, 父 AI 委托查询, AI 提炼日志
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from rule import Rule
from storage_v2 import RuleStorageV2


@pytest.fixture
def store():
    """指向临时目录的 SQLite 存储。"""
    tmp = tempfile.mkdtemp()
    s = RuleStorageV2(tmp)
    yield s


class TestAICache:
    """AI 缓存 CRUD + 淘汰。"""

    def test_cache_set_and_get(self, store):
        store.ai_cache_set("hash1", "query1", "response1", "claude", 0.01, 100)
        entry = store.ai_cache_get("hash1")
        assert entry is not None
        assert entry["query"] == "query1"
        assert entry["response"] == "response1"

    def test_cache_miss(self, store):
        entry = store.ai_cache_get("nonexistent")
        assert entry is None

    def test_cache_hit_increment(self, store):
        store.ai_cache_set("h1", "q", "r", "claude", 0.01, 100)
        store.ai_cache_hit("h1")
        store.ai_cache_hit("h1")
        entry = store.ai_cache_get("h1")
        assert entry["hit_count"] >= 3  # initial 1 + 2 hits

    def test_cache_cleanup_lru(self, store):
        for i in range(10):
            store.ai_cache_set(f"k{i:04d}", f"q{i}", f"r{i}", "claude", 0.01, 100)
        store.ai_cache_cleanup(max_entries=5)
        # 只保留最新的 5 条
        for i in range(5):
            assert store.ai_cache_get(f"k{i:04d}") is None  # 最旧的应被淘汰
        for i in range(5, 10):
            assert store.ai_cache_get(f"k{i:04d}") is not None

    def test_rule_version_hash(self, store):
        h = store.get_rule_version_hash()
        assert isinstance(h, str)
        assert len(h) == 16  # SHA256[:16]


class TestAIFeedback:
    """AI 反馈记录与统计。"""

    def test_record_and_get_feedback(self, store):
        store.add(Rule(id="fb/001", title="FB", content="test"))
        store.record_ai_feedback("fb/001", True)
        store.record_ai_feedback("fb/001", False)
        feedback = store.get_ai_feedback("fb/001")
        assert len(feedback) == 2

    def test_feedback_stats(self, store):
        store.add(Rule(id="fb/002", title="FB2", content="test"))
        store.record_ai_feedback("fb/002", True)
        store.record_ai_feedback("fb/002", True)
        store.record_ai_feedback("fb/002", False)
        stats = store.get_ai_feedback_stats("fb/002")
        assert stats["total"] == 3
        assert stats["positive"] == 2
        assert stats["negative"] == 1
        assert round(stats["ratio"], 2) == 0.67


class TestPendingQueries:
    """父 AI 委托查询。"""

    def test_add_and_list(self, store):
        qid = store.add_pending_query("test query")
        assert qid.startswith("q_")
        pending = store.get_pending_queries(status="pending")
        assert any(p["id"] == qid for p in pending)

    def test_answer_query(self, store):
        qid = store.add_pending_query("answer me")
        ok = store.answer_pending_query(qid, "the answer")
        assert ok is True
        # 不能再次回答
        ok2 = store.answer_pending_query(qid, "again")
        assert ok2 is False

    def test_pending_query_count(self, store):
        store.add_pending_query("q1")
        store.add_pending_query("q2")
        counts = store.get_pending_query_count()
        assert counts["pending"] >= 2


class TestIngestionLog:
    """AI 提炼日志。"""

    def test_log_and_retrieve(self, store):
        store.log_ingestion("query", "created", rule_id="in/001")
        store.log_ingestion("query2", "duplicate", rule_id="in/002")
        logs = store.get_ingestion_logs(limit=10)
        assert len(logs) >= 2

    def test_filter_by_status(self, store):
        store.log_ingestion("q1", "created")
        store.log_ingestion("q2", "skipped")
        created = store.get_ingestion_logs(status="created")
        assert all(l["status"] == "created" for l in created)

    def test_ai_stats(self, store):
        store.ai_cache_set("stat_h1", "q", "r", "claude", 1.0, 100)
        store.log_ingestion("q", "created", rule_id="st/001")
        stats = store.get_ai_stats()
        assert stats["cache_entries"] >= 1
        assert stats["rules_ingested"] >= 1
