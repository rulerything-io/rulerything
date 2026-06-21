"""
Rulerything — 组件初始化（bootstrap）

负责：
1. 加载配置
2. 创建核心组件（storage / index / logger）
3. 按需初始化 v3.0 模块（dep_miner / proposal_system / gap_detector / ai_bridge / …）
4. 启动管理循环 daemon 线程
"""

import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from config import load_config
from rule import Rule
from index import EverythingStyleIndex
from logger import RuleLogger
from evolution import EvolutionEngine
from entropy_engine import EntropyEngine
from immune_system import RuleImmuneSystem
from adaptive_system import AdaptiveRuleSystem

# v3.0 可选模块
try:
    from storage_v2 import RuleStorageV2
    from dep_miner import DepMiner
    from auto_proposer import ProposalSystem
    from gap_detector import GapDetector
    from ai_bridge import AIBridge
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

from core.state import state
from core.repository import create_repository


def bootstrap(config: dict = None, base_dir: str = None, data_dir: str = None,
              start_background: bool = True, owner=None):
    """初始化所有组件并注入 state。"""

    runtime_owner = owner if owner is not None else "manual"
    if state.initialized:
        if state.runtime_owner == runtime_owner:
            return state
        raise RuntimeError("Rulerything runtime is already owned by another application")
    state.runtime_owner = runtime_owner

    # ── 配置 ────────────────────────────────────────────
    state.config = config or load_config()
    state.log_level = state.config["logging"]["level"]
    source_base = Path(base_dir or Path(__file__).resolve().parent.parent).resolve()
    state._BASE_DIR = str(source_base)
    configured_data = data_dir or os.environ.get("RULERYTHING_DATA_DIR")
    if configured_data:
        runtime_data = Path(configured_data).resolve()
    elif (source_base / "data").is_dir():
        runtime_data = source_base / "data"
    else:
        runtime_data = Path.home() / ".rulerything" / "data"
    runtime_data.mkdir(parents=True, exist_ok=True)
    state._DATA_DIR = str(runtime_data)
    state.HAS_V3 = HAS_V3

    # ── 核心组件 ────────────────────────────────────────
    packaged_seed = Path(sys.prefix) / "share" / "rulerything" / "data"
    source_seed = source_base / "data"
    seed_dir = source_seed if source_seed.is_dir() else packaged_seed
    state.storage, state.storage_v2 = create_repository(
        state.config, state._DATA_DIR, str(seed_dir)
    )
    log_dir = Path(os.environ.get("RULERYTHING_LOG_DIR", runtime_data.parent / "logs"))
    state.logger = RuleLogger(
        str(log_dir),
        level=state.log_level,
    )

    state.index = EverythingStyleIndex()
    state.index.HOT_THRESHOLD = state.config["index"]["hot_threshold"]
    state.index.COLD_DAYS = state.config["index"]["cold_days"]

    # 启动时重建索引
    rules = state.storage.list()
    if state.config["index"]["rebuild_on_start"]:
        state.index.build(rules)
        state.logger.info("index",
                          f"索引重建完成，共 {len(rules)} 条规则",
                          rule_count=len(rules),
                          index_version=state.index.index_version)

    _attach_index_sync()
    if hasattr(state.storage, "record_hits"):
        state.index.set_hit_callback(state.storage.record_hits)

    # 启动时预热
    if state.config["cache"]["preheat_on_start"] and rules:
        result = state.index.warmup()
        state.logger.info("cache", f"预热完成，加载 {result['loaded']} 条", **result)

    # ── v3.0 扩展模块（核心存储和索引就绪后）─────────────
    _init_v3_modules()

    # ── 基础引擎 ────────────────────────────────────────
    state.evolution = EvolutionEngine(state.storage, state.index, state.logger)
    state.entropy_engine = EntropyEngine(state.config.get("entropy", {}))

    # Phase 2: 免疫系统（默认关闭）
    state.immune_system = None
    if state.config.get("immune", {}).get("enabled", False):
        state.immune_system = RuleImmuneSystem(state.config.get("immune", {}))

    # Phase 3: AdaptiveRuleSystem（默认关闭）
    state.adaptive_system = None
    if state.config.get("adaptive_system", {}).get("enabled", False):
        state.adaptive_system = AdaptiveRuleSystem(
            state.config,
            str(Path(state._BASE_DIR) / "data"),
            rules,
        )
        state.logger.info("system", "AdaptiveRuleSystem 初始化完成", phase="3")

    state._start_time = datetime.now()

    # 给 auto_evolver 注入 metrics 读取函数
    if state.auto_evolver:
        from core.background import get_metrics
        state.auto_evolver.metrics_fn = get_metrics

    # ── 启动自检 ────────────────────────────────────────
    if state.config.get("v3", {}).get("enabled", False) and StartupCheck:
        try:
            health_check = StartupCheck(
                state.storage_v2, state.index, dict(state.config), state._DATA_DIR,
            )
            startup_report = health_check.run_all()
            if not startup_report.get("can_start", True):
                state.logger.info("v3",
                                  f"启动自检失败: {json.dumps(startup_report, ensure_ascii=False)}")
                if state.alert_manager:
                    state.alert_manager.send("system", "critical",
                                             f"启动自检失败: {startup_report['summary']['failed']} 项失败")
            else:
                state.logger.info("v3",
                                  f"启动自检通过 ({startup_report['summary']['passed']}/"
                                  f"{startup_report['summary']['total']})")
        except Exception as e:
            state.logger.info("v3", f"启动自检异常: {e}")

    # ── v4.0 价值层初始化 ─────────────────────────────────
    _init_value_layer(start_timers=start_background)

    # ── 启动管理循环 ────────────────────────────────────
    if start_background and state.config.get("v3", {}).get("enabled", False):
        state._stop_event = threading.Event()
        from core.background import management_loop
        mgmt_thread = threading.Thread(target=management_loop, daemon=True)
        mgmt_thread.start()
        state._management_thread = mgmt_thread
        state.logger.info("v3", "Phase B 管理循环已启动 (60s tick)")

    state.initialized = True
    return state


