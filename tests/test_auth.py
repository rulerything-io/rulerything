"""
认证模块测试 — core/auth.py Bearer Token
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from fastapi import HTTPException

from core.auth import require_write_token


class MockState:
    """模拟 state，让认证模块可测试。"""
    config = {}


@pytest.fixture
def patch_state(monkeypatch):
    """替换 core.state.state 为可控 mock。"""
    import core.auth as auth_mod
    mock = MockState()
    monkeypatch.setattr(auth_mod, "state", mock)
    return mock


def _make_app():
    """创建带 auth 依赖的测试 app。"""
    app = FastAPI()

    @app.post("/test-write")
    async def test_write(auth=Depends(require_write_token)):
        return {"status": "ok"}

    return app


class TestAuthDisabled:
    """api_key_required = False 时所有请求放行。"""

    def test_no_token_allowed(self, patch_state):
        patch_state.config = {"security": {"api_key_required": False}}
        app = _make_app()
        with TestClient(app) as c:
            resp = c.post("/test-write")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_bad_token_allowed(self, patch_state):
        patch_state.config = {"security": {"api_key_required": False, "api_key": "secret"}}
        app = _make_app()
        with TestClient(app) as c:
            resp = c.post("/test-write", headers={"Authorization": "Bearer bad"})
        assert resp.status_code == 200


class TestAuthEnabled:
    """api_key_required = True 时检查 Bearer Token。"""

    def test_no_token_rejected(self, patch_state):
        patch_state.config = {"security": {"api_key_required": True, "api_key": "secret123"}}
        app = _make_app()
        with TestClient(app) as c:
            resp = c.post("/test-write")
        assert resp.status_code == 401

    def test_bad_token_rejected(self, patch_state):
        patch_state.config = {"security": {"api_key_required": True, "api_key": "secret123"}}
        app = _make_app()
        with TestClient(app) as c:
            resp = c.post("/test-write", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_correct_token_allowed(self, patch_state):
        patch_state.config = {"security": {"api_key_required": True, "api_key": "secret123"}}
        app = _make_app()
        with TestClient(app) as c:
            resp = c.post("/test-write", headers={"Authorization": "Bearer secret123"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_enabled_no_key_configured_rejected(self, patch_state):
        patch_state.config = {"security": {"api_key_required": True, "api_key": ""}}
        app = _make_app()
        with TestClient(app) as c:
            resp = c.post("/test-write")
        assert resp.status_code == 401
        assert "未配置" in resp.text


class TestAuthEdgeCases:
    """边界情况测试。"""

    def test_empty_token(self, patch_state):
        patch_state.config = {"security": {"api_key_required": True, "api_key": "secret123"}}
        app = _make_app()
        with TestClient(app) as c:
            resp = c.post("/test-write", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    def test_malformed_header(self, patch_state):
        patch_state.config = {"security": {"api_key_required": True, "api_key": "secret123"}}
        app = _make_app()
        with TestClient(app) as c:
            resp = c.post("/test-write", headers={"Authorization": "NotBearer foo"})
        assert resp.status_code == 401
