"""
Rulerything — 共享状态容器

所有模块级全局变量统一托管在此，各模块通过 ``from core.state import state`` 访问。
"""

import json
import os
import threading
from datetime import datetime
from typing import Optional


class AppState:
    """集中持有所有运行时依赖。每个属性初始为 None，由 bootstrap 模块注入。"""

    # ── 配置 ────────────────────────────────────────────
    config: dict = None  # 全局配置
    log_level: str = None
    _BASE_DIR: str = None
    _DATA_DIR: str = None

    # ── 存储层 ──────────────────────────────────────────
    storage = None          # RuleStorage（v1 JSONL 或 v2 SQLite 兼容接口）
    storage_v2 = None        # RuleStorageV2（SQLite 特有功能）
    index = None             # EverythingStyleIndex
    logger = None            # RuleLogger

    # ── v3.0 模块 ───────────────────────────────────────
    dep_miner = None
    proposal_system = None
    gap_detector = None
    ai_bridge = None
    auto_ingest = None
    auto_evolver = None
    alert_manager = None

    # ── 进化/熵/免疫/自适应 ────────────────────────────
    evolution = None
    entropy_engine = None
    immune_system = None
    adaptive_system = None

    # ── 运行时状态 ──────────────────────────────────────
    _start_time: datetime = None
    _management_heartbeat: Optional[str] = None
    _management_loop_active: bool = False
    _stop_event: threading.Event = None  # 管理循环停止信号
    HAS_V3: bool = False

    # ── FastAPI 应用 ────────────────────────────────────
    app = None  # FastAPI 实例，由 main.py 创建

    # ════════════════════════════════════════════════════
    #  AI 运行时配置（热加载用）
    # ════════════════════════════════════════════════════

    def save_ai_config(self, ai_config: dict):
        """保存 AI 配置到运行时存储。"""
        if not self.storage_v2:
            return
        try:
            self.storage_v2.set_config("ai_bridge_config",
                                       json.dumps(ai_config, ensure_ascii=False))
        except Exception:
            pass

    def load_ai_config(self) -> dict:
        """从运行时存储加载 AI 配置覆盖。"""
        if not self.storage_v2:
            return {}
        try:
            raw = self.storage_v2.get_config("ai_bridge_config", "{}")
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def reinitialize_ai_modules(self):
        """热加载 AI 模块（不重启服务）。"""
        try:
            runtime = self.load_ai_config()
            if runtime.get("api_key"):
                api_key_env = runtime.get("api_key_env", "ANTHROPIC_API_KEY")
                os.environ[api_key_env] = runtime["api_key"]
            base_cfg = dict(self.config.get("v3", {}).get("ai_bridge", {}))
            merged = {**base_cfg, **runtime}
            try:
                from ai_bridge import AIBridge
                from auto_ingest import AutoIngest
            except ImportError:
                self.logger.info("v3", "AI 模块不可用（未安装），跳过热加载")
                return

            self.ai_bridge = AIBridge(self.storage_v2, merged,
                                      index=self.index, gap_detector=self.gap_detector)
            self.auto_ingest = AutoIngest(self.storage_v2, self.ai_bridge, merged)
            self.logger.info("v3", "AI 模块已热加载 (provider=%s, model=%s)",
                             merged.get("provider"), merged.get("model"))
        except Exception as e:
            self.logger.info("v3", f"AI 模块热加载失败: {e}")


# 模块级单例 — 所有模块通过 ``from core.state import state`` 引用
state = AppState()
