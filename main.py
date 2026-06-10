"""
自适应规则进化系统 — FastAPI 服务入口
"""

import json
import os
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

# ── 配置加载 ──────────────────────────────────────────

config = load_config()
log_level = config["logging"]["level"]

# ── 核心组件初始化 ────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parent
storage = RuleStorage(str(_BASE_DIR / "data"))
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
    return {
        "storage": storage.stats(),
        "index": index.stats(),
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
