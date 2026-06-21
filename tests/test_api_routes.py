"""
FastAPI 路由集成测试 — 验证 refactored API 端点
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

# 设置临时数据目录避免影响生产数据
_test_tmpdir = tempfile.mkdtemp()
os.environ["RULERYTHING_DATA_DIR"] = _test_tmpdir

# 直接 bootstrap 但用测试数据目录
from core.state import state


@pytest.fixture(scope="module")
def client():
    """使用内存配置启动 FastAPI TestClient。"""
    from main import app
    # 减少 bootstrap 噪音
    state.log_level = "WARNING"
    with TestClient(app) as c:
        yield c


# ── 系统端点 ───────────────────────────────────────────


class TestSystemEndpoints:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data

    def test_ready(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200

    def test_stats(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 200

    def test_logs(self, client):
        resp = client.get("/logs?limit=5")
        assert resp.status_code == 200


# ── 规则端点 ───────────────────────────────────────────


class TestRuleEndpoints:
    def test_list_rules(self, client):
        resp = client.get("/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_add_rule(self, client):
        import uuid
        rid = f"api_test/{uuid.uuid4().hex[:8]}"
        content = f"Unique API test content {uuid.uuid4().hex}"
        rule = {
            "id": rid,
            "title": "API Test Rule",
            "content": content,
            "category": "test",
            "tags": ["test"],
            "confidence": 0.5,
        }
        resp = client.post("/add-rule", json=rule)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json()["ok"] is True

        from core.state import state
        assert state.index.sorted_titles.count("API Test Rule") == 1

    def test_add_duplicate_rule(self, client):
        import uuid
        rid = f"dup_test/{uuid.uuid4().hex[:8]}"
        content = f"Unique dup content {uuid.uuid4().hex}"
        rule = {
            "id": rid,
            "title": "Dup",
            "content": content,
            "category": "test",
        }
        resp1 = client.post("/add-rule", json=rule)
        assert resp1.status_code == 200, f"First add failed: {resp1.text}"

        resp2 = client.post("/add-rule", json=rule)
        assert resp2.status_code == 409

    def test_dedup_dry_run(self, client):
        resp = client.post("/dedup/dry-run", json={})
        assert resp.status_code == 200

    def test_evolution_stats(self, client):
        resp = client.get("/evolution/stats")
        assert resp.status_code == 200


# ── 搜索端点 ───────────────────────────────────────────


class TestSearchEndpoint:
    def test_search_exact(self, client):
        resp = client.post("/search", json={
            "query": "python",
            "search_type": "exact",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "confidence" in data

    def test_search_with_category(self, client):
        resp = client.post("/search", json={
            "query": "test",
            "category": "test",
        })
        assert resp.status_code == 200

    def test_search_contract_rejects_blank_query(self, client):
        resp = client.post("/search", json={"query": "", "search_type": "exact"})
        assert resp.status_code == 422

    def test_search_contract_rejects_unknown_type(self, client):
        resp = client.post("/search", json={"query": "python", "search_type": "fuzzy"})
        assert resp.status_code == 422

    def test_search_limit_is_honored(self, client):
        resp = client.post("/search", json={
            "query": "shell", "search_type": "tag", "limit": 8,
        })
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 8

    def test_ai_fallback_latency_and_result_are_observed(self, client):
        import time
        from core.state import state

        class SlowBridge:
            @staticmethod
            def is_enabled():
                return True

            @staticmethod
            def enhance_query(query, search_context=None):
                time.sleep(0.02)
                return {"source": "ai", "content": "fallback", "confidence": 0.5}

        previous = state.ai_bridge
        state.ai_bridge = SlowBridge()
        try:
            resp = client.post("/search", json={
                "query": "no-such-rule-for-ai-fallback", "search_type": "exact",
            })
        finally:
            state.ai_bridge = previous
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 1
        assert resp.json()["latency_ms"] >= 20

    def test_warmup(self, client):
        resp = client.post("/warmup")
        assert resp.status_code == 200


# ── v3 端点 ────────────────────────────────────────────


class TestV3Endpoints:
    def test_v3_status(self, client):
        resp = client.get("/v3/status")
        assert resp.status_code == 200

    def test_v3_health(self, client):
        resp = client.get("/v3/health")
        assert resp.status_code == 200

    def test_v3_config(self, client):
        # POST — Body() 参数需要 JSON
        resp = client.post("/v3/config",
                           json={"key": "test_key", "value": "test_val"})
        assert resp.status_code in (200, 400, 404), f"Unexpected: {resp.status_code}: {resp.text}"

        # GET
        resp = client.get("/v3/config?key=test_key")
        assert resp.status_code in (200, 404)

    def test_audit_logs(self, client):
        resp = client.get("/audit/logs?limit=5")
        assert resp.status_code == 200


# ── AI 端点 ────────────────────────────────────────────


class TestAIEndpoints:
    def test_ai_budget(self, client):
        resp = client.get("/ai/budget")
        assert resp.status_code == 200

    def test_ai_stats(self, client):
        resp = client.get("/ai/stats")
        assert resp.status_code == 200

    def test_ai_pending(self, client):
        resp = client.get("/ai/pending")
        assert resp.status_code == 200


# ── Phase 端点 ─────────────────────────────────────────


class TestPhaseEndpoints:
    def test_entropy_report(self, client):
        resp = client.get("/entropy/report")
        assert resp.status_code == 200

    def test_entropy_suggestions(self, client):
        resp = client.get("/entropy/suggestions")
        assert resp.status_code == 200

    def test_immune_scan(self, client):
        resp = client.post("/immune/scan", json={"auto_cleanup": False})
        assert resp.status_code == 200


# ── 认证 ───────────────────────────────────────────────


class TestAuth:
    """API 认证测试。"""

    def test_auth_module_importable(self):
        from core.auth import require_write_token
        assert callable(require_write_token)

    def test_auth_disabled_by_default(self, client):
        """api_key_required=False 时 POST 无需认证。"""
        import uuid
        rid = f"auth_test/{uuid.uuid4().hex[:8]}"
        rule = {
            "id": rid, "title": "Auth Test",
            "content": f"Unique auth test content {uuid.uuid4().hex}",
            "category": "test",
        }
        resp = client.post("/add-rule", json=rule)
        assert resp.status_code == 200, f"认证关闭时应放行: {resp.text}"

    def test_write_endpoints_require_auth_on_real_config(self):
        """验证所有写端点函数签名包含 auth 参数。"""
        import routes.rules
        import routes.search
        import routes.system
        import routes.v3
        import routes.ai
        import routes.phase3

        for mod in [routes.rules, routes.search, routes.system,
                    routes.v3, routes.ai, routes.phase3]:
            for name in dir(mod):
                obj = getattr(mod, name)
                if hasattr(obj, "methods") and "POST" in obj.methods:
                    # 确保 endpoint 接受 auth 参数
                    import inspect
                    sig = inspect.signature(obj.__wrapped__ if hasattr(obj, "__wrapped__") else obj)
                    assert "auth" in sig.parameters, f"{mod.__name__}.{name} 缺少 auth 参数"


# ── 向后兼容 ───────────────────────────────────────────


class TestBackwardCompat:
    def test_enhance_prompt_exists(self):
        from main import enhance_prompt
        assert callable(enhance_prompt)

    def test_app_exists(self):
        from main import app
        assert app is not None

    def test_second_running_app_is_rejected(self, client):
        import tempfile
        from main import create_app
        other = create_app(data_dir=tempfile.mkdtemp(), start_background=False)
        with pytest.raises(RuntimeError, match="Only one"):
            with TestClient(other):
                pass
        assert client.get("/health").status_code == 200
