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


# ── 公网绑定安全校验测试 ──────────────────────────────────

class TestValidateBindConfig:
    """测试 validate_bind_config 函数。"""

    def test_localhost_without_auth_allowed(self):
        """127.0.0.1 无认证应该通过。"""
        from core.auth import validate_bind_config
        config = {
            "server": {"host": "127.0.0.1"},
            "security": {"api_key_required": False, "allow_insecure_public_bind": False},
        }
        # 不应该抛出 SystemExit
        validate_bind_config(config)

    def test_localhost_with_auth_allowed(self):
        """localhost 开启认证也应该通过。"""
        from core.auth import validate_bind_config
        config = {
            "server": {"host": "localhost"},
            "security": {"api_key_required": True, "allow_insecure_public_bind": False},
        }
        validate_bind_config(config)

    def test_public_bind_with_auth_allowed(self):
        """0.0.0.0 开启认证应该通过。"""
        from core.auth import validate_bind_config
        config = {
            "server": {"host": "0.0.0.0"},
            "security": {"api_key_required": True, "allow_insecure_public_bind": False},
        }
        validate_bind_config(config)

    def test_public_bind_without_auth_rejected(self):
        """0.0.0.0 无认证应该被拒绝。"""
        from core.auth import validate_bind_config
        config = {
            "server": {"host": "0.0.0.0"},
            "security": {"api_key_required": False, "allow_insecure_public_bind": False},
        }
        with pytest.raises(SystemExit):
            validate_bind_config(config)

    def test_public_bind_without_auth_but_explicitly_allowed(self):
        """0.0.0.0 无认证但显式允许应该只警告不退出。"""
        from core.auth import validate_bind_config
        config = {
            "server": {"host": "0.0.0.0"},
            "security": {"api_key_required": False, "allow_insecure_public_bind": True},
        }
        # 不应该抛出 SystemExit
        validate_bind_config(config)

    def test_ipv6_public_bind_without_auth_rejected(self):
        """IPv6 全局地址 :: 无认证应该被拒绝。"""
        from core.auth import validate_bind_config
        config = {
            "server": {"host": "::"},
            "security": {"api_key_required": False, "allow_insecure_public_bind": False},
        }
        with pytest.raises(SystemExit):
            validate_bind_config(config)

    def test_subnet_public_bind_without_auth_rejected(self):
        """0.x 开头地址无认证应该被拒绝。"""
        from core.auth import validate_bind_config
        config = {
            "server": {"host": "0.0.0.1"},
            "security": {"api_key_required": False, "allow_insecure_public_bind": False},
        }
        with pytest.raises(SystemExit):
            validate_bind_config(config)
