"""Rulerything — 路由注册。"""

from fastapi import FastAPI

from routes.search import router as search_router
from routes.system import router as system_router
from routes.rules import router as rules_router
from routes.v3 import router as v3_router
from routes.ai import router as ai_router
from routes.phase3 import router as phase3_router


def register_routes(app: FastAPI):
    """将所有 APIRouter 注册到 FastAPI 应用。"""
    app.include_router(search_router)
    app.include_router(system_router)
    app.include_router(rules_router)
    app.include_router(v3_router)
    app.include_router(ai_router)
    app.include_router(phase3_router)
