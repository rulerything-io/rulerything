"""
自适应规则进化系统 — FastAPI 服务入口
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import load_config
from rule import Rule
from storage import RuleStorage
from index import EverythingStyleIndex
from logger import RuleLogger
from evolution import EvolutionEngine
from entropy_engine import EntropyEngine  # Phase 1
from immune_system import RuleImmuneSystem  # Phase 2
from adaptive_system import AdaptiveRuleSystem  # Phase 3

# v3.0 模块（可选）
try:
    from storage_v2 import RuleStorageV2
    from dep_miner import DepMiner
    from auto_proposer import ProposalSystem
    from gap_detector import GapDetector
    from ai_bridge import AIBridge, AIValidationResult
    from auto_ingest import AutoIngest
    from auto_evolver import AutoEvolver
    from alert import AlertManager
    from health import StartupCheck
    HAS_V3 = True
except ImportError:
    RuleStorageV2 = None
    DepMiner = None
    ProposalSystem = None
    GapDetector = None
    AIBridge = None
    AutoIngest = None
    AutoEvolver = None
    AlertManager = None
    StartupCheck = None
    HAS_V3 = False

# ── 配置加载 ──────────────────────────────────────────

config = load_config()
log_level = config["logging"]["level"]

# ── 核心组件初始化 ────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parent
_DATA_DIR = str(_BASE_DIR / "data")

# v3.0 SQLite 存储（初始化在 index 创建后完成）
storage_v2 = None
dep_miner = None

# ── AI 运行时配置管理（模块级，不受 v3 开关影响）──────


def _save_ai_config(ai_config: dict):
    """保存 AI 配置到运行时存储（热加载用）。"""
    if not storage_v2:
        return
    try:
        storage_v2.set_config("ai_bridge_config", json.dumps(ai_config, ensure_ascii=False))
    except Exception:
        pass


def _load_ai_config() -> dict:
    """从运行时存储加载 AI 配置覆盖。"""
    if not storage_v2:
        return {}
    try:
        raw = storage_v2.get_config("ai_bridge_config", "{}")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _reinitialize_ai_modules():
    """热加载 AI 模块（不重启服务）。"""
    global ai_bridge, auto_ingest
    try:
        runtime = _load_ai_config()
        if runtime.get("api_key"):
            api_key_env = runtime.get("api_key_env", "ANTHROPIC_API_KEY")
            os.environ[api_key_env] = runtime["api_key"]
        base_cfg = dict(config.get("v3", {}).get("ai_bridge", {}))
        merged = {**base_cfg, **runtime}
        if AIBridge:
            ai_bridge = AIBridge(storage_v2, merged, index=index, gap_detector=gap_detector)
            auto_ingest = AutoIngest(storage_v2, ai_bridge, merged)
            logger.info("v3", "AI 模块已热加载 (provider=%s, model=%s)",
                        merged.get("provider"), merged.get("model"))
    except Exception as e:
        logger.info("v3", f"AI 模块热加载失败: {e}")


storage = RuleStorage(_DATA_DIR)
logger = RuleLogger(str(_BASE_DIR / "logs"), level=log_level)
index = EverythingStyleIndex()
index.HOT_THRESHOLD = config["index"]["hot_threshold"]
index.COLD_DAYS = config["index"]["cold_days"]

# 启动时重建索引
rules = storage.list()
if config["index"]["rebuild_on_start"]:
    index.build(rules)
    logger.info("index", f"索引重建完成，共 {len(rules)} 条规则",
                rule_count=len(rules), index_version=index.index_version)

# 启动时预热
if config["cache"]["preheat_on_start"] and rules:
    result = index.warmup()
    logger.info("cache", f"预热完成，加载 {result['loaded']} 条",
                **result)

# v3.0 存储层初始化（索引就绪后）
if config.get("v3", {}).get("enabled", False) and config["v3"].get("storage") == "sqlite" and RuleStorageV2:
    storage_v2 = RuleStorageV2(_DATA_DIR)
    # 索引同步回调
    def _sync_index(action, data):
        try:
            if action == "add" and hasattr(data, "id"):
                index.add(data)
        except Exception:
            pass
    storage_v2.set_index_callback(_sync_index)
    # 将 storage 指向 v2 实现（接口兼容，所有现有代码自动受益）
    storage = storage_v2
    # 从 SQLite 重建索引（覆盖前面 JSONL 的索引）
    if config["index"]["rebuild_on_start"]:
        sqlite_rules = storage.list()
        index.build(sqlite_rules)
        logger.info("v3", f"索引已从 SQLite 重建，共 {len(sqlite_rules)} 条规则")
    # 启动 dep_miner
    if DepMiner:
        dep_miner = DepMiner(storage_v2, config.get("v3", {}).get("dep_miner", {}))
    else:
        dep_miner = None
        logger.warning("DepMiner not available")
    logger.info("v3", f"v3.0 存储已启用 (SQLite, {len(storage_v2.list())} 条规则)")

    # Phase B: 自动提案系统 + 缺口检测
    if ProposalSystem:
        proposal_system = ProposalSystem(
            storage_v2, dep_miner, index,
            config.get("v3", {}).get("proposal_system", {}),
        )
    else:
        proposal_system = None
        logger.warning("ProposalSystem not available")
    if GapDetector:
        gap_detector = GapDetector(
            storage_v2,
            config.get("v3", {}).get("gap_detector", {}),
        )
    else:
        gap_detector = None
        logger.warning("GapDetector not available")
    logger.info("v3", "Phase B 模块已初始化 (proposal_system + gap_detector)")

    # Phase C: 自动演化引擎
    auto_evolver = None
    evolver_cfg = config.get("v3", {}).get("auto_evolver", {})
    if evolver_cfg.get("enabled", False) and AutoEvolver:
        try:
            auto_evolver = AutoEvolver(storage_v2, index, logger, evolver_cfg)
            logger.info("v3", "AutoEvolver 已初始化")
        except Exception as e:
            logger.info("v3", f"AutoEvolver 初始化失败: {e}")

    # Phase C: 告警管理器
    alert_manager = None
    if AlertManager:
        try:
            alert_manager = AlertManager(config.get("v3", {}))
            logger.info("v3", "AlertManager 已初始化")
        except Exception as e:
            logger.info("v3", f"AlertManager 初始化失败: {e}")

    # Phase C: AI 桥接 + 自动学习
    ai_bridge = None
    auto_ingest = None
    if config.get("v3", {}).get("ai_bridge", {}).get("enabled", False) and AIBridge:
        try:
            # 加载运行时配置覆盖（UI 设置的 API key 等）
            _runtime_ai_config = _load_ai_config()
            if _runtime_ai_config.get("api_key"):
                api_key_env = _runtime_ai_config.get("api_key_env", "ANTHROPIC_API_KEY")
                os.environ[api_key_env] = _runtime_ai_config["api_key"]
            ai_config = {**config["v3"]["ai_bridge"], **_runtime_ai_config}
            ai_bridge = AIBridge(storage_v2, ai_config, index=index, gap_detector=gap_detector)
            auto_ingest = AutoIngest(storage_v2, ai_bridge, ai_config)
            logger.info("v3", "Phase C 模块已初始化 (ai_bridge + auto_ingest)")

            # 缓存预热：记录热查询统计（不调 LLM，避免启动时爆发费用）
            try:
                recent = storage_v2.get_recent_queries(days=7)
                if recent:
                    hit_count = 0
                    for entry in recent[:20]:
                        q_text = entry.get("query", "")
                        if q_text and ai_bridge.cache.lookup(q_text):
                            hit_count += 1
                    logger.info("v3", f"AI 缓存已就绪: 共 {len(ai_bridge.cache.cache)} 条, "
                                f"{hit_count}/{min(20, len(recent))} 热门查询已缓存")
            except Exception:
                pass
        except Exception as e:
            logger.info("v3", f"Phase C 初始化失败: {e}")

# Phase B/C 全局引用（即使 v3 未启用也保持 None）
proposal_system = locals().get("proposal_system", None)
gap_detector = locals().get("gap_detector", None)
ai_bridge = locals().get("ai_bridge", None)
auto_ingest = locals().get("auto_ingest", None)
auto_evolver = locals().get("auto_evolver", None)
alert_manager = locals().get("alert_manager", None)

# 进化引擎
evolution = EvolutionEngine(storage, index, logger)

# Phase 1: 熵引擎
entropy_engine = EntropyEngine(config.get("entropy", {}))

# Phase 2: 规则免疫系统（默认关闭）
immune_system = None
if config.get("immune", {}).get("enabled", False):
    immune_system = RuleImmuneSystem(config.get("immune", {}))

# Phase 3: AdaptiveRuleSystem（默认关闭）
adaptive_system = None
if config.get("adaptive_system", {}).get("enabled", False):
    adaptive_system = AdaptiveRuleSystem(config, str(_BASE_DIR / "data"), rules)
    logger.info("system", "AdaptiveRuleSystem 初始化完成", phase="3")

_start_time = datetime.now()

# ── Phase B: 后台管理循环 ──────────────────────────────

_management_heartbeat = None
_management_loop_active = False


def _get_metrics() -> dict:
    """收集系统指标用于提案系统。"""
    idx_stats = index.stats()
    return {
        "cache_hit_rate": idx_stats.get("cache_hit_rate", 0),
        "avg_latency_ms": idx_stats.get("avg_latency_ms", 0),
        "total_rules": idx_stats.get("total_rules_indexed", 0),
        "hot_cache_size": idx_stats.get("hot_cache_size", 0),
        "cold_count": idx_stats.get("cold_count", 0),
        "health_score": 1.0,  # placeholder, 由实际扫描更新
    }

# 给 auto_evolver 注入 metrics 读取函数（用于执行后指标重读）
if auto_evolver:
    auto_evolver.metrics_fn = _get_metrics


def _management_loop():
    """后台管理循环：每 30 秒检查一次，执行自动提案等维护任务。"""
    global _management_heartbeat, _management_loop_active
    _management_loop_active = True
    tick_count = 0

    while _management_loop_active:
        try:
            _management_heartbeat = datetime.now().isoformat()
            tick_count += 1

            # 每 30 秒：提案扫描（各提案类型内部有冷却/频率控制）
            if proposal_system:
                try:
                    metrics = _get_metrics()
                    results = proposal_system.scan_and_propose(metrics)
                    if results:
                        for r in results:
                            logger.info("management",
                                        f"提案执行: {r['type']} → {r['status']}",
                                        **r)
                except Exception as e:
                    logger.info("management", f"提案扫描异常: {e}")

            # 每 30 秒：自动演化 tick
            if auto_evolver:
                try:
                    metrics = _get_metrics()
                    evolver_results = auto_evolver.tick(metrics)
                    if evolver_results:
                        for r in evolver_results:
                            logger.info("management",
                                        f"演化执行: {r['strategy']} → {r['status']}",
                                        **r)
                except Exception as e:
                    logger.info("management", f"自动演化异常: {e}")

            # 每 120 秒（约 2 tick）：查询日志轮换
            if storage_v2 and tick_count % 2 == 0:
                try:
                    storage_v2.rotate_query_log()
                except Exception:
                    pass

            # 每 600 秒（约 10 tick）：快照清理
            if storage_v2 and tick_count % 10 == 0:
                try:
                    storage_v2.prune_snapshots(max_keep=50)
                except Exception:
                    pass

            # Phase C: 每 tick 检查 AI 提炼队列
            if auto_ingest:
                try:
                    auto_ingest.process_pending()
                except Exception:
                    pass

            # Phase C: 每 600 秒 AI 缓存清理
            if storage_v2 and ai_bridge and tick_count % 10 == 0:
                try:
                    storage_v2.ai_cache_cleanup(
                        max_entries=config.get("v3", {}).get("ai_bridge", {}).get("cache_max_entries", 5000)
                    )
                except Exception:
                    pass

            # Phase C: 每 3600 秒（60 tick）置信度衰减
            if auto_ingest and tick_count % 60 == 0:
                try:
                    decayed = auto_ingest.confidence_adjuster.decay_check()
                    if decayed:
                        logger.info("ai", f"置信度衰减: {len(decayed)} 条规则", rules=decayed)
                    promoted = auto_ingest.confidence_adjuster.promote_check()
                    if promoted:
                        logger.info("ai", f"置信度升级: {len(promoted)} 条规则", rules=promoted)
                except Exception:
                    pass

            # 每 600 秒（10 tick）：系统健康检查 + 告警
            if alert_manager and tick_count % 10 == 0:
                try:
                    evolver_healthy = auto_evolver.healthy if auto_evolver else True
                    if not evolver_healthy:
                        alert_manager.send("auto_evolver", "warning",
                                           f"自动演化引擎健康异常: {auto_evolver.last_error if auto_evolver else 'unknown'}")
                except Exception:
                    pass

        except Exception:
            pass

        # 等待 60 秒
        for _ in range(60):
            if not _management_loop_active:
                break
            time.sleep(1)


# 启动自检
if config.get("v3", {}).get("enabled", False) and StartupCheck:
    try:
        health_check = StartupCheck(storage_v2, index, dict(config), _DATA_DIR)
        startup_report = health_check.run_all()
        if not startup_report.get("can_start", True):
            logger.info("v3", f"启动自检失败: {json.dumps(startup_report, ensure_ascii=False)}")
            if alert_manager:
                alert_manager.send("system", "critical",
                                   f"启动自检失败: {startup_report['summary']['failed']} 项失败")
        else:
            logger.info("v3", f"启动自检通过 ({startup_report['summary']['passed']}/{startup_report['summary']['total']})")
    except Exception as e:
        logger.info("v3", f"启动自检异常: {e}")

# 启动管理循环（daemon 线程，随主进程退出）
if config.get("v3", {}).get("enabled", False):
    mgmt_thread = threading.Thread(target=_management_loop, daemon=True)
    mgmt_thread.start()
    logger.info("v3", "Phase B 管理循环已启动 (60s tick)")


# ── FastAPI 应用 ──────────────────────────────────────

app = FastAPI(
    title="自适应规则进化系统",
    version="1.0.1",
    description="作为大语言模型确定性副脑的知识规则系统",
)

# 挂载静态文件（管理面板）
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")


@app.get("/")
async def root():
    """重定向到管理面板。"""
    return RedirectResponse(url="/static/dashboard.html")


class SearchRequest(BaseModel):
    query: str
    search_type: str = "exact"
    category: str = "all"
    user_feedback: Optional[bool] = None


class SearchResult(BaseModel):
    title: str
    content: str
    id: str
    confidence: float
    category: str
    tags: list


class SearchResponse(BaseModel):
    results: list[SearchResult]
    confidence: float
    rule_id: str
    latency_ms: float
    ai_delegated: bool = False
    ai_query_id: str = ""


# ── 路由 ──────────────────────────────────────────────


@app.get("/health")
async def health():
    """基础健康检查。"""
    idx_stats = index.stats()
    return {
        "status": "ok",
        "version": "1.0.1",
        "uptime_seconds": int((datetime.now() - _start_time).total_seconds()),
        **idx_stats,
    }


@app.get("/ready")
async def ready():
    """就绪检查（索引已加载）。"""
    is_ready = index.is_ready
    return {
        "status": "ready" if is_ready else "not_ready",
        "index_loaded": is_ready,
        "cache_warmed": len(index.hot_cache) > 0,
        "total_rules": len(index._rules) if is_ready else 0,
    }


@app.post("/restart")
async def restart_server():
    """重启服务器（启动新进程后退出当前进程）。"""
    import subprocess
    import threading
    import sys

    def _restart():
        time.sleep(1.5)  # 等响应返回后再重启
        # 直接启动 uvicorn，避免 start.bat 的端口清理逻辑误杀当前进程
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app",
             "--host", "127.0.0.1", "--port", "8001",
             "--log-level", "warning"],
            cwd=_BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        os._exit(0)

    threading.Thread(target=_restart, daemon=True).start()
    logger.info("system", "服务器正在重启...")
    return {"status": "restarting", "message": "服务器正在重启..."}


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    """搜索规则。"""
    start = time.perf_counter()

    results = index.search(
        query=req.query,
        search_type=req.search_type,
        category=None if req.category == "all" else req.category,
        limit=10,
    )

    latency_ms = (time.perf_counter() - start) * 1000

    # 记录延迟到索引统计
    index.record_latency(latency_ms)

    # v3.0 查询日志
    if storage_v2:
        try:
            storage_v2.log_query(req.query, latency_ms, len(results), latency_ms < 1.0)
        except Exception:
            pass

    # 结构化日志
    logger.query(
        query=req.query,
        search_type=req.search_type,
        latency_ms=latency_ms,
        result_count=len(results),
        result_ids=[r.id for r in results],
        cache_hit=latency_ms < 1.0,
        user_feedback=req.user_feedback,
    )

    # 熵引擎记录查询（Phase 1）
    entropy_engine.record_query(
        query=req.query,
        result_ids=[r.id for r in results],
        latency_ms=latency_ms,
        cache_hit=latency_ms < 1.0,
    )

    # 收集反馈（如果提供）
    if req.user_feedback is not None and results:
        context = f"用户对搜索结果{'满意' if req.user_feedback else '不满意'}"
        evolution.collect_feedback(
            results[0].id, req.user_feedback, context
        )

    # AI 兜底：索引无结果时自动委托 AI Bridge（含搜索上下文）
    ai_delegated = False
    ai_query_id = ""
    if not results and ai_bridge and ai_bridge.is_enabled():
        try:
            # 构建搜索上下文
            search_context = {
                "fallback": True,
                "search_type": req.search_type,
                "categories": [req.category] if req.category != "all" else [],
                "results": [],  # 无结果
            }
            ai_result = ai_bridge.enhance_query(req.query, search_context=search_context)
            if ai_result.get("source") == "delegated":
                ai_delegated = True
                ai_query_id = ai_result.get("query_id", "")
        except Exception:
            pass

    return SearchResponse(
        results=[
            SearchResult(
                title=r.title, content=r.content, id=r.id,
                confidence=r.confidence, category=r.category, tags=r.tags,
            )
            for r in results[:5]
        ],
        confidence=results[0].confidence if results else 0.0,
        rule_id=results[0].id if results else "",
        latency_ms=round(latency_ms, 2),
        ai_delegated=ai_delegated,
        ai_query_id=ai_query_id,
    )


@app.post("/warmup")
async def warmup(category: Optional[str] = Query(None)):
    """预热缓存。"""
    result = index.warmup(category=category)
    logger.info("cache", f"预热完成", **result)
    return {"status": "ok", **result}


@app.post("/dedup/dry-run")
async def dedup_dry_run():
    """预览去重结果。"""
    return {"duplicates": storage.dedup_dry_run()}


@app.post("/dedup/apply")
async def dedup_apply():
    """执行去重。"""
    results = storage.dedup_apply()
    # 去重后重建索引
    index.build(storage.list())
    logger.info("dedup", f"去重完成: {len(results)} 条规则被标记",
                count=len(results))
    return {"applied": len(results), "details": results}


@app.post("/evolve")
async def trigger_evolution(dry_run: bool = False):
    """触发进化。

    Args:
        dry_run: True=仅预览，False=执行
    """
    changes = evolution.apply_pending_evolutions(dry_run=dry_run)
    return {
        "applied": len(changes),
        "dry_run": dry_run,
        "changes": changes,
    }


class AddRuleRequest(BaseModel):
    id: str
    title: str
    content: str
    category: str = "general"
    tags: list = []
    confidence: float = 0.5
    verifier: str = "manual"


@app.post("/add-rule")
async def add_rule(req: AddRuleRequest):
    """添加一条新规则。"""
    rule = Rule(
        id=req.id, title=req.title, content=req.content,
        category=req.category, tags=req.tags,
        confidence=req.confidence, verifier=req.verifier,
    )
    ok, msg = storage.add(rule)
    if ok:
        index.build(storage.list())
        logger.info("api", f"添加规则 {rule.id}", rule_id=rule.id)
        return {"ok": True, "msg": "ok"}
    else:
        return JSONResponse(status_code=409, content={"ok": False, "msg": msg})


class RollbackRequest(BaseModel):
    rule_id: str
    target_version: int


@app.post("/rollback")
async def rollback(req: RollbackRequest):
    """回滚规则到指定版本。"""
    ok = evolution.rollback(req.rule_id, req.target_version)
    return {
        "success": ok,
        "rule_id": req.rule_id,
        "target_version": req.target_version,
    }


@app.get("/evolution/stats")
async def evolution_stats():
    """进化引擎统计。"""
    return evolution.stats()


@app.get("/evolution/pending")
async def evolution_pending():
    """查看待处理进化。"""
    return {
        "pending_count": evolution.pending_count,
        "pending": evolution.pending_evolutions,
    }


@app.get("/evolution/versions/{rule_id}")
async def evolution_versions(rule_id: str):
    """查看规则的归档版本。"""
    versions = evolution.list_archived_versions(rule_id)
    return {"rule_id": rule_id, "archived_versions": versions}


class RuleListItem(BaseModel):
    id: str
    title: str
    content: str
    category: str
    tags: list
    confidence: float
    version: int
    hit_count: int
    created_at: Optional[str] = None


@app.get("/logs")
async def get_logs(limit: int = Query(50, ge=1, le=500), level: Optional[str] = Query(None)):
    """获取最近的系统日志（JSON Lines）。"""
    log_file = _BASE_DIR / "logs" / "system.log"
    if not log_file.exists():
        return {"lines": []}

    with open(log_file, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    # 解析并过滤
    entries = []
    for line in all_lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            entry = {"message": line, "level": "INFO", "log_type": "system"}
        if level and entry.get("level", "").upper() != level.upper():
            continue
        entries.append(entry)

    return {"lines": entries[-limit:]}


@app.get("/rules")
async def list_rules(category: Optional[str] = Query(None)):
    """列出所有规则（不分页）。"""
    rules = storage.list(category=category)
    return [
        RuleListItem(
            id=r.id, title=r.title, content=r.content,
            category=r.category, tags=r.tags,
            confidence=r.confidence, version=r.version,
            hit_count=r.hit_count,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in sorted(rules, key=lambda x: x.id)
    ]


@app.get("/stats")
async def stats():
    """系统全面统计。"""
    result = {
        "storage": storage.stats(),
        "index": index.stats(),
        "uptime_seconds": int((datetime.now() - _start_time).total_seconds()),
    }
    if storage_v2:
        result["storage_v2"] = storage_v2.stats()
    if dep_miner:
        result["dep_miner"] = dep_miner.get_stats()
    if proposal_system:
        result["proposal_system"] = proposal_system.get_stats()
    if gap_detector:
        result["gap_detector"] = gap_detector.get_stats()
    if ai_bridge:
        result["ai_bridge"] = ai_bridge.get_stats()
    if auto_ingest:
        result["auto_ingest"] = auto_ingest.get_stats()
    if auto_evolver:
        result["auto_evolver"] = auto_evolver.get_stats()
    if alert_manager:
        result["alert_manager"] = alert_manager.health_check()
    if storage_v2:
        result["ai_storage"] = storage_v2.get_ai_stats()
    return result


# ── v3.0 依赖关系 API ───────────────────────────────


@app.get("/v3/status")
async def v3_status():
    """v3.0 全模块状态汇总。"""
    return {
        "v3_enabled": config.get("v3", {}).get("enabled", False),
        "storage": "sqlite" if storage_v2 else "jsonl",
        "dep_miner": {
            "enabled": dep_miner is not None,
            "stats": dep_miner.get_stats() if dep_miner else {},
        },
        "proposal_system": {
            "enabled": proposal_system is not None,
            "stats": proposal_system.get_stats() if proposal_system else {},
        },
        "gap_detector": {
            "enabled": gap_detector is not None,
            "stats": gap_detector.get_stats() if gap_detector else {},
        },
        "ai_bridge": {
            "enabled": ai_bridge.is_enabled() if ai_bridge else False,
            "budget": ai_bridge.get_budget_status() if ai_bridge else {},
            "stats": ai_bridge.get_stats() if ai_bridge else {},
        },
        "auto_ingest": {
            "enabled": auto_ingest is not None,
            "stats": auto_ingest.get_stats() if auto_ingest else {},
        },
        "auto_evolver": {
            "enabled": auto_evolver is not None,
            "stats": auto_evolver.get_stats() if auto_evolver else {},
        },
        "alert_manager": {
            "enabled": alert_manager is not None,
            "health": alert_manager.health_check() if alert_manager else {},
        },
        "management_loop": {
            "running": _management_loop_active,
            "heartbeat": _management_heartbeat,
        },
        "storage_v2": storage_v2.stats() if storage_v2 else {"status": "disabled"},
        "index": {
            "rules": len(index._rules) if hasattr(index, '_rules') else 0,
            "hot_cache": len(index.hot_cache) if hasattr(index, 'hot_cache') else 0,
        },
        "uptime_seconds": int((datetime.now() - _start_time).total_seconds()),
    }


@app.get("/deps/graph")
async def deps_graph():
    """获取依赖图数据（D3.js 格式）。"""
    if not dep_miner:
        return {"nodes": [], "edges": []}
    return dep_miner.get_graph_data()


@app.get("/deps/chain/{rule_id:path}")
async def deps_chain(rule_id: str, max_depth: int = Query(3, ge=1, le=10)):
    """获取某规则的影响链。"""
    if not dep_miner:
        return {"error": "dep_miner 未启用"}
    return {"rule_id": rule_id, "chain": dep_miner.get_impact_chain(rule_id, max_depth)}


@app.get("/deps/conflicts")
async def deps_conflicts():
    """获取检测到的冲突规则对。"""
    if not dep_miner:
        return {"conflicts": []}
    return {"conflicts": dep_miner.get_relations(relation_type="conflicts")}


@app.post("/deps/refresh")
async def deps_refresh():
    """触发依赖关系重新挖掘。"""
    if not dep_miner:
        return {"error": "dep_miner 未启用"}
    try:
        dep_miner.clear_relations()
        dep_miner.mine_all()
        return {"status": "ok", "stats": dep_miner.get_stats()}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/deps/relations")
async def deps_relations(rule_id: Optional[str] = Query(None),
                         relation_type: Optional[str] = Query(None)):
    """获取规则关系列表。"""
    if not dep_miner:
        return {"relations": []}
    return {"relations": dep_miner.get_relations(rule_id, relation_type)}


# ── v3.0 提案系统 API ────────────────────────────────


@app.get("/proposals")
async def list_proposals(status: Optional[str] = Query(None),
                         module: Optional[str] = Query(None),
                         limit: int = Query(50, ge=1, le=500)):
    """列出提案。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    return {"proposals": storage_v2.list_proposals(status, module, limit)}


