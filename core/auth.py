"""
Rulerything — Bearer Token 认证依赖 + 安全配置检查

用法:
    from core.auth import require_write_token
    @router.post("/endpoint")
    async def endpoint(auth=Depends(require_write_token)):
        ...

配置:
    security.api_key_required: bool   # 是否启用认证
    security.api_key: str             # Bearer Token 值
    security.allow_insecure_public_bind: bool  # 允许公网绑定无认证
"""

import sys
import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from core.state import state

_bearer_scheme = HTTPBearer(auto_error=False)

logger = logging.getLogger("rulerything.security")


# ── 公网绑定安全校验 ──────────────────────────────────────

PUBLIC_ADDRESSES = {"0.0.0.0", "::"}


def validate_bind_config(config: dict) -> None:
    """Validate that public binding requires API authentication.

    Raises ConfigError (prints message and exits) if an insecure
    public binding is detected without explicit opt-in.

    This must be called during server startup, before the HTTP listener
    binds to any address.
    """
    host = config.get("server", {}).get("host", "127.0.0.1")
    api_key_required = config.get("security", {}).get("api_key_required", False)
    allow = config.get("security", {}).get("allow_insecure_public_bind", False)

    is_public = host in PUBLIC_ADDRESSES or host.startswith("0.") or host.startswith("::")

    if is_public and not api_key_required and not allow:
        print(
            "\n"
            "╔══════════════════════════════════════════════════════════════╗\n"
            "║            SECURITY ERROR: INSECURE PUBLIC BIND            ║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            f"║  Host: {host:<45}║\n"
            "║  Binding to a public address without API authentication    ║\n"
            "║  would expose write endpoints to the internet.             ║\n"
            "║                                                              ║\n"
            "║  Fix: Set security.api_key_required=true in config.yaml    ║\n"
            "║    or set RULES_SECURITY_API_KEY_REQUIRED=true env var.    ║\n"
            "║                                                              ║\n"
            "║  To explicitly allow: set allow_insecure_public_bind=true  ║\n"
            "╚══════════════════════════════════════════════════════════════╝\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if is_public and not api_key_required and allow:
        print(
            "\n"
            "╔══════════════════════════════════════════════════════════════╗\n"
            "║       WARNING: PUBLIC BIND WITH AUTHENTICATION DISABLED    ║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            f"║  Host: {host:<45}║\n"
            "║  The server is binding to a public address without API     ║\n"
            "║  authentication. All write endpoints are accessible to     ║\n"
            "║  anyone who can reach this host.                           ║\n"
            "║                                                              ║\n"
            "║  This is NOT recommended for production use.               ║\n"
            "╚══════════════════════════════════════════════════════════════╝\n",
            file=sys.stderr,
        )
        logger.warning("Public bind with auth disabled (allow_insecure_public_bind=true)")


def _is_auth_enabled() -> bool:
    """检查认证是否启用。"""
    return state.config.get("security", {}).get("api_key_required", False)


def _get_api_key() -> str:
    """获取配置中的 API key。"""
    return state.config.get("security", {}).get("api_key", "")


async def require_write_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> bool:
    """要求有效的 Bearer Token（仅 api_key_required=True 时生效）。

    当认证关闭时，所有请求放行。
    当认证开启时，缺少或无效的 token 返回 401。
    """
    if not _is_auth_enabled():
        return True

    expected = _get_api_key()
    if not expected:
        # api_key_required=True 但未配置 api_key，拒绝所有写操作
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API 认证已启用但未配置 API Key",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization: Bearer <token> 请求头",
        )

    if credentials.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key 无效",
        )

    return True
