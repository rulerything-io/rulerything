# Copyright 2026 Rulerything Project Authors
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
集成测试 — 配置加载、CLI、enhance_prompt、端到端流程
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_config
from main import enhance_prompt


# ── 配置测试 ───────────────────────────────────────


class TestConfig:
    def test_defaults(self):
        config = load_config(path="nonexistent.yaml")
        assert config["server"]["host"] == "0.0.0.0"
        assert config["server"]["port"] == 8000
        assert config["index"]["hot_threshold"] == 10
        assert config["logging"]["level"] == "INFO"

    def test_load_from_file(self):
        config = load_config()
        assert config["server"]["host"] == "0.0.0.0"
        assert config["index"]["hot_threshold"] == 10

    def test_env_override(self):
        os.environ["RULES_INDEX_HOT_THRESHOLD"] = "42"
        os.environ["RULES_SERVER_PORT"] = "9000"
        try:
            config = load_config(path="nonexistent.yaml")
            assert config["index"]["hot_threshold"] == 42
            assert config["server"]["port"] == 9000
        finally:
            del os.environ["RULES_INDEX_HOT_THRESHOLD"]
            del os.environ["RULES_SERVER_PORT"]

    def test_env_bool(self):
        os.environ["RULES_CACHE_PREHEAT_ON_START"] = "false"
        try:
            config = load_config(path="nonexistent.yaml")
            assert config["cache"]["preheat_on_start"] is False
        finally:
            del os.environ["RULES_CACHE_PREHEAT_ON_START"]

    def test_cli_override(self):
        config = load_config(
            path="nonexistent.yaml",
            cli_overrides={"server": {"port": 3000}},
        )
        assert config["server"]["port"] == 3000

    def test_cli_overrides_env(self):
        os.environ["RULES_SERVER_PORT"] = "9000"
        try:
            config = load_config(
                path="nonexistent.yaml",
                cli_overrides={"server": {"port": 3000}},
            )
            assert config["server"]["port"] == 3000
        finally:
            del os.environ["RULES_SERVER_PORT"]

    def test_deep_merge(self):
        config = load_config(
            path="nonexistent.yaml",
            cli_overrides={"server": {"port": 3000}},
        )
        assert config["server"]["host"] == "0.0.0.0"
        assert config["server"]["port"] == 3000


# ── CLI 测试 ────────────────────────────────────────


class TestCLI:
    def _run(self, *args) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "cli.py", *args],
            capture_output=True, text=True, encoding="utf-8",
            cwd=Path(__file__).parent.parent,
            env=env,
        )

    def test_health(self):
        r = self._run("health")
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert len(r.stdout) > 0

    def test_stats(self):
        r = self._run("stats")
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert len(r.stdout) > 0

    def test_list(self):
        r = self._run("list")
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert len(r.stdout) > 0

    def test_list_category(self):
        r = self._run("list", "--category", "performance")
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert "performance" in r.stdout

    def test_search_exact(self):
        # Use ASCII-safe query via unicode escapes in the subprocess call
        query = "\u7528\u751f\u6210\u5668\u4ee3\u66ff\u5217\u8868\u5904\u7406\u5927\u6570\u636e"
        r = self._run("search", query)
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert "performance/001" in r.stdout

    def test_search_prefix(self):
        query = "\u7528\u751f\u6210\u5668"
        r = self._run("search", query, "--type", "prefix")
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert "performance/001" in r.stdout

    def test_search_tag(self):
        r = self._run("search", "python", "--type", "tag")
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert len(r.stdout) > 0

    def test_get(self):
        r = self._run("get", "performance/001")
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert "performance/001" in r.stdout

    def test_get_nonexistent(self):
        r = self._run("get", "nonexistent")
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert len(r.stdout) > 0

    def test_dedup_dry_run(self):
        r = self._run("dedup", "--dry-run")
        assert r.returncode == 0, f"stderr: {r.stderr}"

    def test_warmup(self):
        r = self._run("warmup")
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert len(r.stdout) > 0

    def test_warmup_category(self):
        r = self._run("warmup", "--category", "performance")
        assert r.returncode == 0, f"stderr: {r.stderr}"

    def test_config(self):
        r = self._run("config")
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert "server" in r.stdout
        assert "index" in r.stdout

    def test_no_args(self):
        r = self._run()
        assert r.returncode == 0

    def test_add_rule(self):
        rule_data = {
            "id": "cli-test/001",
            "title": "CLI Test Rule",
            "content": "Rule added via CLI test",
            "category": "test",
            "tags": ["test"],
        }
        tmpf = Path(tempfile.mktemp(suffix=".json"))
        tmpf.write_text(json.dumps(rule_data, ensure_ascii=False), encoding="utf-8")
        try:
            r = self._run("add", str(tmpf))
            assert r.returncode == 0, f"stderr: {r.stderr}"
            assert "cli-test/001" in r.stdout or "OK" in r.stdout
        finally:
            tmpf.unlink(missing_ok=True)
            from storage import RuleStorage
            store = RuleStorage("data")
            store.hard_delete("cli-test/001")


# ── enhance_prompt 测试 ────────────────────────────


class TestEnhancePrompt:
    def test_python_query(self):
        prompt = enhance_prompt("Python big data processing")
        assert "\u89c4\u5219 1" in prompt or "Rule" in prompt
        assert "python/" in prompt  # v2.0 BM25 优先匹配 title 含 Python 的规则

    def test_prefix_query(self):
        prompt = enhance_prompt("use generator")
        assert "\u89c4\u5219 1" in prompt or "Rule" in prompt

    def test_tag_query(self):
        prompt = enhance_prompt("python memory")
        assert "\u89c4\u5219 1" in prompt or "Rule" in prompt

    def test_no_match_returns_raw(self):
        prompt = enhance_prompt("zzzznothing123")
        assert prompt == "zzzznothing123"

    def test_contains_sections(self):
        prompt = enhance_prompt("Python big data")
        assert "\u7528\u6237\u95ee\u9898" in prompt  # 用户问题
        assert "\u56de\u7b54\u8981\u6c42" in prompt  # 回答要求

    def test_multiple_queries_return_unique(self):
        prompt = enhance_prompt("Python database connection pool")
        assert "\u89c4\u5219 1" in prompt or "Rule" in prompt
        assert len(prompt) > 50

    def test_security_query_keyword(self):
        prompt = enhance_prompt("SQL injection prevention")
        assert "\u89c4\u5219 1" in prompt or "Rule" in prompt
