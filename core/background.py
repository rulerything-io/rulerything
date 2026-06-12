"""
Rulerything — 后台管理循环

每 60 秒 tick 一次，执行：
- 提案扫描
- 自动演化 tick
- 查询日志轮换
- 快照清理
- AI 提炼队列处理
- AI 缓存清理
- 置信度衰减 / 升级
- 系统健康检查 + 告警
"""

import time
from datetime import datetime

from core.state import state


def get_metrics() -> dict:
    """收集系统指标。"""
    idx_stats = state.index.stats()
    return {
        "cache_hit_rate": idx_stats.get("cache_hit_rate", 0),
        "avg_latency_ms": idx_stats.get("avg_latency_ms", 0),
        "total_rules": idx_stats.get("total_rules_indexed", 0),
        "hot_cache_size": idx_stats.get("hot_cache_size", 0),
        "cold_count": idx_stats.get("cold_count", 0),
        "health_score": 1.0,
    }


def management_loop():
    """后台管理循环。"""
    state._management_loop_active = True
    tick_count = 0

    while state._management_loop_active:
        try:
            state._management_heartbeat = datetime.now().isoformat()
            tick_count += 1

            # 每 30 秒：提案扫描
            if state.proposal_system:
                try:
                    metrics = get_metrics()
                    results = state.proposal_system.scan_and_propose(metrics)
                    if results:
                        for r in results:
                            state.logger.info("management",
                                              f"提案执行: {r['type']} → {r['status']}", **r)
                except Exception as e:
                    state.logger.warn("management", f"提案扫描异常: {e}")
                    if state.alert_manager:
                        state.alert_manager.send("proposal_system", "warning", str(e))

            # 每 30 秒：自动演化 tick
            if state.auto_evolver:
                try:
                    metrics = get_metrics()
                    evolver_results = state.auto_evolver.tick(metrics)
                    if evolver_results:
                        for r in evolver_results:
                            state.logger.info("management",
                                              f"演化执行: {r['strategy']} → {r['status']}", **r)
                except Exception as e:
                    state.logger.warn("management", f"自动演化异常: {e}")
                    if state.alert_manager:
                        state.alert_manager.send("auto_evolver", "warning", str(e))

            # 每 120 秒：查询日志轮换
            if state.storage_v2 and tick_count % 2 == 0:
                try:
                    state.storage_v2.rotate_query_log()
                except Exception as e:
                    state.logger.warn("storage", f"日志轮换异常: {e}")

            # 每 600 秒：快照清理
            if state.storage_v2 and tick_count % 10 == 0:
                try:
                    state.storage_v2.prune_snapshots(max_keep=50)
                except Exception as e:
                    state.logger.warn("storage", f"快照清理异常: {e}")

            # 每 tick：AI 提炼队列
            if state.auto_ingest:
                try:
                    state.auto_ingest.process_pending()
                except Exception as e:
                    state.logger.warn("ai", f"AI 提炼队列异常: {e}")

            # 每 600 秒：AI 缓存清理
            if state.storage_v2 and state.ai_bridge and tick_count % 10 == 0:
                try:
                    state.storage_v2.ai_cache_cleanup(
                        max_entries=state.config.get("v3", {}).get("ai_bridge", {})
                        .get("cache_max_entries", 5000)
                    )
                except Exception as e:
                    state.logger.warn("ai", f"AI 缓存清理异常: {e}")

            # 每 3600 秒：置信度衰减
            if state.auto_ingest and tick_count % 60 == 0:
                try:
                    decayed = state.auto_ingest.confidence_adjuster.decay_check()
                    if decayed:
                        state.logger.info("ai", f"置信度衰减: {len(decayed)} 条规则", rules=decayed)
                    promoted = state.auto_ingest.confidence_adjuster.promote_check()
                    if promoted:
                        state.logger.info("ai", f"置信度升级: {len(promoted)} 条规则", rules=promoted)
                except Exception as e:
                    state.logger.warn("ai", f"置信度检查异常: {e}")

            # 每 600 秒：健康检查 + 告警
            if state.alert_manager and tick_count % 10 == 0:
                try:
                    evolver_healthy = state.auto_evolver.healthy if state.auto_evolver else True
                    if not evolver_healthy:
                        state.alert_manager.send("auto_evolver", "warning",
                                                  f"自动演化引擎健康异常: "
                                                  f"{state.auto_evolver.last_error if state.auto_evolver else 'unknown'}")
                except Exception as e:
                    state.logger.warn("management", f"健康检查异常: {e}")

        except Exception as e:
            state.logger.error("management", "loop_crash", f"管理循环未捕获异常: {e}")
            if state.alert_manager:
                state.alert_manager.send("management_loop", "error", str(e))

        # 等待 60 秒（可中断）
        for _ in range(60):
            if not state._management_loop_active:
                break
            time.sleep(1)
