"""
Alert — 告警通道抽象（v3.0 Phase C）

支持多种告警输出:
  - LogAlert:    记录到日志（默认，始终启用）
  - WebhookAlert: 发送到 Slack/企微/自定义 URL（可选）

用法:
    alert = AlertManager(config)
    alert.send("system", "high", "SQLite 写入延迟超过 500ms")

配置:
    v3:
      alerts:
        webhook:
          enabled: false
          url: "https://hooks.slack.com/..."
          channel: "#rule-system"
"""

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional


class AlertChannel:
    """告警通道基类。"""

    def send(self, title: str, message: str, level: str = "info") -> bool:
        raise NotImplementedError


class LogAlert(AlertChannel):
    """日志告警通道。"""

    def __init__(self):
        self.logger = logging.getLogger("alert")

    def send(self, title: str, message: str, level: str = "info") -> bool:
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }
        log_level = level_map.get(level, logging.INFO)
        self.logger.log(log_level, "[%s] %s: %s", level.upper(), title, message)
        return True


class WebhookAlert(AlertChannel):
    """Webhook 告警通道 (Slack/企微兼容)。"""

    def __init__(self, config: dict):
        self.url = config.get("url", "")
        self.channel = config.get("channel", "#alerts")
        self.enabled = config.get("enabled", False)
        self._last_error: Optional[str] = None

    def send(self, title: str, message: str, level: str = "info") -> bool:
        if not self.enabled or not self.url:
            return False
        try:
            import urllib.request
            data = json.dumps({
                "text": f"[{level.upper()}] {title}\n{message}",
                "channel": self.channel,
            }).encode("utf-8")
            req = urllib.request.Request(
                self.url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            return True
        except Exception as e:
            self._last_error = str(e)
            return False


class AlertManager:
    """告警管理器。

    聚合多个告警通道，支持:
      - 频率限制（同一告警 N 秒内不重复发送）
      - 级别过滤
    """

    def __init__(self, config: dict):
        self.config = config.get("alerts", {})
        self.channels: List[AlertChannel] = [LogAlert()]

        # Webhook 通道（可选）
        webhook_cfg = self.config.get("webhook", {})
        if webhook_cfg.get("enabled", False) and webhook_cfg.get("url"):
            self.channels.append(WebhookAlert(webhook_cfg))

        # 频率限制: {alert_key: last_send_time}
        self._rate_limits: Dict[str, datetime] = {}
        self._default_cooldown_sec = self.config.get("cooldown_seconds", 300)
        self._lock = threading.Lock()

        # 统计
        self.stats = {
            "total_sent": 0,
            "by_level": defaultdict(int),
            "by_channel": defaultdict(int),
            "last_alert": None,
        }

    def send(self, module: str, level: str, message: str,
             title: Optional[str] = None, cooldown_sec: Optional[int] = None) -> bool:
        """发送告警。

        Args:
            module: 告警来源模块名
            level: debug | info | warning | error | critical
            message: 告警内容
            title: 可选标题（默认自动生成）
            cooldown_sec: 可选频率限制覆盖

        Returns:
            是否发送成功（至少一个通道成功）
        """
        effective_title = title or f"RuleSystem:{module}"
        alert_key = f"{module}:{effective_title}"

        # 频率限制 (线程安全)
        cooldown = cooldown_sec or self._default_cooldown_sec
        with self._lock:
            last = self._rate_limits.get(alert_key)
            if last and (datetime.now() - last).total_seconds() < cooldown:
                return False
            self._rate_limits[alert_key] = datetime.now()

        sent = False
        for channel in self.channels:
            try:
                ok = channel.send(effective_title, message, level)
                if ok:
                    sent = True
                    channel_name = channel.__class__.__name__
                    with self._lock:
                        self.stats["by_channel"][channel_name] += 1
            except Exception:
                pass

        if sent:
            with self._lock:
                self.stats["total_sent"] += 1
                self.stats["by_level"][level] += 1
                self.stats["last_alert"] = datetime.now().isoformat()

        return sent

    def health_check(self) -> dict:
        """告警系统健康状态。"""
        return {
            "log_alert": True,
            "webhook_enabled": any(
                isinstance(c, WebhookAlert) and c.enabled for c in self.channels
            ),
            "channels": [c.__class__.__name__ for c in self.channels],
            "stats": dict(self.stats),
        }
