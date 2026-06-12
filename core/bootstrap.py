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
import threading
from datetime import datetime
from pathlib import Path

from config import load_config
from rule import Rule
from storage import RuleStorage
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


def bootstrap():
    """初始化所有组件并注入 state。"""

    # ── 配置 ────────────────────────────────────────────
    state.config = load_config()
    state.log_level = state.config["logging"]["level"]
    state._BASE_DIR = str(Path(__file__).resolve().parent.parent)
    state._DATA_DIR = str(Path(state._BASE_DIR) / "data")
    state.HAS_V3 = HAS_V3

    # ── 核心组件 ────────────────────────────────────────
    state.storage = RuleStorage(state._DATA_DIR)
    state.logger = RuleLogger(
        str(Path(state._BASE_DIR) / "logs"),
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

    # 启动时预热
    if state.config["cache"]["preheat_on_start"] and rules:
        result = state.index.warmup()
        state.logger.info("cache", f"预热完成，加载 {result['loaded']} 条", **result)

    # ── v3.0 存储层（索引就绪后）─────────────────────────
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

    # ── 启动管理循环 ────────────────────────────────────
    if state.config.get("v3", {}).get("enabled", False):
        from core.background import management_loop
        mgmt_thread = threading.Thread(target=management_loop, daemon=True)
        mgmt_thread.start()
        state.logger.info("v3", "Phase B 管理循环已启动 (60s tick)")

    return state


def _init_v3_modules():
    """初始化 v3.0 存储层及上层模块。"""
    v3_cfg = state.config.get("v3", {})
    if not v3_cfg.get("enabled", False) or v3_cfg.get("storage") != "sqlite":
        state.storage_v2 = None
        state.dep_miner = None
        state.proposal_system = None
        state.gap_detector = None
        state.ai_bridge = None
        state.auto_ingest = None
        state.auto_evolver = None
        state.alert_manager = None
        return

    if not RuleStorageV2:
        state.logger.warning("v3", "RuleStorageV2 不可用，跳过 v3 初始化")
        return

    state.storage_v2 = RuleStorageV2(state._DATA_DIR)

    # 索引同步回调
    def _sync_index(action, data):
        try:
            if action == "add" and hasattr(data, "id"):
                state.index.add(data)
        except Exception:
            pass

    state.storage_v2.set_index_callback(_sync_index)
    state.storage = state.storage_v2

    # 从 SQLite 重建索引
    if state.config["index"]["rebuild_on_start"]:
        sqlite_rules = state.storage.list()
        state.index.build(sqlite_rules)
        state.logger.info("v3", f"索引已从 SQLite 重建，共 {len(sqlite_rules)} 条规则")

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
                pass
        except Exception as e:
            state.logger.info("v3", f"Phase C 初始化失败: {e}")
