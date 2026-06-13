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
Rulerything — FastAPI 服务入口

用法::

    uvicorn main:app --host 127.0.0.1 --port 8001
"""

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from core.state import state
from core.bootstrap import bootstrap
from core.version import VERSION
from routes import register_routes

# ── 初始化所有组件 ────────────────────────────────────
state = bootstrap()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理（优雅关闭）。"""
    # 启动阶段：bootstrap 已在模块级完成
    yield
    # 关闭阶段
    state.logger.info("system", "服务正在关闭...")

    # 1. 停止管理循环
    if state._stop_event:
        state._stop_event.set()
        state.logger.info("system", "管理循环已停止")

    # 2. 关闭 SQLite 连接
    if state.storage_v2:
        try:
            state.storage_v2.close()
        except Exception as e:
            state.logger.warn("system", f"SQLite 关闭异常: {e}")

    # 3. 关闭日志（flush + 关闭 handlers）
    if state.logger:
        try:
            state.logger.shutdown()
        except Exception as e:
            print(f"日志关闭异常: {e}")

    state.logger.info("system", "服务已关闭")


# ── FastAPI 应用 ──────────────────────────────────────
app = FastAPI(
    title="Rulerything",
    version=VERSION,
    description="作为大语言模型确定性副脑的知识规则系统",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
    redoc_url="/redoc",
)
state.app = app

# ── CORS 中间件 ─────────────────────────────────────────
cors_origins = state.config.get("server", {}).get("cors_origins", ["*"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 限流中间件 ──────────────────────────────────────────
_rate_limit_per_min = state.config.get("server", {}).get("rate_limit_per_min", 1000)
_rate_window: list = []


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path.startswith("/static/"):
        return await call_next(request)

    global _rate_window
    now = time.time()
    cutoff = now - 60
    _rate_window = [t for t in _rate_window if t > cutoff]

    if len(_rate_window) >= _rate_limit_per_min:
        return JSONResponse(
            status_code=429,
            content={"error": "请求过于频繁，请稍后再试", "retry_after_seconds": 60},
        )

    _rate_window.append(now)
    return await call_next(request)


# 挂载静态文件
_BASE_DIR = state._BASE_DIR
app.mount("/static", StaticFiles(directory=str(Path(_BASE_DIR) / "static")), name="static")

# 注册路由
register_routes(app)

# ── 向后兼容导出 ──────────────────────────────────────
from core.utils import enhance_prompt  # noqa: E402, F401
