"""
Rulerything — Bearer Token 认证依赖

用法:
    from core.auth import require_write_token
    @router.post("/endpoint")
    async def endpoint(auth=Depends(require_write_token)):
        ...

配置:
    security.api_key_required: bool   # 是否启用认证
    security.api_key: str             # Bearer Token 值
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from core.state import state

_bearer_scheme = HTTPBearer(auto_error=False)


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
