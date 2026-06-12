"""
Rulerything — 系统管理路由
"""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Body, Depends
from fastapi.responses import RedirectResponse, JSONResponse

from core.state import state
from core.auth import require_write_token

router = APIRouter()


@router.get("/")
async def root():
    """重定向到管理面板。"""
    return RedirectResponse(url="/static/dashboard.html")


@router.get("/health")
async def health():
    """基础健康检查。"""
    idx_stats = state.index.stats()
    return {
        "status": "ok",
        "version": "1.1.1",
        "uptime_seconds": int((datetime.now() - state._start_time).total_seconds()),
        **idx_stats,
    }


@router.get("/ready")
async def ready():
    """就绪检查。"""
    is_ready = state.index.is_ready
    return {
        "status": "ready" if is_ready else "not_ready",
        "index_loaded": is_ready,
        "cache_warmed": len(state.index.hot_cache) > 0,
        "total_rules": len(state.index._rules) if is_ready else 0,
    }


@router.post("/restart")
async def restart_server(auth=Depends(require_write_token)):
    """重启服务器。"""
    def _restart():
        time.sleep(1.5)
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app",
             "--host", "127.0.0.1", "--port", "8001",
             "--log-level", "warning"],
            cwd=state._BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        os._exit(0)

    threading.Thread(target=_restart, daemon=True).start()
    state.logger.info("system", "服务器正在重启...")
    return {"status": "restarting", "message": "服务器正在重启..."}


@router.get("/logs")
async def get_logs(limit: int = Query(50, ge=1, le=500),
                   level: Optional[str] = Query(None),
                   log_type: Optional[str] = Query(None, alias="type")):
    """获取最近的系统日志（JSON Lines）。"""
    log_file = Path(state._BASE_DIR) / "logs" / "system.log"
    if not log_file.exists():
        return {"lines": []}

    # Tail 策略：只读末尾 ~limit 行避免 OOM
    entries = []
    with open(log_file, "r", encoding="utf-8") as f:
        # 快速跳到文件尾部读取
        chunk_size = 8192
        f.seek(0, 2)  # 跳到文件末尾
        file_size = f.tell()
        position = max(0, file_size - chunk_size * 4)  # 最多读 32KB
        f.seek(position)
        # 跳过第一行可能的不完整行
        if position > 0:
            f.readline()

        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                entry = {"message": line, "level": "INFO", "log_type": "system"}
            if level and entry.get("level", "").upper() != level.upper():
                continue
            if log_type and entry.get("log_type", "system") != log_type:
                continue
            entries.append(entry)

    return {"lines": entries[-limit:]}


@router.get("/stats")
async def stats():
    """系统全面统计。"""
    result = {
        "storage": state.storage.stats(),
        "index": state.index.stats(),
        "uptime_seconds": int((datetime.now() - state._start_time).total_seconds()),
    }
    if state.storage_v2:
        result["storage_v2"] = state.storage_v2.stats()
    if state.dep_miner:
        result["dep_miner"] = state.dep_miner.get_stats()
    if state.proposal_system:
        result["proposal_system"] = state.proposal_system.get_stats()
    if state.gap_detector:
        result["gap_detector"] = state.gap_detector.get_stats()
    if state.ai_bridge:
        result["ai_bridge"] = state.ai_bridge.get_stats()
    if state.auto_ingest:
        result["auto_ingest"] = state.auto_ingest.get_stats()
    if state.auto_evolver:
        result["auto_evolver"] = state.auto_evolver.get_stats()
    if state.alert_manager:
        result["alert_manager"] = state.alert_manager.health_check()
    if state.storage_v2:
        result["ai_storage"] = state.storage_v2.get_ai_stats()
    return result


