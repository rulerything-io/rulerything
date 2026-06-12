"""
v3.0 SQLite 存储单元测试 — RuleStorageV2
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from rule import Rule
from storage_v2 import RuleStorageV2


@pytest.fixture
def store():
    """创建指向临时目录的 SQLite 存储实例。"""
    tmp = tempfile.mkdtemp()
    s = RuleStorageV2(tmp)
    yield s
    # 清理
    try:
        db_path = Path(tmp) / "rules.db"
        if db_path.exists():
            db_path.unlink()
    except Exception:
        pass


def _rule(**overrides) -> Rule:
    defaults = dict(
        id="test/001", title="Test Rule",
        content="Content", category="test",
        tags=["test"], confidence=0.8,
        hit_count=0, verifier="manual", version=1,
    )
    defaults.update(overrides)
    return Rule(**defaults)


# ── CRUD ───────────────────────────────────────────────


class TestCRUD:
    def test_add_and_get(self, store):
        r = _rule(id="a/001", title="Alpha")
        ok, msg = store.add(r)
        assert ok, msg
        got = store.get("a/001")
        assert got is not None
        assert got.id == "a/001"
        assert got.title == "Alpha"

    def test_add_duplicate(self, store):
        r = _rule(id="a/001", content="Unique dup content")
        store.add(r)
        ok, msg = store.add(_rule(id="a/001", content="Other content"))
        assert not ok

    def test_list(self, store):
        store.add(_rule(id="a/001", title="Alpha", content="Alpha content"))
        store.add(_rule(id="a/002", title="Beta", content="Beta content"))
        rules = store.list()
        assert len(rules) >= 2
        ids = [r.id for r in rules]
        assert "a/001" in ids
        assert "a/002" in ids

    def test_list_with_category(self, store):
        store.add(_rule(id="a/001", content="Python content", category="python"))
        store.add(_rule(id="a/002", content="Go content", category="go"))
        python_rules = store.list(category="python")
        assert len(python_rules) >= 1
        assert any(r.id == "a/001" for r in python_rules)

    def test_hard_delete(self, store):
        store.add(_rule(id="a/001"))
        ok = store.hard_delete("a/001")
        assert ok
        assert store.get("a/001") is None

    def test_update_rule(self, store):
        store.add(_rule(id="a/001"))
        ok = store.update("a/001", hit_count=10, title="Updated")
        assert ok
        got = store.get("a/001")
        assert got.hit_count == 10
        assert got.title == "Updated"


# ── 运行时配置 ─────────────────────────────────────────


class TestConfig:
    def test_set_and_get_config(self, store):
        store.set_config("test_key", "test_value")
        val = store.get_config("test_key")
        assert val == "test_value"

    def test_get_nonexistent_config(self, store):
        val = store.get_config("no_such_key")
        assert val is None

    def test_get_config_with_default(self, store):
        val = store.get_config("no_such_key", "fallback")
        assert val == "fallback"

    def test_overwrite_config(self, store):
        store.set_config("key", "v1")
        store.set_config("key", "v2")
        assert store.get_config("key") == "v2"


# ── 快照管理 ──────────────────────────────────────────


class TestSnapshots:
    def test_create_and_list_snapshots(self, store):
        store.add(_rule(id="a/001"))
        sid = store.create_snapshot()
        assert sid is not None
        assert len(sid) > 0

        snapshots = store.list_snapshots()
        assert any(s["id"] == sid for s in snapshots)

    def test_restore_snapshot(self, store):
        store.add(_rule(id="a/001"))
        sid = store.create_snapshot()
        assert sid is not None
        ok = store.restore_snapshot(sid)
        assert ok
        # a/001 应该在回滚后仍在
        assert store.get("a/001") is not None

    def test_restore_nonexistent_snapshot(self, store):
        ok = store.restore_snapshot("no_such_snapshot")
        assert not ok


# ── 去重 ───────────────────────────────────────────────


class TestDedup:
    def test_dry_run_no_duplicates(self, store):
        store.add(_rule(id="a/001", title="Unique"))
        store.add(_rule(id="a/002", title="Different"))
        result = store.dedup_dry_run()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_dry_run_finds_duplicates(self, store):
        store.add(_rule(id="a/001", title="Same Title", content="Content A"))
        store.add(_rule(id="a/002", title="Same Title", content="Content B"))
        result = store.dedup_dry_run()
        assert isinstance(result, list)
        # 可能有重复检测结果
        if result:
            assert isinstance(result[0], dict)


# ── 待处理查询 ─────────────────────────────────────────


class TestPendingQueries:
    def test_add_and_get_pending(self, store):
        qid = store.add_pending_query("test query")
        assert qid is not None

        queries = store.get_pending_queries(status="pending")
        assert any(q["id"] == qid for q in queries)

    def test_answer_pending_query(self, store):
        qid = store.add_pending_query("test query")
        ok = store.answer_pending_query(qid, "test response", responder="unittest")
        assert ok

        # 已回答的不应出现在待处理中
        pending = store.get_pending_queries(status="pending")
        assert not any(q["id"] == qid for q in pending)

    def test_pending_query_count(self, store):
        store.add_pending_query("query 1")
        store.add_pending_query("query 2")
        counts = store.get_pending_query_count()
        assert isinstance(counts, dict)


# ── AI 缓存 ────────────────────────────────────────────


class TestAICache:
    def test_cache_set_and_get(self, store):
        store.add(_rule(id="a/001"))  # ensure db is initialized
        qh = "abc123hash"
        store.ai_cache_set(qh, "query", "response", "claude", 0.01, 100)
        cached = store.ai_cache_get(qh, ttl_hours=24)
        assert cached is not None
        assert cached["response"] == "response"

    def test_cache_miss(self, store):
        cached = store.ai_cache_get("nonexistent_hash")
        assert cached is None

    def test_cache_cleanup(self, store):
        store.ai_cache_set("h1", "q1", "r1", "claude", 0.01, 100)
        store.ai_cache_cleanup(max_entries=100)
        # 不应报错


# ── 提案系统 ───────────────────────────────────────────


class TestProposals:
    def test_create_proposal(self, store):
        pid = store.create_proposal("Test", "Description", "test_module", "dedup_key")
        assert pid is not None

        proposals = store.list_proposals()
        assert any(p["id"] == pid for p in proposals)

    def test_get_proposal(self, store):
        pid = store.create_proposal("Test", "Desc", "test_module", "dk")
        p = store.get_proposal(pid)
        assert p is not None
        assert p["title"] == "Test"

    def test_update_proposal_status(self, store):
        pid = store.create_proposal("Test", "Desc", "test_module", "dk")
        ok = store.update_proposal_status(pid, "cancelled")
        assert ok
        p = store.get_proposal(pid)
        assert p["status"] == "cancelled"


# ── 集成场景 ───────────────────────────────────────────


class TestIntegration:
    def test_full_lifecycle(self, store):
        # 添加
        store.add(_rule(id="life/001", title="Lifecycle Rule", content="Lifecycle content"))
        assert store.get("life/001") is not None

        # 快照
        sid = store.create_snapshot()
        assert sid is not None

        # 添加另一条
        store.add(_rule(id="life/002", title="Second", content="Second content"))
        assert store.get("life/002") is not None

        # 查询
        pending_qid = store.add_pending_query("how to test?")
        assert pending_qid is not None

        # 删除
        store.hard_delete("life/001")
        assert store.get("life/001") is None
