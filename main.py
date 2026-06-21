# Copyright 2026 rulerything-io
"""Rulerything FastAPI application factory.

Importing this module constructs routes only. Runtime resources are initialized
inside FastAPI's lifespan, so tests, workers and packaging tools can import it
without opening databases or starting threads.
"""

import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import load_config
from core.bootstrap import abort_bootstrap, bootstrap, shutdown
from core.state import state
from core.version import VERSION
from routes import register_routes

_app_lifecycle_lock = threading.RLock()


def _static_dir(base_dir: Path) -> Optional[Path]:
    local = base_dir / "static"
    if local.is_dir():
        return local
    import sys
    installed = Path(sys.prefix) / "share" / "rulerything" / "static"
    return installed if installed.is_dir() else None


def create_app(config: dict = None, *, base_dir: str = None,
               data_dir: str = None, start_background: bool = True) -> FastAPI:
    """Create an application with explicit runtime dependencies."""
    resolved_config = load_config(cli_overrides=config) if config is not None else load_config()
    resolved_base = Path(base_dir or Path(__file__).resolve().parent).resolve()
    runtime_owner = object()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        with _app_lifecycle_lock:
            # The compatibility prompt helper may lazily own the runtime.
            if state.initialized and state.runtime_owner == "prompt-helper":
                shutdown("prompt-helper")
            if state.initialized:
                raise RuntimeError(
                    "Only one Rulerything application may run per process"
                )
            try:
                bootstrap(
                    config=application.state.rulerything_config,
                    base_dir=str(application.state.rulerything_base_dir),
                    data_dir=application.state.rulerything_data_dir,
                    start_background=application.state.rulerything_start_background,
                    owner=runtime_owner,
                )
            except Exception:
                abort_bootstrap(runtime_owner)
                raise
        state.app = application
        try:
            yield
        finally:
            with _app_lifecycle_lock:
                shutdown(runtime_owner)

    application = FastAPI(
        title="Rulerything",
        version=VERSION,
        description="作为大语言模型确定性副脑的知识规则系统",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
        redoc_url="/redoc",
    )
    application.state.rulerything_config = resolved_config
    application.state.rulerything_base_dir = resolved_base
    application.state.rulerything_data_dir = data_dir
    application.state.rulerything_start_background = start_background

    origins = resolved_config.get("server", {}).get("cors_origins", [])
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials="*" not in origins,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    limit = int(resolved_config.get("server", {}).get("rate_limit_per_min", 1000))
    windows = defaultdict(deque)
    rate_lock = threading.Lock()
    request_count = 0

    @application.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        nonlocal request_count
        if request.url.path.startswith("/static/") or limit <= 0:
            return await call_next(request)
        now = time.monotonic()
        client = request.client.host if request.client else "unknown"
        with rate_lock:
            request_count += 1
            cutoff = now - 60
            if request_count % 256 == 0 or len(windows) > 10_000:
                for key, old_window in list(windows.items()):
                    if not old_window or old_window[-1] <= cutoff:
                        windows.pop(key, None)
            window = windows[client]
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= limit:
                return JSONResponse(
                    status_code=429,
                    content={"error": {"code": "rate_limit_exceeded",
                                       "message": "请求过于频繁，请稍后再试"}},
                    headers={"Retry-After": "60"},
                )
            window.append(now)
        return await call_next(request)

    static_dir = _static_dir(resolved_base)
    if static_dir:
        application.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    register_routes(application)
    return application


app = create_app()

# Backward-compatible prompt helper. Calling it may lazily initialize the core;
# importing this module does not.
from core.utils import enhance_prompt  # noqa: E402,F401


def run():
    """Console entry point for ``rulerything-server``."""
    import uvicorn
    server = load_config().get("server", {})
    uvicorn.run("main:app", host=server.get("host", "127.0.0.1"),
                port=int(server.get("port", 8001)),
                workers=int(server.get("workers", 1)))


if __name__ == "__main__":
    run()
