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

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.state import state
from core.bootstrap import bootstrap
from routes import register_routes

# ── 初始化所有组件 ────────────────────────────────────
state = bootstrap()

# ── FastAPI 应用 ──────────────────────────────────────
app = FastAPI(
    title="Rulerything",
    version="1.1.0",
    description="作为大语言模型确定性副脑的知识规则系统",
    docs_url="/docs",
    openapi_url="/openapi.json",
    redoc_url="/redoc",
)
state.app = app

# 挂载静态文件
_BASE_DIR = state._BASE_DIR
app.mount("/static", StaticFiles(directory=str(Path(_BASE_DIR) / "static")), name="static")

# 注册路由
register_routes(app)

# ── 向后兼容导出 ──────────────────────────────────────
from core.utils import enhance_prompt  # noqa: E402, F401
