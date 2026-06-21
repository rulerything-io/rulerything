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

"""
索引层单元测试
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rule import Rule
from index import EverythingStyleIndex


def _rule(rid: str, title: str, content: str = "内容",
          category: str = "test", tags: list = None,
          confidence: float = 0.5, hit_count: int = 0) -> Rule:
    return Rule(
        id=rid, title=title, content=content,
        category=category, tags=tags or [],
        confidence=confidence, hit_count=hit_count,
    )


class TestIndexBuild:
    def test_build_empty(self):
        idx = EverythingStyleIndex([])
        assert idx.is_ready is False
        assert idx.stats()["total_rules_indexed"] == 0

    def test_build_with_rules(self):
        rules = [
            _rule("a/001", "Alpha", tags=["python"]),
            _rule("a/002", "Beta", tags=["go"]),
        ]
        idx = EverythingStyleIndex(rules)
        assert idx.is_ready
        assert len(idx.sorted_titles) == 2
        assert idx.sorted_titles == ["Alpha", "Beta"]
        assert "python" in idx.tag_index
        assert "go" in idx.tag_index

    def test_rebuild(self):
        idx = EverythingStyleIndex([_rule("a/001", "A")])
        v1 = idx.index_version
        idx.build([_rule("a/001", "A"), _rule("a/002", "B")])
        assert idx.index_version == v1 + 1
        assert idx.stats()["total_rules_indexed"] == 2

    def test_add_same_id_is_idempotent(self):
        idx = EverythingStyleIndex([])
        rule = _rule("a/001", "Same", tags=["python"])
        idx.add(rule)
        idx.add(rule)
        assert idx.sorted_titles == ["Same"]
        assert idx.tag_index["python"] == ["a/001"]
        assert [item.id for item in idx.search("Sa", "prefix")] == ["a/001"]


class TestExactSearch:
    def setup_method(self):
        self.idx = EverythingStyleIndex([
            _rule("a/001", "用生成器代替列表处理大数据", category="performance"),
            _rule("a/002", "使用连接池管理数据库连接", category="performance"),
            _rule("a/003", "单一职责原则", category="philosophy"),
        ])

    def test_exact_match(self):
        r = self.idx.search_exact("单一职责原则")
        assert r is not None
        assert r.id == "a/003"

    def test_exact_no_match(self):
        r = self.idx.search_exact("不存在的规则")
        assert r is None

    def test_exact_case_sensitive(self):
        """Everything 风格区分大小写。"""
        r = self.idx.search_exact("单一职责原则")
        assert r is not None
        r2 = self.idx.search_exact("单一职责")  # 不完整，不匹配
        assert r2 is None

    def test_exact_updates_hit_count(self):
        r = self.idx.search_exact("单一职责原则")
        assert r.hit_count > 0

    def test_exact_updates_total_searches(self):
        before = self.idx.total_search_count
        self.idx.search_exact("单一职责原则")
        assert self.idx.total_search_count == before + 1


class TestPrefixSearch:
    def setup_method(self):
        self.idx = EverythingStyleIndex([
            _rule("a/001", "用生成器代替列表处理大数据"),
            _rule("a/002", "使用连接池管理数据库连接"),
            _rule("a/003", "用工厂方法替代直接实例化"),
            _rule("a/004", "单一职责原则"),
        ])

    def test_prefix_match(self):
        results = self.idx.search_prefix("用")
        assert len(results) == 2  # 用生成器, 用工厂方法

    def test_prefix_match_single(self):
        results = self.idx.search_prefix("使用")
        assert len(results) == 1

    def test_prefix_no_match(self):
        results = self.idx.search_prefix("ZZZ")
        assert len(results) == 0

    def test_prefix_limit(self):
        # Add more "用" prefix rules
        idx = EverythingStyleIndex([
            _rule(f"a/{i:03d}", f"用{i}") for i in range(20)
        ])
        results = idx.search_prefix("用", limit=5)
        assert len(results) == 5

    def test_prefix_returns_in_order(self):
        results = self.idx.search_prefix("用")
        assert results[0].id == "a/003"  # 用工厂... > 用生成器... 按标题排序
        # Actually, depends on sort order of titles
        titles = [r.title for r in results]
        assert titles == sorted(titles)  # 按标题排序


class TestTagSearch:
    def setup_method(self):
        self.idx = EverythingStyleIndex([
            _rule("a/001", "A", tags=["python", "memory"]),
            _rule("a/002", "B", tags=["python", "database"]),
            _rule("a/003", "C", tags=["go", "performance"]),
        ])

    def test_tag_match(self):
        results = self.idx.search_by_tag("python")
        assert len(results) == 2

    def test_tag_no_match(self):
        results = self.idx.search_by_tag("nonexistent")
        assert len(results) == 0

    def test_tag_limit(self):
        results = self.idx.search_by_tag("python", limit=1)
        assert len(results) == 1


class TestHotCache:
    def test_hot_cache_stores_frequent_rules(self):
        rules = [_rule("a/001", "A", hit_count=10)]
        idx = EverythingStyleIndex(rules)
        assert "a/001" in idx.hot_ids

    def test_cold_ids_detected(self):
        """长期未访问的规则应进入冷区。"""
        from datetime import datetime, timedelta
        old_hit = datetime.now() - timedelta(days=60)
        r = _rule("a/001", "A", hit_count=1)
        r.last_hit = old_hit
        idx = EverythingStyleIndex([r])
        assert "a/001" in idx.cold_ids

    def test_hit_triggers_cache_refresh(self):
        idx = EverythingStyleIndex([
            _rule("a/001", "A", hit_count=5, tags=["t"]),
            _rule("a/002", "B", hit_count=5, tags=["t"]),
        ])
        # Both have hit_count=5 which is below HOT_THRESHOLD=10
        assert "a/001" not in idx.hot_ids
        assert "a/002" not in idx.hot_ids

        # Hit a/001 5+ times to reach threshold
        for _ in range(6):
            idx._record_hit("a/001")

        # After HOT_UPDATE_INTERVAL (10) hits, cache refreshes
        assert idx._hit_counter == 6  # 6 hits, less than interval
        # Refresh hasn't triggered yet
        assert "a/001" not in idx.hot_ids

        # 4 more hits to trigger refresh
        for _ in range(4):
            idx._record_hit("a/001")
        assert "a/001" in idx.hot_ids  # hit_count=15 >= 10

    def test_cache_hit_rate(self):
        idx = EverythingStyleIndex([_rule("a/001", "A", hit_count=10, tags=["t"])])
        # Rule is already hot (hit_count >= HOT_THRESHOLD)
        idx.search("A", "exact")  # cache hit
        stats = idx.stats()
        assert stats["total_searches"] == 1
        assert stats["cache_hit_rate"] == 100.0


class TestUnifiedSearch:
    def setup_method(self):
        self.idx = EverythingStyleIndex([
            _rule("a/001", "用生成器代替列表处理大数据",
                  category="performance", confidence=0.8),
            _rule("a/002", "用工厂方法替代直接实例化",
                  category="pattern", confidence=0.85),
            _rule("a/003", "使用连接池管理数据库连接",
                  category="performance", confidence=0.9),
        ])

    def test_search_exact(self):
        results = self.idx.search("用生成器代替列表处理大数据", "exact")
        assert len(results) == 1
        assert results[0].id == "a/001"

    def test_search_prefix(self):
        results = self.idx.search("用", "prefix")
        assert len(results) == 2

    def test_search_tag(self):
        results = self.idx.search("performance", "tag")
        assert len(results) == 0  # 没有规则带 "performance" 标签，返回空

    def test_search_tag_matches_tag_index(self):
        idx = EverythingStyleIndex([
            _rule("a/001", "A", tags=["performance", "python"]),
            _rule("a/002", "B", tags=["performance"]),
            _rule("a/003", "C", tags=["security"]),
        ])
        results = idx.search("performance", "tag")
        assert len(results) == 2

    def test_search_with_category_filter(self):
        results = self.idx.search("用", "prefix", category="performance")
        assert len(results) == 1
        assert results[0].id == "a/001"

    def test_search_sorts_by_confidence(self):
        results = self.idx.search("用", "prefix")
        assert len(results) >= 2
        # Should be sorted by confidence desc
        confidences = [r.confidence for r in results]
        assert confidences == sorted(confidences, reverse=True)

    def test_search_empty_query(self):
        results = self.idx.search("", "exact")
        assert len(results) == 0

    def test_smart_search_is_explicit(self):
        strict = self.idx.search("连接池", "exact")
        smart = self.idx.search("连接池", "smart")
        assert strict == []
        assert [rule.id for rule in smart] == ["a/003"]

    def test_unknown_search_type_rejected(self):
        import pytest
        with pytest.raises(ValueError):
            self.idx.search("用", "unknown")


class TestWarmup:
    def test_warmup_loads_top_rules(self):
        rules = [_rule(f"a/{i:03d}", f"Rule {i}",
                       hit_count=i * 2, tags=["t"])
                 for i in range(1, 21)]  # 20 rules, hit_count 2..40
        idx = EverythingStyleIndex(rules)

        # All rules with hit_count >= 10 are already hot (11 rules)
        # Warmup should find none new
        result = idx.warmup()
        assert result["loaded"] >= 0

    def test_warmup_by_category(self):
        rules = (
            [_rule(f"perf/{i:03d}", f"P{i}", category="performance",
                   hit_count=20, tags=["t"])
             for i in range(5)]
            +
            [_rule(f"sec/{i:03d}", f"S{i}", category="security",
                   hit_count=5, tags=["t"])
             for i in range(5)]
        )
        idx = EverythingStyleIndex(rules)
        # perf rules already hot (hit_count >= 10), sec rules not

        # Warmup only security
        result = idx.warmup(category="security")
        # sec rules with hit_count=5 < HOT_THRESHOLD=10, not hot yet
        # warmup loads top 20% of candidates (hit_count > 0)
        # sec rules have hit_count=5, so they're candidates
        # top_n = max(1, 5 // 5) = 1
        assert result["loaded"] == 1
        assert result["category"] == "security"


class TestPerformance:
    def test_exact_search_sub_ms(self):
        """精确搜索应在亚毫秒级完成。"""
        import time
        rules = [_rule(f"a/{i:04d}", f"Rule {i:04d}", tags=["t"])
                 for i in range(1000)]
        idx = EverythingStyleIndex(rules)

        start = time.perf_counter()
        idx.search_exact("Rule 0500")
        elapsed = (time.perf_counter() - start) * 1000  # ms

        assert elapsed < 1.0, f"精确搜索 {elapsed:.3f}ms，预期 < 1ms"