def _cleanup_resources():
    """Best-effort cleanup for complete or partially initialized runtimes."""
    if state._stop_event:
        state._stop_event.set()
    if state._management_thread and state._management_thread.is_alive():
        state._management_thread.join(timeout=2)
    if state.value_engine and getattr(state.value_engine, "decay_timer", None):
        try:
            state.value_engine.decay_timer.stop()
        except Exception:
            pass
    if state.logger:
        try:
            state.logger.info("system", "服务正在关闭")
        except Exception:
            pass
    if state.storage_v2:
        try:
            state.storage_v2.close()
        except Exception:
            pass
    if state.logger:
        try:
            state.logger.shutdown()
        except Exception:
            pass


def abort_bootstrap(owner=None):
    """Clean a failed startup without requiring initialized=True."""
    if owner is not None and state.runtime_owner not in (None, owner):
        return False
    _cleanup_resources()
    state.reset()
    return True


def shutdown(owner=None):
    """Stop background work and close resources. Safe to call repeatedly."""
    if not state.initialized:
        return False
    if owner is not None and state.runtime_owner != owner:
        return False
    _cleanup_resources()
    state.reset()
    return True


def _attach_index_sync():
    """Keep the in-memory index consistent with the selected repository."""
    if not state.storage_v2:
        return

    def sync_index(action, data):
        try:
            if action == "add" and hasattr(data, "id"):
                state.index.add(data)
            elif action == "update":
                rule = state.storage.get(data)
                if rule:
                    state.index.remove(data)
                    state.index.add(rule)
            elif action == "delete":
                state.index.remove(data)
        except Exception:
            state.logger.warn("index", "索引同步失败")

    state.storage_v2.set_index_callback(sync_index)


def _init_value_layer(start_timers: bool = True):
    """初始化 v4.0 价值层（懒加载，enabled=false 时跳过）。"""
    value_cfg = state.config.get("value", {})
    if not value_cfg.get("enabled", False):
        state.logger.info("v4", "价值层未启用 (value.enabled=false)")
        state.value_engine = None
        state.mode_engine = None
        state.shadow_engine = None
        return

    # 初始化模式引擎
    from value.mode_engine import ModeEngine
    state.mode_engine = ModeEngine(value_cfg, state.storage_v2)

    # 初始化价值引擎（懒加载，首次调用时触发子模块导入）
    from value import get_value_engine
    state.value_engine = get_value_engine(state.config, state.storage_v2)

    if state.value_engine:
        # 启动衰减定时器
        if start_timers:
            state.value_engine.decay_timer.start()
        state.logger.info("v4",
                          f"价值引擎已初始化: {len(state.value_engine.profiles)} 个画像, "
                          f"mode={value_cfg.get('mode', 'off')}")

    # 影子/双写模式初始化
    if state.mode_engine and state.mode_engine.mode.value in ("shadow", "dual_write"):
        from value.shadow import ShadowEngine
        state.shadow_engine = ShadowEngine(state.value_engine, state.storage_v2)
        state.logger.info("v4", f"影子引擎已初始化 (mode={state.mode_engine.mode.value})")


