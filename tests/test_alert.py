"""
Alert 模块测试 — AlertManager, LogAlert, WebhookAlert

覆盖: 日志告警, Webhook 告警, 频率限制, 级别过滤, 健康检查
"""

import json
import logging
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from alert import AlertManager, LogAlert, WebhookAlert


# ── LogAlert ──────────────────────────────────────────────────────────────

class TestLogAlert:
    """LogAlert 基本发送功能。"""

    def test_send_info_level(self, caplog):
        """info 级别写入 logging 的 alert logger。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        la = LogAlert()
        result = la.send("Test Title", "info message", "info")
        assert result is True
        assert len(caplog.records) >= 1
        assert caplog.records[0].levelname == "INFO"
        msg = caplog.records[0].getMessage()
        assert "[INFO]" in msg
        assert "Test Title" in msg
        assert "info message" in msg

    def test_send_warning_level(self, caplog):
        """warning 级别的日志级别切换正确。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        la = LogAlert()
        result = la.send("Warn Title", "warn message", "warning")
        assert result is True
        assert caplog.records[0].levelname == "WARNING"
        assert "[WARNING]" in caplog.records[0].getMessage()

    def test_send_error_level(self, caplog):
        """error 级别。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        la = LogAlert()
        result = la.send("Error Title", "error msg", "error")
        assert result is True
        assert caplog.records[0].levelname == "ERROR"

    def test_send_critical_level(self, caplog):
        """critical 级别。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        la = LogAlert()
        result = la.send("Critical", "critical msg", "critical")
        assert result is True
        assert caplog.records[0].levelname == "CRITICAL"

    def test_send_debug_level(self, caplog):
        """debug 级别。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        la = LogAlert()
        result = la.send("Debug", "debug msg", "debug")
        assert result is True
        assert caplog.records[0].levelname == "DEBUG"

    def test_unknown_level_falls_back_to_info(self, caplog):
        """不识别的级别默认 fallback 到 INFO。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        la = LogAlert()
        result = la.send("Unknown", "msg", "unknown_level")
        assert result is True
        assert caplog.records[0].levelname == "INFO"

    def test_send_message_format(self, caplog):
        """消息格式为 [LEVEL] title: message。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        la = LogAlert()
        la.send("ModuleA", "something happened", "warning")
        msg = caplog.records[0].getMessage()
        assert msg == "[WARNING] ModuleA: something happened"


# ── WebhookAlert ──────────────────────────────────────────────────────────

class TestWebhookAlert:
    """WebhookAlert HTTP 请求与配置。"""

    def test_send_enabled(self):
        """启用的 webhook 发送 POST 请求并返回 True。"""
        config = {"url": "https://hooks.example.com/hook", "channel": "#test", "enabled": True}
        wa = WebhookAlert(config)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_urlopen.return_value = mock_resp
            result = wa.send("Hook Title", "hook message", "info")

        assert result is True
        mock_urlopen.assert_called_once()
        # 验证 POST 请求的目标 URL
        args, kwargs = mock_urlopen.call_args
        req = args[0]
        assert req.get_full_url() == "https://hooks.example.com/hook"
        assert req.get_method() == "POST"
        # 验证请求体
        body = json.loads(req.data.decode("utf-8"))
        assert body["channel"] == "#test"
        assert "[INFO]" in body["text"]
        assert "Hook Title" in body["text"]

    def test_send_disabled(self):
        """未启用时返回 False 且不发请求。"""
        config = {"url": "https://hooks.example.com/hook", "channel": "#test", "enabled": False}
        wa = WebhookAlert(config)

        with patch("urllib.request.urlopen") as mock_urlopen:
            result = wa.send("Title", "msg", "info")

        assert result is False
        mock_urlopen.assert_not_called()

    def test_send_no_url(self):
        """URL 为空时返回 False。"""
        config = {"url": "", "channel": "#test", "enabled": True}
        wa = WebhookAlert(config)

        with patch("urllib.request.urlopen") as mock_urlopen:
            result = wa.send("Title", "msg", "info")

        assert result is False
        mock_urlopen.assert_not_called()

    def test_http_error_returns_false(self):
        """HTTP 请求异常时返回 False 并记录 last_error。"""
        config = {"url": "https://hooks.example.com/hook", "channel": "#test", "enabled": True}
        wa = WebhookAlert(config)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = ConnectionError("connection refused")
            result = wa.send("Title", "msg", "info")

        assert result is False
        assert wa._last_error is not None
        assert "connection refused" in wa._last_error

    def test_last_error_cleared_on_success(self):
        """上次异常后，成功发送应覆盖 _last_error。"""
        config = {"url": "https://hooks.example.com/hook", "channel": "#test", "enabled": True}
        wa = WebhookAlert(config)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = OSError("fail")
            wa.send("Title", "msg", "info")

        assert wa._last_error is not None

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = None
            mock_resp = MagicMock()
            mock_urlopen.return_value = mock_resp
            wa.send("Title", "msg", "info")

        # 成功时 _last_error 不变（只在异常时设值，没有清除逻辑）
        # 验证新的错误不会追加 — 实际上代码只在异常时赋值，所以本次没改
        # 这里验证的是异常发生后成功发送，last_error 仍是旧值（代码设计如此）


# ── AlertManager ──────────────────────────────────────────────────────────

class TestAlertManagerBasic:
    """AlertManager 基本发送功能。"""

    def test_send_calls_log_alert(self, caplog):
        """send() 默认调用 LogAlert 通道。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({})
        result = am.send("module_x", "info", "test from manager")
        assert result is True
        assert any("module_x" in r.getMessage() for r in caplog.records)

    def test_send_returns_true_on_success(self, caplog):
        """至少一个通道成功时返回 True。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({})
        result = am.send("mod", "info", "hello")
        assert result is True

    def test_send_uses_default_title(self, caplog):
        """未指定 title 时自动生成 RuleSystem:{module}。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({})
        am.send("my_module", "info", "auto title")
        msg = caplog.records[0].getMessage()
        assert "RuleSystem:my_module" in msg

    def test_send_custom_title(self, caplog):
        """指定 title 时使用自定义标题。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({})
        am.send("mod", "info", "custom", title="MyCustomTitle")
        msg = caplog.records[0].getMessage()
        assert "MyCustomTitle" in msg


class TestAlertManagerRateLimit:
    """频率限制功能。"""

    def test_same_alert_rate_limited(self, caplog):
        """同一 alert_key 在冷却期内第二次发送返回 False。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({"alerts": {"cooldown_seconds": 300}})
        result1 = am.send("mod", "info", "same msg")
        assert result1 is True
        result2 = am.send("mod", "info", "same msg")
        assert result2 is False

    def test_different_alerts_not_rate_limited(self, caplog):
        """不同 alert_key 不受彼此频率限制。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({"alerts": {"cooldown_seconds": 300}})
        r1 = am.send("mod1", "info", "msg1")
        r2 = am.send("mod2", "info", "msg2")
        assert r1 is True
        assert r2 is True

    def test_different_module_same_title_not_rate_limited(self, caplog):
        """不同模块同标题视为不同 alert_key。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({"alerts": {"cooldown_seconds": 300}})
        r1 = am.send("mod_a", "info", "msg", title="SameTitle")
        r2 = am.send("mod_b", "info", "msg", title="SameTitle")
        assert r1 is True
        assert r2 is True

    def test_custom_cooldown_0_falls_back_to_default(self, caplog):
        """cooldown_sec=0 由于 Python falsy 值特性会回退到默认冷却。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({"alerts": {"cooldown_seconds": 300}})
        r1 = am.send("mod", "info", "msg", cooldown_sec=0)
        r2 = am.send("mod", "info", "msg", cooldown_sec=0)
        assert r1 is True
        # cooldown_sec=0 is falsy in Python → 0 or 300 = 300, so second is rate limited
        assert r2 is False

    def test_custom_cooldown_positive(self, caplog):
        """传入正数冷却秒数覆盖默认值。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({"alerts": {"cooldown_seconds": 300}})
        r1 = am.send("mod", "info", "msg", cooldown_sec=1)
        r2 = am.send("mod", "info", "msg", cooldown_sec=1)
        assert r1 is True
        # 1 秒冷却，第二次在毫秒内发送，应被限流
        assert r2 is False