@app.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str):
    """获取提案详情。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    p = storage_v2.get_proposal(proposal_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "提案不存在"})
    return {"proposal": p}


@app.post("/proposals/{proposal_id}/cancel")
async def cancel_proposal(proposal_id: str):
    """取消待处理提案。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    p = storage_v2.get_proposal(proposal_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "提案不存在"})
    if p["status"] != "pending":
        return JSONResponse(status_code=400, content={"error": f"提案状态为 {p['status']}，无法取消"})
    ok = storage_v2.update_proposal_status(proposal_id, "cancelled")
    return {"status": "ok" if ok else "failed"}


# ── v3.0 知识缺口 API ────────────────────────────────


@app.get("/coverage/gaps")
async def coverage_gaps():
    """检测知识缺口。"""
    if not gap_detector:
        return {"gaps": [], "error": "gap_detector 未启用"}
    try:
        gaps = gap_detector.detect_gaps()
        return {"gaps": gaps, "count": len(gaps)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/coverage/stats")
async def coverage_stats():
    """覆盖度统计。"""
    if not gap_detector:
        return {"error": "gap_detector 未启用"}
    return gap_detector.get_coverage_stats()


# ── v3.0 审计日志 API ────────────────────────────────


@app.get("/audit/logs")
async def audit_logs(limit: int = Query(50, ge=1, le=500),
                     module: Optional[str] = Query(None)):
    """获取审计日志。"""
    if not storage_v2:
        return {"logs": []}
    return {"logs": storage_v2.get_recent_audit_logs(limit, module)}


# ── Phase C: AI Bridge API ────────────────────────────


@app.get("/ai/query")
async def ai_query(query: str = Query(..., min_length=1)):
    """AI 增强查询。"""
    if not ai_bridge or not ai_bridge.is_enabled():
        return {"source": "system", "content": "", "error": "AI Bridge 未启用"}
    try:
        result = ai_bridge.enhance_query(query)
        # 异步触发 auto_ingest（不阻塞响应）
        if auto_ingest and result.get("source") in ("ai", "cache"):
            validation = result.get("validation", {}) or {}
            try:
                v_result = validation.get("result", "unverifiable")
                auto_ingest.enqueue(query, result["content"], v_result)
            except Exception:
                pass  # 提炼失败不影响查询结果
        return result
    except Exception as e:
        return {"source": "system", "error": str(e)}


@app.get("/ai/budget")
async def ai_budget_status():
    """AI 预算状态。"""
    if not ai_bridge:
        return {"enabled": False}
    return ai_bridge.get_budget_status()


@app.get("/ai/stats")
async def ai_stats():
    """AI 模块统计。"""
    result = {}
    if ai_bridge:
        result["ai_bridge"] = ai_bridge.get_stats()
    if auto_ingest:
        result["auto_ingest"] = auto_ingest.get_stats()
    if storage_v2:
        result["storage"] = storage_v2.get_ai_stats()
    return result


@app.post("/ai/feedback")
async def ai_feedback(rule_id: str = Query(...), positive: bool = Query(...)):
    """提交用户对 AI 生成规则的反馈。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    storage_v2.record_ai_feedback(rule_id, positive)
    if auto_ingest:
        adj = auto_ingest.confidence_adjuster
        adj.record_feedback(rule_id, positive)
    return {"status": "ok"}


@app.get("/ingest/logs")
async def ingest_logs(limit: int = Query(50, ge=1, le=500),
                      status: Optional[str] = Query(None)):
    """获取规则提炼日志。"""
    if not storage_v2:
        return {"logs": []}
    return {"logs": storage_v2.get_ingestion_logs(limit, status)}


@app.post("/ai/clear-cache")
async def ai_clear_cache():
    """清除 AI 缓存。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    try:
        storage_v2.ai_cache_cleanup(max_entries=0)
        return {"status": "ok", "cleared": True}
    except Exception as e:
        return {"error": str(e)}


@app.get("/ai/config")
async def ai_get_config():
    """获取当前 AI 配置（API key 脱敏显示）。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    base = dict(config.get("v3", {}).get("ai_bridge", {}))
    runtime = _load_ai_config()
    merged = {**base, **runtime}
    # 脱敏 API key
    if merged.get("api_key"):
        merged["api_key_preview"] = merged["api_key"][:6] + "****"
        merged["api_key"] = ""
    return merged


class AIConfigRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None
    enabled: Optional[bool] = None
    use_parent_ai: Optional[bool] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    daily_limit_usd: Optional[float] = None
    per_call_limit_usd: Optional[float] = None
    confidence_threshold: Optional[float] = None
    cache_ttl_hours: Optional[float] = None
    cache_max_entries: Optional[int] = None
    max_new_rules_per_session: Optional[int] = None
    max_new_rules_per_day: Optional[int] = None


@app.post("/ai/config")
async def ai_set_config(req: AIConfigRequest):
    """更新 AI 配置并热加载（无需重启）。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    # 合并现有运行时配置
    current = _load_ai_config()
    updates = {k: v for k, v in req.dict(exclude_none=True).items()}
    merged = {**current, **updates}
    _save_ai_config(merged)
    # 热加载
    _reinitialize_ai_modules()
    return {"status": "ok", "message": "AI 配置已更新并生效"}


@app.post("/ingest/run")
async def ingest_run():
    """手动触发一次提炼扫描。"""
    if not auto_ingest:
        return {"error": "auto_ingest 未启用"}
    created = auto_ingest.process_pending()
    return {"created": len(created), "rule_ids": created}


@app.get("/ai/pending")
async def ai_pending(limit: int = Query(20, ge=1, le=100)):
    """获取待处理的 AI 委托查询（供父 AI 消费）。"""
    if not storage_v2:
        return {"queries": [], "error": "SQLite 存储未启用"}
    queries = storage_v2.get_pending_queries(status="pending", limit=limit)
    counts = storage_v2.get_pending_query_count()
    return {"queries": queries, "counts": counts}


class AIRespondRequest(BaseModel):
    query_id: str
    response: str
    error_message: Optional[str] = None


@app.post("/ai/respond")
async def ai_respond(req: AIRespondRequest):
    """提交对委托查询的回答（由父 AI 调用）。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    ok = storage_v2.answer_pending_query(
        req.query_id, req.response,
        responder="parent-ai",
        error_message=req.error_message,
    )
    if ok:
        # 如果 auto_ingest 启用且回答有效，触发异步提炼
        if auto_ingest and not req.error_message:
            try:
                auto_ingest.enqueue(
                    f"pending:{req.query_id}",
                    req.response,
                    "consistent",
                )
            except Exception:
                pass
        return {"status": "ok"}
    return JSONResponse(status_code=404, content={"error": "查询不存在或已回答"})


@app.get("/ai/query/status/{query_id}")
async def ai_query_status(query_id: str):
    """查询委托 AI 的处理状态（供 dashboard 轮询）。"""
    if not storage_v2:
        return {"status": "unknown", "error": "存储未启用"}
    queries = storage_v2.get_pending_queries(limit=100)
    for q in queries:
        if q.get("id") == query_id:
            return {"status": q.get("status", "pending"), "query_id": query_id}
    return {"status": "unknown", "query_id": query_id}


@app.post("/ai/conversation/start")
async def ai_conversation_start():
    """开始新的多轮对话。"""
    if not ai_bridge:
        return {"error": "AI Bridge 未启用"}
    cid = f"conv_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    return {"conversation_id": cid}


@app.post("/ai/conversation/query")
async def ai_conversation_query(req: SearchRequest, conversation_id: str = Query(...)):
    """多轮对话查询。"""
    if not ai_bridge or not ai_bridge.is_enabled():
        return {"error": "AI Bridge 未启用"}
    try:
        result = ai_bridge.enhance_query(req.query, conversation_id=conversation_id)
        return result
    except Exception as e:
        return {"error": str(e)}


@app.post("/ai/conversation/clear")
async def ai_conversation_clear(conversation_id: str = Query(...)):
    """清除对话历史。"""
    if ai_bridge:
        ai_bridge.clear_conversation(conversation_id)
    return {"status": "ok"}


# ── v3.0 冷规则管理 API ────────────────────────────


@app.get("/cold/list")
async def cold_list():
    """列出冷存储中的规则。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    return {"rules": [r.to_dict() for r in storage_v2.list_cold()]}


@app.post("/cold/archive")
async def cold_archive(days: int = Query(365, ge=30)):
    """将长时间未命中的规则归档到冷存储。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    archived = storage_v2.archive_cold_rules(days=days)
    return {"archived": len(archived), "rule_ids": archived}


@app.post("/cold/unfreeze/{rule_id:path}")
async def cold_unfreeze(rule_id: str):
    """从冷存储解冻规则。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    ok = storage_v2.unfreeze_rule(rule_id)
    if ok:
        # 重建索引
        index.build(storage.list())
        return {"status": "ok", "rule_id": rule_id}
    return JSONResponse(status_code=404, content={"error": f"规则 {rule_id} 不在冷存储中"})


# ── v3.0 运行时配置 API ─────────────────────────────


@app.get("/evolver/stats")
async def evolver_stats():
    """自动演化引擎统计。"""
    if not auto_evolver:
        return {"error": "auto_evolver 未启用"}
    return auto_evolver.get_stats()


@app.get("/evolver/strategy/{name}")
async def evolver_strategy(name: str):
    """获取某条策略的详情。"""
    if not auto_evolver:
        return {"error": "auto_evolver 未启用"}
    s = auto_evolver.get_strategy(name)
    if not s:
        return {"error": f"未知策略: {name}"}
    return {"strategy": s}


@app.post("/evolver/run/{name}")
async def evolver_run(name: str):
    """手动触发某条策略。"""
    if not auto_evolver:
        return {"error": "auto_evolver 未启用"}
    result = auto_evolver.run_strategy_now(name, _get_metrics())
    return {"result": result}


# ── 告警系统 API ──────────────────────────────────


@app.get("/alerts/health")
async def alerts_health():
    """告警系统健康状态。"""
    if not alert_manager:
        return {"enabled": False}
    return alert_manager.health_check()


@app.post("/alerts/test")
async def alerts_test():
    """发送测试告警。"""
    if not alert_manager:
        return {"error": "alert_manager 未启用"}
    ok = alert_manager.send("system", "info", "这是一条测试告警 (v3.0)")
    return {"sent": ok}


@app.get("/v3/config")
async def v3_config_get(key: Optional[str] = Query(None)):
    """获取运行时配置。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    if key:
        return {"key": key, "value": storage_v2.get_config(key)}
    return {"config": {"note": "使用 /v3/config?key=xxx 查询单个配置"}}


@app.post("/v3/config")
async def v3_config_set(key: str = Query(...), value: str = Query(...)):
    """设置运行时配置（热加载，无需重启）。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    storage_v2.set_config(key, value)
    return {"status": "ok", "key": key, "value": value}


# ── v3.0 快照管理 API ──────────────────────────────


@app.get("/v3/snapshots")
async def v3_snapshots():
    """列出系统快照。"""
    if not storage_v2:
        return {"snapshots": []}
    return {"snapshots": storage_v2.list_snapshots()}


@app.post("/v3/snapshot")
async def v3_create_snapshot():
    """创建系统快照。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    snapshot_id = storage_v2.create_snapshot()
    return {"status": "ok", "snapshot_id": snapshot_id}


@app.post("/v3/rollback/{snapshot_id}")
async def v3_rollback(snapshot_id: str):
    """回滚到指定快照。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    ok = storage_v2.restore_snapshot(snapshot_id)
    if ok:
        # 重建索引
        rules = storage.list()
        index.build(rules)
        return {"status": "ok", "snapshot_id": snapshot_id, "rules_count": len(rules)}
    return JSONResponse(status_code=404, content={"error": f"快照 {snapshot_id} 不存在"})


# ── v3.0 系统健康 API ──────────────────────────────


@app.get("/v3/health")
async def v3_health():
    """v3.0 系统健康检查。"""
    checks = {
        "sqlite": False,
        "index_consistent": False,
        "dep_miner": False,
    }

    # SQLite 健康
    if storage_v2:
        try:
            integrity = storage_v2.integrity_check()
            checks["sqlite"] = len(integrity) == 0
        except Exception:
            checks["sqlite"] = False

    # 索引一致性（粗略）
    if index.is_ready:
        checks["index_consistent"] = True

    # dep_miner 健康
    if dep_miner:
        checks["dep_miner"] = True

    all_ok = all(checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
        "uptime_seconds": int((datetime.now() - _start_time).total_seconds()),
    }


# ── Phase 2: 规则免疫系统 API ──────────────────────


class ImmuneScanRequest(BaseModel):
    auto_cleanup: bool = False


class ImmuneClearRequest(BaseModel):
    rule_ids: Optional[list] = None
    all_nk_targets: bool = False


@app.post("/immune/scan")
async def immune_scan(req: ImmuneScanRequest):
    """扫描所有规则的健康状态。"""
    if not immune_system:
        return {"status": "disabled", "message": "免疫系统未启用"}
    rules = storage.list()
    results = immune_system.batch_scan(rules, auto_cleanup=req.auto_cleanup)
    return {
        "healthy": len(results["healthy"]),
        "weakened": len(results["weakened"]),
        "infected": len(results["infected"]),
        "dead": len(results["dead"]),
        "nk_targets": list(immune_system.nk_targets),
        "summary": immune_system.get_health_summary(),
    }


@app.post("/immune/clear")
async def immune_clear(req: ImmuneClearRequest):
    """NK 清除低质量规则。"""
    if not immune_system:
        return {"status": "disabled", "message": "免疫系统未启用"}
    if req.all_nk_targets:
        cleared = immune_system.nk_clear()
    else:
        cleared = immune_system.nk_clear(req.rule_ids)
    # 从存储中物理删除
    for rid in cleared:
        storage.hard_delete(rid)
    # 重建索引
    index.build(storage.list())
    return {"cleared": cleared}


@app.get("/health/{rule_id}")
async def rule_health(rule_id: str):
    """查看单条规则的健康详情。"""
    if not immune_system:
        return {"status": "disabled", "message": "免疫系统未启用"}
    rule = storage.get(rule_id)
    if not rule:
        return {"error": f"规则 {rule_id} 不存在"}
    report = immune_system.evaluate_health(rule)
    return {
        "rule_id": report.rule_id,
        "status": report.status.value,
        "score": report.score,
        "dimensions": report.dimensions,
        "conflicts": report.conflicts,
        "antibodies": report.antibodies,
    }


# ── AI 集成辅助 ────────────────────────────────────


# ── Phase 1: 熵引擎 API ────────────────────────────


class AckRequest(BaseModel):
    type: str


@app.get("/entropy/report")
async def entropy_report():
    """获取系统熵报告。"""
    if not config.get("entropy", {}).get("enabled", True):
        return {"status": "disabled"}
    return entropy_engine.get_report()


@app.get("/entropy/suggestions")
async def entropy_suggestions():
    """获取优化建议。"""
    if not config.get("entropy", {}).get("enabled", True):
        return {"suggestions": []}

    # 收集当前指标（Phase 3 组件优先）
    metrics = {
        'cache_hit_rate': entropy_engine.get_report().get(
            'cache_hit_rate', index.stats().get('cache_hit_rate', 0)
        ),
        'avg_query_latency_ms': entropy_engine.get_report().get('avg_latency_ms', 0),
        'conflict_count': (
            len(adaptive_system.immune_system.regulatory_t_cells)
            if adaptive_system and adaptive_system.immune_system
            else 0
        ),
        'low_quality_ratio': (
            len(adaptive_system.immune_system.nk_targets) / max(1, len(adaptive_system.rules))
            if adaptive_system and adaptive_system.immune_system
            else 0
        ),
        'preheat_accuracy': 0,
    }
    suggestions = entropy_engine.suggest_optimizations(metrics)
    return {
        "suggestions": [
            {"type": s.type, "target": s.target,
             "description": s.description,
             "estimated_cost": s.estimated_cost,
             "predicted_improvement": s.predicted_improvement}
            for s in suggestions
        ],
        "current_entropy": entropy_engine.get_report().get('estimated_system_entropy', 0),
    }


@app.post("/entropy/ack")
async def entropy_ack(req: AckRequest):
    """标记优化建议已执行。"""
    from entropy_engine import OptimizationAction
    action = OptimizationAction(type=req.type, target="")
    entropy_engine.mark_executed(action)
    return {"status": "acknowledged", "type": req.type}


# ── Phase 3: AdaptiveRuleSystem API ──────────────────


class QueryRequest(BaseModel):
    query_text: str
    sort_by: str = "title"
    category: Optional[str] = None
    use_semantic: bool = False
    limit: int = 10


@app.post("/query")
async def phase3_query(req: QueryRequest):
    """Phase 3 统一查询（EnhancedEverythingIndex + 语义插件）。"""
    if not adaptive_system:
        return JSONResponse(
            status_code=400,
            content={"error": "AdaptiveRuleSystem 未启用"},
        )
    results = adaptive_system.query(
        query_text=req.query_text,
        sort_by=req.sort_by,
        category=req.category,
        use_semantic=req.use_semantic,
        limit=req.limit,
    )
    return {
        "results": [
            {
                "id": r.id, "title": r.title, "content": r.content,
                "category": r.category, "tags": r.tags,
                "confidence": r.confidence, "hit_count": r.hit_count,
            }
            for r in results
        ],
        "total": len(results),
    }


@app.get("/status")
async def phase3_status():
    """Phase 3 完整系统状态。"""
    if not adaptive_system:
        return JSONResponse(
            status_code=400,
            content={"error": "AdaptiveRuleSystem 未启用"},
        )
    return adaptive_system.get_full_status()


@app.get("/cache/stats")
async def cache_stats():
    """Phase 3 缓存统计。"""
    if not adaptive_system or not adaptive_system.cache:
        return JSONResponse(
            status_code=400,
            content={"error": "Phase 3 缓存未启用"},
        )
    cache = adaptive_system.cache
    return {
        "size": len(cache.cache),
        "max_size": cache.max_size,
        "heat_entries": len(cache.heat),
        "threshold": cache.preheat_threshold,
        "decay_half_life": cache.decay_half_life,
    }


@app.post("/index/incremental")
async def index_incremental(req: AddRuleRequest):
    """Phase 3 增量添加规则到增强索引（无需全量重建）。"""
    if not adaptive_system:
        return JSONResponse(
            status_code=400,
            content={"error": "AdaptiveRuleSystem 未启用"},
        )
    from rule import Rule
    rule = Rule(
        id=req.id, title=req.title, content=req.content,
        category=req.category, tags=req.tags,
        confidence=req.confidence, verifier=req.verifier,
    )
    adaptive_system.index.add(rule)
    adaptive_system.rules[rule.id] = rule
    return {"ok": True, "rule_id": rule.id}


@app.post("/optimize")
async def phase3_optimize():
    """Phase 3 熵驱动系统优化。"""
    if not adaptive_system:
        return JSONResponse(
            status_code=400,
            content={"error": "AdaptiveRuleSystem 未启用"},
        )
    result = adaptive_system.optimize()
    return result


def enhance_prompt(user_input: str, max_rules: int = 5) -> str:
    """将用户问题与相关规则结合，生成增强提示词（v2.0，回退 v1.0）。

    用法：
        enhanced = enhance_prompt("如何在Python中高效处理百万条数据的循环？")
        # 然后调用 LLM API
        # response = claude.chat(messages=[
        #     {"role": "system", "content": enhanced},
        #     {"role": "user", "content": user_input},
        # ])
    """
    if adaptive_system:
        # Phase 3: 语义+标题前缀混合查询
        # sort_by="title" → 前缀查询按文本过滤，无匹配时不返回无关结果
        results = adaptive_system.query(
            query_text=user_input,
            sort_by="title",
            use_semantic=True,
            limit=max_rules,
        )
    else:
        # v1.0 回退：多策略关键词搜索
        all_results = []
        for search_type in ("exact", "prefix", "tag"):
            results = index.search(user_input, search_type, limit=3)
            all_results.extend(results)

        import re
        keywords = re.findall(r'[a-zA-Z_+#.]+', user_input)
        for kw in keywords[:5]:
            kw_lower = kw.lower()
            tag_results = index.search_by_tag(kw_lower, limit=3)
            all_results.extend(tag_results)
            prefix_results = index.search_prefix(kw_lower, limit=3)
            all_results.extend(prefix_results)

        seen = set()
        unique = []
        for r in all_results:
            if r.id not in seen:
                seen.add(r.id)
                unique.append(r)
        unique.sort(key=lambda r: r.confidence, reverse=True)
        results = unique[:max_rules]

    if not results:
        return user_input

    rules_text = "\n\n".join(
        f"规则 {i + 1}: [{r.id}] {r.title}\n"
        f"分类: {r.category} | 置信度: {r.confidence:.2f}\n"
        f"内容: {r.content}"
        for i, r in enumerate(results[:max_rules])
    )

    return f"""你是一个具备专业知识的技术助手。以下是与当前问题相关的最佳实践规则，请优先参考这些规则来回答：

## 相关规则库
{rules_text}

## 用户问题
{user_input}

## 回答要求
- 优先基于以上规则给出建议
- 如果引用了某条规则，请明确标注规则 ID（如 [规则 python/001]）
- 如果规则不完全适用，解释理由并提供补充建议
- 如果规则不相关，忽略规则并正常回答
- 保持回答自然、有用"""
