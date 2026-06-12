# Copyright 2026 Rulerything Project Authors
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

# Copyright 2026 Rulerything Project Authors
"""
配置管理 — 支持 YAML 文件 + 环境变量 + 命令行参数三级覆盖

加载优先级（高 → 低）：
  1. 命令行参数
  2. 环境变量（RULES_* 前缀）
  3. 配置文件 config.yaml
  4. 代码默认值
"""

import os
import yaml
from typing import Any, Dict


# 默认配置
DEFAULTS: Dict[str, Dict[str, Any]] = {
    "server": {
        "host": "0.0.0.0",
        "port": 8000,
        "workers": 4,
    },
    "index": {
        "hot_threshold": 3,
        "cold_days": 30,
        "rebuild_on_start": True,
    },
    "evolution": {
        "enabled": True,
        "auto_apply": True,
        "min_confidence": 0.3,
        "batch_size": 10,
    },
    "logging": {
        "level": "INFO",
        "query_log_enabled": True,
        "metrics_interval_sec": 300,
    },
    "cache": {
        "max_size_mb": 512,
        "preheat_on_start": True,
    },
    "security": {
        "api_key_required": False,
        "rate_limit_per_min": 1000,
    },

    # v3.0 默认配置
    "v3": {
        "enabled": True,
        "storage": "sqlite",
        "dep_miner": {
            "enabled": True,
            "schedule_hour": 2,
            "cooccurrence_window": 1000,
            "pmi_threshold": 0.3,
            "content_sim_threshold": 0.7,
            "use_scipy": True,
            "min_queries_for_pmi": 100,
        },
        "proposal_system": {
            "enabled": True,
            "scan_interval_seconds": 30,
            "cooldown_hours": {
                "index_rebuild": 24,
                "cache_tune": 4,
                "quality_scan": 12,
                "cold_archive": 168,
                "dep_refresh": 24,
            },
            "circuit_breaker": {
                "failure_threshold": 3,
                "recovery_timeout_sec": 300,
            },
        },
        "gap_detector": {
            "enabled": True,
            "schedule_interval_hours": 168,
            "similarity_threshold": 0.3,
            "min_frequency": 5,
            "max_rules_per_week": 15,
            "use_sklearn": True,
        },
        "auto_evolver": {
            "enabled": True,
            "require_approval": False,
            "auto_snapshot_before_evolve": True,
            "max_snapshots": 50,
            "validation_min_samples": 10,
            "validation_effect_threshold": 0.5,
        },
        "ai_bridge": {
            "enabled": False,
            "provider": "claude",
            "endpoint": "",
            "api_key_env": "ANTHROPIC_API_KEY",
            "model": "claude-sonnet-4-6",
            "confidence_threshold": 0.6,
            "daily_limit_usd": 5.0,
            "warn_threshold": 0.8,
            "per_call_limit_usd": 0.5,
            "cache_ttl_hours": 24,
            "cache_max_entries": 5000,
            "max_new_rules_per_session": 10,
            "max_new_rules_per_day": 50,
            "temperature": 0.3,
            "max_tokens": 1024,
            "title_dedup_threshold": 0.7,
            "max_draft_content_length": 500,
            "batch_interval_seconds": 30,
            "budget_sync_interval": 5,
        },
        "timeouts": {
            "dep_miner": 120,
            "gap_detector": 60,
            "auto_evolver": 300,
            "index_rebuild": 600,
            "ai_call": 30,
        },
    },
}

# 环境变量映射：RULES_{SECTION}_{KEY} → (section, key, type)
ENV_MAP = {
    "RULES_SERVER_HOST": ("server", "host", str),
    "RULES_SERVER_PORT": ("server", "port", int),
    "RULES_SERVER_WORKERS": ("server", "workers", int),
    "RULES_INDEX_HOT_THRESHOLD": ("index", "hot_threshold", int),
    "RULES_INDEX_COLD_DAYS": ("index", "cold_days", int),
    "RULES_EVOLUTION_ENABLED": ("evolution", "enabled", bool),
    "RULES_EVOLUTION_AUTO_APPLY": ("evolution", "auto_apply", bool),
    "RULES_EVOLUTION_MIN_CONFIDENCE": ("evolution", "min_confidence", float),
    "RULES_LOGGING_LEVEL": ("logging", "level", str),
    "RULES_LOGGING_QUERY_LOG_ENABLED": ("logging", "query_log_enabled", bool),
    "RULES_CACHE_MAX_SIZE_MB": ("cache", "max_size_mb", int),
    "RULES_CACHE_PREHEAT_ON_START": ("cache", "preheat_on_start", bool),
    "RULES_SECURITY_API_KEY_REQUIRED": ("security", "api_key_required", bool),
    "RULES_SECURITY_RATE_LIMIT_PER_MIN": ("security", "rate_limit_per_min", int),
}


def _deep_merge(base: dict, override: dict) -> dict:
    """深层合并字典。"""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_from_file(path: str = "config.yaml") -> dict:
    """从 YAML 文件加载配置。"""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_from_env() -> dict:
    """从环境变量加载配置（RULES_* 前缀）。"""
    result: Dict[str, Dict] = {}
    for env_key, (section, key, cast) in ENV_MAP.items():
        val = os.environ.get(env_key)
        if val is not None:
            result.setdefault(section, {})
            try:
                if cast == bool:
                    result[section][key] = val.lower() in ("true", "1", "yes")
                else:
                    result[section][key] = cast(val)
            except (ValueError, TypeError):
                pass  # 忽略无法转换的值
    return result


def load_config(
    path: str = "config.yaml",
    cli_overrides: dict = None,
) -> dict:
    """加载配置（YAML → 环境变量 → CLI 覆盖 → 默认值）。

    Args:
        path: YAML 配置文件路径
        cli_overrides: 命令行参数字典，格式 {section: {key: value}}

    Returns:
        合并后的配置字典
    """
    config = _deep_merge(DEFAULTS, load_from_file(path))
    config = _deep_merge(config, load_from_env())
    if cli_overrides:
        config = _deep_merge(config, cli_overrides)
    return config
