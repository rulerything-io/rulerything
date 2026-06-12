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
结构化日志模块 — JSON 格式输出，按 log_type 区分
"""

import json
import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


class JSONFormatter(logging.Formatter):
    """JSON 日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "log_type": getattr(record, "log_type", "system"),
            "message": record.getMessage(),
        }
        # 合并 extra 字段
        for key, val in getattr(record, "extra_fields", {}).items():
            log_entry[key] = val
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


class RuleLogger:
    """结构化日志管理器。

    用法：
        logger = RuleLogger("logs")
        logger.query(query="...", latency_ms=2.3, ...)
        logger.evolution(rule_id="...", ...)
        logger.error(component="index", error_type="...", message="...")
    """

    def __init__(self, log_dir: str = "logs", level: str = "INFO",
                 max_bytes: int = 100 * 1024 * 1024,  # 100 MB
                 backup_count: int = 10):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._logger = logging.getLogger("rule_system")
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self._logger.handlers.clear()

        handler = logging.handlers.RotatingFileHandler(
            filename=self.log_dir / "system.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(JSONFormatter())
        self._logger.addHandler(handler)

        # 同时输出到控制台（可选）
        console = logging.StreamHandler()
        console.setFormatter(JSONFormatter())
        console.setLevel(logging.WARN)  # 控制台只显示 WARN+
        self._logger.addHandler(console)

    def _log(self, level: int, log_type: str, message: str = "", **kwargs):
        extra = {
            "log_type": log_type,
            "extra_fields": kwargs,
        }
        self._logger.log(level, message, extra=extra)

    def query(self, query: str, search_type: str = "exact",
              latency_ms: float = 0.0, result_count: int = 0,
              result_ids: Optional[list] = None,
              cache_hit: bool = False,
              user_feedback: Optional[bool] = None):
        """记录查询日志。"""
        self._log(
            logging.INFO, "query",
            query=query, search_type=search_type,
            latency_ms=round(latency_ms, 2),
            result_count=result_count,
            result_ids=result_ids or [],
            cache_hit=cache_hit,
            user_feedback=user_feedback,
        )

    def evolution(self, rule_id: str, evolution_type: str,
                  old_confidence: float = 0.0, new_confidence: float = 0.0,
                  trigger_reason: str = ""):
        """记录进化日志。"""
        self._log(
            logging.INFO, "evolution",
            rule_id=rule_id, evolution_type=evolution_type,
            old_confidence=round(old_confidence, 3),
            new_confidence=round(new_confidence, 3),
            trigger_reason=trigger_reason,
        )

    def error(self, component: str, error_type: str, message: str,
              stack_trace: Optional[str] = None):
        """记录错误日志。"""
        self._log(
            logging.ERROR, "system",
            component=component, error_type=error_type,
            message=message, stack_trace=stack_trace or "",
        )

    def warn(self, component: str, message: str, **kwargs):
        """记录警告日志。"""
        self._log(logging.WARN, "system", component=component, message=message, **kwargs)

    def info(self, component: str, message: str, **kwargs):
        """记录信息日志。"""
        self._log(logging.INFO, "system", component=component, message=message, **kwargs)