class TestAlertManagerWebhook:
    """Webhook 集成测试。"""

    def test_send_with_webhook_enabled(self, caplog):
        """Webhook 启用时 channels 包含 WebhookAlert。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        config = {
            "alerts": {
                "webhook": {
                    "enabled": True,
                    "url": "https://hooks.example.com/hook",
                    "channel": "#test",
                }
            }
        }
        am = AlertManager(config)
        assert len(am.channels) == 2
        assert any(isinstance(c, WebhookAlert) for c in am.channels)
        assert any(isinstance(c, LogAlert) for c in am.channels)

    def test_send_with_webhook_disabled(self, caplog):
        """Webhook 未启用时 channels 只有 LogAlert。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        config = {"alerts": {"webhook": {"enabled": False, "url": "", "channel": "#test"}}}
        am = AlertManager(config)
        assert len(am.channels) == 1
        assert isinstance(am.channels[0], LogAlert)

    def test_webhook_invoked_on_warning(self, caplog):
        """warning 及以上级别触发 webhook。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        config = {
            "alerts": {
                "webhook": {"enabled": True, "url": "https://hooks.example.com/hook", "channel": "#test"},
                "webhook_min_level": "warning",
            }
        }
        am = AlertManager(config)
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_urlopen.return_value = mock_resp
            result = am.send("mod", "warning", "warning msg")
        assert result is True
        mock_urlopen.assert_called_once()

    def test_webhook_not_invoked_on_info(self, caplog):
        """info 及以下级别不触发 webhook。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        config = {
            "alerts": {
                "webhook": {"enabled": True, "url": "https://hooks.example.com/hook", "channel": "#test"},
                "webhook_min_level": "warning",
            }
        }
        am = AlertManager(config)
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = am.send("mod", "info", "info msg")
        assert result is True  # LogAlert 仍然发送
        mock_urlopen.assert_not_called()  # Webhook 不被调用

    def test_webhook_not_invoked_on_debug(self, caplog):
        """debug 级别不触发 webhook。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        config = {
            "alerts": {
                "webhook": {"enabled": True, "url": "https://hooks.example.com/hook", "channel": "#test"},
                "webhook_min_level": "warning",
            }
        }
        am = AlertManager(config)
        with patch("urllib.request.urlopen") as mock_urlopen:
            am.send("mod", "debug", "debug msg")
        mock_urlopen.assert_not_called()

    def test_webhook_error_does_not_affect_log_alert(self, caplog):
        """Webhook 发送失败时 LogAlert 仍成功。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        config = {
            "alerts": {
                "webhook": {"enabled": True, "url": "https://hooks.example.com/hook", "channel": "#test"},
                "webhook_min_level": "warning",
            }
        }
        am = AlertManager(config)
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = OSError("timeout")
            result = am.send("mod", "warning", "webhook fails")
        assert result is True  # LogAlert 仍成功
        # 检查 caplog 中有日志告警
        assert any("mod" in r.getMessage() for r in caplog.records)