@router.get("/v3/status")
async def v3_status():
    """v3.0 全模块状态汇总。"""
    return {
        "v3_enabled": state.config.get("v3", {}).get("enabled", False),
        "storage": "sqlite" if state.storage_v2 else "jsonl",
        "dep_miner": {
            "enabled": state.dep_miner is not None,
            "stats": state.dep_miner.get_stats() if state.dep_miner else {},
        },
        "proposal_system": {
            "enabled": state.proposal_system is not None,
            "stats": state.proposal_system.get_stats() if state.proposal_system else {},
        },
        "gap_detector": {
            "enabled": state.gap_detector is not None,
            "stats": state.gap_detector.get_stats() if state.gap_detector else {},
        },
        "ai_bridge": {
            "enabled": state.ai_bridge.is_enabled() if state.ai_bridge else False,
            "budget": state.ai_bridge.get_budget_status() if state.ai_bridge else {},
            "stats": state.ai_bridge.get_stats() if state.ai_bridge else {},
        },
        "auto_ingest": {
            "enabled": state.auto_ingest is not None,
            "stats": state.auto_ingest.get_stats() if state.auto_ingest else {},
        },
        "auto_evolver": {
            "enabled": state.auto_evolver is not None,
            "stats": state.auto_evolver.get_stats() if state.auto_evolver else {},
        },
        "alert_manager": {
            "enabled": state.alert_manager is not None,
            "health": state.alert_manager.health_check() if state.alert_manager else {},
        },
        "management_loop": {
            "running": state._management_loop_active,
            "heartbeat": state._management_heartbeat,
            "tick_interval_sec": state.config.get("v3", {}).get("management_tick_sec", 60),
        },
        "storage_v2": state.storage_v2.stats() if state.storage_v2 else {"status": "disabled"},
        "index": {
            "rules": len(state.index._rules) if hasattr(state.index, '_rules') else 0,
            "hot_cache": len(state.index.hot_cache) if hasattr(state.index, 'hot_cache') else 0,
        },
        "uptime_seconds": int((datetime.now() - state._start_time).total_seconds()),
    }


@router.post("/shutdown")
async def shutdown(auth=Depends(require_write_token)):
    """停止管理循环（不影响 API 服务）。"""
    if state._stop_event:
        state._stop_event.set()
        state.logger.info("system", "管理循环停止信号已发送")
        return {"status": "stopping", "message": "管理循环停止中..."}
    return {"status": "not_running", "message": "管理循环未运行"}


@router.get("/v3/health")
async def v3_health():
    """v3.0 系统健康检查。"""
    checks = {
        "sqlite": False,
        "index_consistent": False,
        "dep_miner": False,
    }

    if state.storage_v2:
        try:
            integrity = state.storage_v2.integrity_check()
            checks["sqlite"] = len(integrity) == 0
        except Exception:
            checks["sqlite"] = False

    if state.index.is_ready:
        checks["index_consistent"] = True

    if state.dep_miner:
        checks["dep_miner"] = True

    all_ok = all(checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
        "uptime_seconds": int((datetime.now() - state._start_time).total_seconds()),
    }


@router.get("/v3/config")
async def v3_config_get(key: Optional[str] = Query(None)):
    """获取运行时配置。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    if key:
        return {"key": key, "value": state.storage_v2.get_config(key)}
    return {"config": {"note": "使用 /v3/config?key=xxx 查询单个配置"}}


@router.post("/v3/config")
async def v3_config_set(key: str = Body(...), value: str = Body(...), auth=Depends(require_write_token)):
    """设置运行时配置。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    state.storage_v2.set_config(key, value)
    return {"status": "ok", "key": key, "value": value}


@router.get("/v3/snapshots")
async def v3_snapshots():
    """列出系统快照。"""
    if not state.storage_v2:
        return {"snapshots": []}
    return {"snapshots": state.storage_v2.list_snapshots()}


@router.post("/v3/snapshot")
async def v3_create_snapshot(auth=Depends(require_write_token)):
    """创建系统快照。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    snapshot_id = state.storage_v2.create_snapshot()
    return {"status": "ok", "snapshot_id": snapshot_id}


@router.post("/v3/rollback/{snapshot_id}")
async def v3_rollback(snapshot_id: str, auth=Depends(require_write_token)):
    """回滚到指定快照。"""
    if not state.storage_v2:
        return {"error": "SQLite 存储未启用"}
    ok = state.storage_v2.restore_snapshot(snapshot_id)
    if ok:
        rules = state.storage.list()
        state.index.build(rules)
        return {"status": "ok", "snapshot_id": snapshot_id, "rules_count": len(rules)}
    return JSONResponse(status_code=404, content={"error": f"快照 {snapshot_id} 不存在"})


@router.get("/audit/logs")
async def audit_logs(limit: int = Query(50, ge=1, le=500),
                     module: Optional[str] = Query(None)):
    """获取审计日志。"""
    if not state.storage_v2:
        return {"logs": []}
    return {"logs": state.storage_v2.get_recent_audit_logs(limit, module)}
