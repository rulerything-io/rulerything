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
存储层单元测试
"""

import json
import tempfile
from pathlib import Path
from datetime import datetime

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from rule import Rule
from storage import RuleStorage


def _make_rule(rule_id: str, category: str = "test",
               content: str = "test content") -> Rule:
    return Rule(
        id=rule_id,
        title=f"Rule {rule_id}",
        content=content,
        category=category,
        tags=[category],
        confidence=0.5,
    )


def _dump_to(tmpdir: str, category: str, rules: list[Rule]):
    """直接写入 JSONL（绕过 dedup，用于构造测试数据）。"""
    fpath = Path(tmpdir) / f"{category}.jsonl"
    with open(fpath, "w", encoding="utf-8") as f:
        for r in rules:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")


# ── Rule 实体测试 ─────────────────────────────────


class TestRuleEntity:
    def test_basic_creation(self):
        r = Rule(id="test/001", title="测试规则", content="测试内容")
        assert r.id == "test/001"
        assert r.confidence == 0.5
        assert r.version == 1
        assert r.created_at is not None
        assert r.is_expired is False
        assert r.is_duplicate is False

    def test_content_hash_equal(self):
        r1 = Rule(id="a/001", title="A", content="Hello World")
        r2 = Rule(id="b/001", title="B", content="Hello World")
        r3 = Rule(id="c/001", title="C", content="Hello World  ")
        assert r1.content_hash == r2.content_hash
        assert r1.content_hash == r3.content_hash   # strip 后相同

    def test_content_hash_different(self):
        r1 = Rule(id="a/001", title="A", content="Hello")
        r2 = Rule(id="b/001", title="B", content="World")
        assert r1.content_hash != r2.content_hash

    def test_is_expired(self):
        r = Rule(id="test/001", title="T", content="C",
                 expires_at=datetime(2020, 1, 1))
        assert r.is_expired is True

    def test_is_not_expired(self):
        r = Rule(id="test/001", title="T", content="C", expires_at=None)
        assert r.is_expired is False

    def test_is_duplicate(self):
        r = Rule(id="test/001", title="T", content="C",
                 duplicate_of="master/001")
        assert r.is_duplicate is True
        assert r.effective_id == "master/001"

    def test_is_not_duplicate(self):
        r = Rule(id="test/001", title="T", content="C", duplicate_of=None)
        assert r.is_duplicate is False
        assert r.effective_id == "test/001"

    def test_serialize_roundtrip(self):
        r1 = Rule(
            id="test/001", title="测试规则", content="测试内容",
            category="performance", tags=["python", "test"],
            confidence=0.8, verifier="manual", version=3,
            evolution_log=["v1: init", "v2: update"], hit_count=42,
            created_at=datetime(2024, 6, 1, 10, 0, 0),
        )
        d = r1.to_dict()
        r2 = Rule.from_dict(d)
        for attr in ("id", "title", "content", "category", "tags",
                     "confidence", "version", "hit_count", "created_at"):
            assert getattr(r1, attr) == getattr(r2, attr), f"{attr} mismatch"
        assert r1.content_hash == r2.content_hash

    def test_serialize_json_compatible(self):
        r = Rule(id="test/001", title="T", content="C")
        json_str = json.dumps(r.to_dict(), ensure_ascii=False)
        assert isinstance(json_str, str)

    def test_record_hit(self):
        r = Rule(id="test/001", title="T", content="C")
        assert r.hit_count == 0
        assert r.last_hit is None
        r.record_hit()
        assert r.hit_count == 1
        assert r.last_hit is not None

    def test_evolve(self):
        r = Rule(id="test/001", title="T", content="C")
        r.evolve("调整置信度")
        assert r.version == 2
        assert r.evolution_log == ["调整置信度"]


# ── Storage CRUD 测试 ────────────────────────────


class TestRuleStorage:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = RuleStorage(self.tmpdir)

    def test_add_and_get(self):
        r = _make_rule("test/001")
        ok, msg = self.store.add(r)
        assert ok, msg
        assert self.store.get("test/001") is not None

    def test_add_duplicate_id(self):
        r1 = _make_rule("test/001")
        r2 = _make_rule("test/001")
        assert self.store.add(r1)[0]
        ok, msg = self.store.add(r2)
        assert not ok
        assert "已存在" in msg

    def test_add_duplicate_content(self):
        r1 = _make_rule("test/001", content="相同内容")
        r2 = _make_rule("test/002", content="相同内容")
        assert self.store.add(r1)[0]
        ok, msg = self.store.add(r2)
        assert not ok
        assert "内容重复" in msg

    def test_add_unique_content_same_category(self):
        assert self.store.add(_make_rule("test/001", content="A"))[0]
        assert self.store.add(_make_rule("test/002", content="B"))[0]
        assert len(self.store.list()) == 2

    def test_get_nonexistent(self):
        assert self.store.get("nonexistent") is None

    def test_get_redirects_duplicate(self):
        """get() 应自动跟随 duplicate_of 重定向。"""
        master = _make_rule("master/001", content="主规则")
        assert self.store.add(master)[0]
        dup = _make_rule("dup/001", content="不同内容才可添加")
        assert self.store.add(dup)[0]
        # 标记 dup 为 master 的重复
        assert self.store.update("dup/001", duplicate_of="master/001")
        fetched = self.store.get("dup/001")
        assert fetched is not None
        assert fetched.id == "master/001"

    def test_update(self):
        assert self.store.add(_make_rule("test/001"))[0]
        assert self.store.update("test/001", confidence=0.9, title="新标题")
        r = self.store.get("test/001")
        assert r.confidence == 0.9
        assert r.title == "新标题"

    def test_update_nonexistent(self):
        assert not self.store.update("nope", confidence=0.5)

    def test_soft_delete(self):
        assert self.store.add(_make_rule("test/001"))[0]
        assert self.store.delete("test/001")
        r = self.store.get("test/001")
        assert r.expires_at is not None     # 设置了过期时间
        assert r.is_expired                 # 已过期
        assert len(self.store.list()) == 0  # list 已排除

    def test_hard_delete(self):
        assert self.store.add(_make_rule("test/001"))[0]
        assert self.store.hard_delete("test/001")
        assert self.store.get("test/001") is None

    def test_list_all(self):
        for i in range(5):
            self.store.add(_make_rule(f"test/{i:03d}", content=f"内容{i}"))
        assert len(self.store.list()) == 5

    def test_list_filter_by_category(self):
        self.store.add(_make_rule("perf/001", category="performance"))
        self.store.add(_make_rule("sec/001", category="security"))
        assert len(self.store.list(category="performance")) == 1
        assert len(self.store.list(category="security")) == 1
        assert len(self.store.list(category="philosophy")) == 0

    def test_list_excludes_expired(self):
        self.store.add(_make_rule("test/001", content="内容A"))
        self.store.add(_make_rule("test/002", content="内容B"))
        self.store.delete("test/001")
        remaining = self.store.list()
        assert len(remaining) == 1
        assert remaining[0].id == "test/002"


# ── 去重测试 ─────────────────────────────────────


class TestDedup:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_no_duplicates(self):
        """没有重复时 find_duplicates 返回空。"""
        _dump_to(self.tmpdir, "test", [
            _make_rule("a/001", content="内容A"),
            _make_rule("a/002", content="内容B"),
        ])
        store = RuleStorage(self.tmpdir)
        assert len(store.find_duplicates()) == 0

    def test_find_duplicates(self):
        """查找同分类下的重复规则。"""
        _dump_to(self.tmpdir, "test", [
            _make_rule("a/001", content="相同内容"),
            _make_rule("a/002", content="相同内容"),
        ])
        store = RuleStorage(self.tmpdir)
        dups = store.find_duplicates()
        assert len(dups) == 1

    def test_dedup_dry_run(self):
        """dry_run 预览应标记置信度高的为主规则。"""
        Path(self.tmpdir, "test.jsonl").write_text(
            json.dumps(_make_rule("a/001", content="相同内容").to_dict()
                       | {"confidence": 0.9}, ensure_ascii=False) + "\n" +
            json.dumps(_make_rule("a/002", content="相同内容").to_dict()
                       | {"confidence": 0.5}, ensure_ascii=False) + "\n",
            encoding="utf-8"
        )
        store = RuleStorage(self.tmpdir)
        previews = store.dedup_dry_run()
        assert len(previews) == 1
        assert previews[0]["master_id"] == "a/001"
        assert len(previews[0]["duplicates"]) == 1
        assert previews[0]["duplicates"][0]["id"] == "a/002"

    def test_dedup_apply(self):
        """执行去重后，重复规则设置 duplicate_of。"""
        Path(self.tmpdir, "test.jsonl").write_text(
            json.dumps(_make_rule("a/001", content="相同内容").to_dict() | {"confidence": 0.9},
                       ensure_ascii=False) + "\n" +
            json.dumps(_make_rule("a/002", content="相同内容").to_dict() | {"confidence": 0.5},
                       ensure_ascii=False) + "\n",
            encoding="utf-8"
        )
        store = RuleStorage(self.tmpdir)
        results = store.dedup_apply()
        assert len(results) == 1
        assert results[0]["rule_id"] == "a/002"
        assert results[0]["duplicate_of"] == "a/001"

    def test_dedup_after_apply_list_excludes(self):
        """去重后 list 不应包含被标记为重复的规则。"""
        Path(self.tmpdir, "test.jsonl").write_text(
            json.dumps(_make_rule("a/001", content="相同内容").to_dict() | {"confidence": 0.9},
                       ensure_ascii=False) + "\n" +
            json.dumps(_make_rule("a/002", content="相同内容").to_dict() | {"confidence": 0.5},
                       ensure_ascii=False) + "\n",
            encoding="utf-8"
        )
        store = RuleStorage(self.tmpdir)
        store.dedup_apply()
        ids = [r.id for r in store.list()]
        assert "a/001" in ids
        assert "a/002" not in ids

    def test_dedup_apply_redirects_get(self):
        """去重后 get(dup_rule) 应返回主规则。"""
        Path(self.tmpdir, "test.jsonl").write_text(
            json.dumps(_make_rule("a/001", content="相同内容").to_dict() | {"confidence": 0.9},
                       ensure_ascii=False) + "\n" +
            json.dumps(_make_rule("a/002", content="相同内容").to_dict() | {"confidence": 0.5},
                       ensure_ascii=False) + "\n",
            encoding="utf-8"
        )
        store = RuleStorage(self.tmpdir)
        store.dedup_apply()
        master = store.get("a/002")
        assert master is not None
        assert master.id == "a/001"

    def test_dedup_same_content_different_category(self):
        """不同分类的相同内容不算重复。"""
        _dump_to(self.tmpdir, "performance", [
            _make_rule("perf/001", content="相同内容", category="performance"),
        ])
        _dump_to(self.tmpdir, "security", [
            _make_rule("sec/001", content="相同内容", category="security"),
        ])
        store = RuleStorage(self.tmpdir)
        assert len(store.find_duplicates()) == 0


# ── 持久化测试 ───────────────────────────────────


class TestPersistence:
    def test_save_and_reload(self):
        tmpdir = tempfile.mkdtemp()
        store1 = RuleStorage(tmpdir)
        store1.add(_make_rule("persist/001", content="测试数据"))
        store1.add(_make_rule("persist/002", content="测试数据2"))

        store2 = RuleStorage(tmpdir)
        assert len(store2.list()) == 2
        assert store2.get("persist/001") is not None

    def test_seed_data_loads(self):
        """测试默认种子数据的加载。"""
        store = RuleStorage("data")
        stats = store.stats()
        assert stats["active_rules"] >= 11
        for cat in ("philosophy", "performance", "pattern", "security"):
            assert cat in stats["categories"]


# ── 边界测试 ─────────────────────────────────────


class TestEdgeCases:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = RuleStorage(self.tmpdir)

    def test_empty_storage(self):
        assert self.store.list() == []
        assert self.store.stats()["total_rules"] == 0

    def test_empty_category_file(self):
        Path(self.tmpdir, "empty.jsonl").write_text("", encoding="utf-8")
        store = RuleStorage(self.tmpdir)
        assert store.list() == []

    def test_malformed_line(self):
        Path(self.tmpdir, "test.jsonl").write_text(
            '{"id": "test/001", "title": "T", "content": "C"}\n'
            'not json\n'
            '{"id": "test/002", "title": "T2", "content": "C2"}\n',
            encoding="utf-8",
        )
        store = RuleStorage(self.tmpdir)
        assert len(store.list()) == 2

    def test_update_persists_to_file(self):
        self.store.add(_make_rule("test/001", content="内容"))
        self.store.update("test/001", title="新标题")
        store2 = RuleStorage(self.tmpdir)
        assert store2.get("test/001").title == "新标题"

    def test_large_rule_content(self):
        large = "x" * 9000
        assert self.store.add(
            _make_rule("large/001", content=large)
        )[0]
        assert len(self.store.get("large/001").content) == 9000

    def test_special_chars(self):
        content = "特殊字符: \n\t\"'\\u0041"
        assert self.store.add(
            _make_rule("special/001", content=content)
        )[0]
        d = self.store.get("special/001").to_dict()
        assert isinstance(json.dumps(d, ensure_ascii=False), str)
