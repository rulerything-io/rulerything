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