def _init_v3_modules():
    """初始化依赖 SQLite 扩展能力的上层模块。"""
    v3_cfg = state.config.get("v3", {})
    if not v3_cfg.get("enabled", False) or state.storage_v2 is None:
        state.dep_miner = None
        state.proposal_system = None
        state.gap_detector = None
        state.ai_bridge = None
        state.auto_ingest = None
        state.auto_evolver = None
        state.alert_manager = None
        return

    # dep_miner
    if DepMiner:
        state.dep_miner = DepMiner(state.storage_v2, v3_cfg.get("dep_miner", {}))
    else:
        state.dep_miner = None
        state.logger.warning("DepMiner not available")

    state.logger.info("v3", f"v3.0 存储已启用 (SQLite, {len(state.storage_v2.list())} 条规则)")

    # Phase B: 自动提案系统 + 缺口检测
    if ProposalSystem:
        state.proposal_system = ProposalSystem(
            state.storage_v2, state.dep_miner, state.index,
            v3_cfg.get("proposal_system", {}),
        )
    else:
        state.proposal_system = None
        state.logger.warning("ProposalSystem not available")

    if GapDetector:
        state.gap_detector = GapDetector(
            state.storage_v2,
            v3_cfg.get("gap_detector", {}),
        )
    else:
        state.gap_detector = None
        state.logger.warning("GapDetector not available")

    state.logger.info("v3", "Phase B 模块已初始化 (proposal_system + gap_detector)")

    # Phase C: 自动演化引擎
    state.auto_evolver = None
    evolver_cfg = v3_cfg.get("auto_evolver", {})
    if evolver_cfg.get("enabled", False) and AutoEvolver:
        try:
            state.auto_evolver = AutoEvolver(state.storage_v2, state.index,
                                             state.logger, evolver_cfg)
            state.logger.info("v3", "AutoEvolver 已初始化")
        except Exception as e:
            state.logger.info("v3", f"AutoEvolver 初始化失败: {e}")

    # Phase C: 告警管理器
    state.alert_manager = None
    if AlertManager:
        try:
            state.alert_manager = AlertManager(state.config.get("v3", {}))
            state.logger.info("v3", "AlertManager 已初始化")
        except Exception as e:
            state.logger.info("v3", f"AlertManager 初始化失败: {e}")

    # Phase C: AI 桥接 + 自动学习
    state.ai_bridge = None
    state.auto_ingest = None
    ai_cfg = v3_cfg.get("ai_bridge", {})
    if ai_cfg.get("enabled", False) and AIBridge:
        try:
            runtime = state.load_ai_config()
            if runtime.get("api_key"):
                api_key_env = runtime.get("api_key_env", "ANTHROPIC_API_KEY")
                os.environ[api_key_env] = runtime["api_key"]
            merged = {**ai_cfg, **runtime}
            state.ai_bridge = AIBridge(state.storage_v2, merged,
                                       index=state.index, gap_detector=state.gap_detector)
            state.auto_ingest = AutoIngest(state.storage_v2, state.ai_bridge, merged)
            state.logger.info("v3", "Phase C 模块已初始化 (ai_bridge + auto_ingest)")

            # 缓存预热
            try:
                recent = state.storage_v2.get_recent_queries(days=7)
                if recent:
                    hit_count = 0
                    for entry in recent[:20]:
                        q_text = entry.get("query", "")
                        if q_text and state.ai_bridge.cache.lookup(q_text):
                            hit_count += 1
                    state.logger.info("v3",
                                      f"AI 缓存已就绪: 共 {len(state.ai_bridge.cache.cache)} 条, "
                                      f"{hit_count}/{min(20, len(recent))} 热门查询已缓存")
            except Exception:
                state.logger.warn("v3", "AI 缓存预热失败")
        except Exception as e:
            state.logger.info("v3", f"Phase C 初始化失败: {e}")