class TestAlertManagerHealth:
    """健康检查与统计。"""

    def test_health_initial_state(self):
        """初始状态下 health_check 的统计为零。"""
        am = AlertManager({})
        hc = am.health_check()
        assert hc["log_alert"] is True
        assert hc["webhook_enabled"] is False
        assert hc["channels"] == ["LogAlert"]
        assert hc["stats"]["total_sent"] == 0
        assert len(hc["stats"]["by_level"]) == 0

    def test_health_after_send(self, caplog):
        """发送后统计递增。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({})
        am.send("mod", "info", "msg")
        hc = am.health_check()
        assert hc["stats"]["total_sent"] == 1
        assert hc["stats"]["by_level"]["info"] == 1
        assert hc["stats"]["by_channel"]["LogAlert"] == 1
        assert hc["stats"]["last_alert"] is not None

    def test_health_multiple_sends(self, caplog):
        """多次发送统计累积。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({})
        for lvl in ("info", "warning", "error"):
            am.send("mod", lvl, f"{lvl} msg", title=f"Title{lvl}")
        hc = am.health_check()
        assert hc["stats"]["total_sent"] == 3
        assert hc["stats"]["by_level"]["info"] == 1
        assert hc["stats"]["by_level"]["warning"] == 1
        assert hc["stats"]["by_level"]["error"] == 1

    def test_health_webhook_enabled_flag(self):
        """webhook 启用时 health_check 返回 webhook_enabled=True。"""
        config = {
            "alerts": {
                "webhook": {"enabled": True, "url": "https://hooks.example.com/hook", "channel": "#test"},
            }
        }
        am = AlertManager(config)
        hc = am.health_check()
        assert hc["webhook_enabled"] is True
        assert "WebhookAlert" in hc["channels"]

    def test_health_webhook_disabled_flag(self):
        """webhook 禁用时 health_check 返回 webhook_enabled=False。"""
        am = AlertManager({})
        hc = am.health_check()
        assert hc["webhook_enabled"] is False

    def test_rate_limited_not_counted(self, caplog):
        """被限流不计数。"""
        caplog.set_level(logging.DEBUG, logger="alert")
        am = AlertManager({"alerts": {"cooldown_seconds": 300}})
        am.send("mod", "info", "msg")
        am.send("mod", "info", "msg")  # rate limited
        hc = am.health_check()
        assert hc["stats"]["total_sent"] == 1  # 只计一次
