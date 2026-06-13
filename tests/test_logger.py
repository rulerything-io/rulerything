"""
Logger 模块测试 — JSONFormatter, RuleLogger

覆盖: JSON 格式化, 结构化日志写入 (query/evolution/error/warn/info),
      文件轮转, 目录自动创建, 多条目读取
"""

import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from logger import JSONFormatter, RuleLogger


# ── JSONFormatter ─────────────────────────────────────────────────────────

class TestJSONFormatter:
    """JSONFormatter 格式化行为。"""

    def test_basic_format(self):
        """基础 JSON 输出包含 timestamp, level, log_type, message。"""
        formatter = JSONFormatter()
        record = logging.LogRecord("test", logging.INFO, "test.py", 1,
                                   "hello world", (), None)
        record.log_type = "system"
        record.extra_fields = {}
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["log_type"] == "system"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed

    def test_default_log_type(self):
        """未设置 log_type 时默认 system。"""
        formatter = JSONFormatter()
        record = logging.LogRecord("test", logging.WARNING, "test.py", 1,
                                   "warning msg", (), None)
        record.extra_fields = {}
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["log_type"] == "system"

    def test_extra_fields(self):
        """extra_fields 合并到 JSON 顶层。"""
        formatter = JSONFormatter()
        record = logging.LogRecord("test", logging.INFO, "test.py", 1,
                                   "with extras", (), None)
        record.log_type = "query"
        record.extra_fields = {"latency_ms": 12.5, "result_count": 5}
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["latency_ms"] == 12.5
        assert parsed["result_count"] == 5

    def test_exception_info(self):
        """含异常信息时输出 exception 字段。"""
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = logging.LogRecord("test", logging.ERROR, "test.py", 1,
                                   "error occurred", (), exc_info)
        record.log_type = "system"
        record.extra_fields = {}
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]
        assert "test error" in parsed["exception"]

    def test_different_levels(self):
        """不同级别反映在 JSON 的 level 字段。"""
        formatter = JSONFormatter()
        for level, name in [(logging.DEBUG, "DEBUG"), (logging.INFO, "INFO"),
                            (logging.WARNING, "WARNING"), (logging.ERROR, "ERROR"),
                            (logging.CRITICAL, "CRITICAL")]:
            record = logging.LogRecord("test", level, "test.py", 1,
                                       f"{name} msg", (), None)
            record.log_type = "system"
            record.extra_fields = {}
            parsed = json.loads(formatter.format(record))
            assert parsed["level"] == name

    def test_ensure_ascii_false(self):
        """非 ASCII 字符正常保留（ensure_ascii=False）。"""
        formatter = JSONFormatter()
        record = logging.LogRecord("test", logging.INFO, "test.py", 1,
                                   "中文消息", (), None)
        record.log_type = "system"
        record.extra_fields = {}
        output = formatter.format(record)
        assert "中文消息" in output
        parsed = json.loads(output)
        assert parsed["message"] == "中文消息"


# ── RuleLogger ────────────────────────────────────────────────────────────

class TestRuleLoggerInit:
    """RuleLogger 初始化。"""

    def test_log_dir_created(self):
        """日志目录自动创建。"""
        tmpdir = tempfile.mkdtemp()
        log_dir = Path(tmpdir) / "my_logs"
        try:
            logger = RuleLogger(str(log_dir))
            logger.shutdown()
            assert log_dir.exists()
            assert log_dir.is_dir()
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_rotating_file_handler_created(self):
        """创建 RotatingFileHandler 指向 system.log。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.shutdown()
            log_path = Path(tmpdir) / "system.log"
            assert log_path.exists()
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_empty_log_file_after_init(self):
        """初始化后日志文件存在但为空。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.shutdown()
            content = Path(tmpdir, "system.log").read_text(encoding="utf-8").strip()
            assert content == ""
        finally:
            import shutil
            shutil.rmtree(tmpdir)


class TestRuleLoggerQuery:
    """query() 结构化日志。"""

    def test_query_basic(self):
        """query() 写入 query 类型的日志条目。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.query(query="SELECT * FROM rules", search_type="exact",
                         latency_ms=15.3, result_count=42, cache_hit=True)
            logger.shutdown()

            lines = Path(tmpdir, "system.log").read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) >= 1
            entry = json.loads(lines[0])
            assert entry["log_type"] == "query"
            assert entry["query"] == "SELECT * FROM rules"
            assert entry["search_type"] == "exact"
            assert entry["latency_ms"] == 15.3
            assert entry["result_count"] == 42
            assert entry["cache_hit"] is True
            assert entry["level"] == "INFO"
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_query_with_result_ids(self):
        """query() 记录 result_ids 列表。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.query(query="find me", result_ids=["r/001", "r/002", "r/003"])
            logger.shutdown()

            entry = json.loads(Path(tmpdir, "system.log").read_text(encoding="utf-8").strip())
            assert entry["result_ids"] == ["r/001", "r/002", "r/003"]
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_query_with_feedback(self):
        """query() 记录 user_feedback。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.query(query="test", user_feedback=True)
            logger.shutdown()

            entry = json.loads(Path(tmpdir, "system.log").read_text(encoding="utf-8").strip())
            assert entry["user_feedback"] is True
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_query_latency_rounded(self):
        """latency_ms 保留两位小数。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.query(query="q", latency_ms=3.14159)
            logger.shutdown()

            entry = json.loads(Path(tmpdir, "system.log").read_text(encoding="utf-8").strip())
            assert entry["latency_ms"] == 3.14
        finally:
            import shutil
            shutil.rmtree(tmpdir)


class TestRuleLoggerEvolution:
    """evolution() 结构化日志。"""

    def test_evolution_basic(self):
        """evolution() 写入 evolution 类型条目。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.evolution(rule_id="test/001", evolution_type="confidence_update",
                             old_confidence=0.5, new_confidence=0.85,
                             trigger_reason="positive feedback")
            logger.shutdown()

            entry = json.loads(Path(tmpdir, "system.log").read_text(encoding="utf-8").strip())
            assert entry["log_type"] == "evolution"
            assert entry["rule_id"] == "test/001"
            assert entry["evolution_type"] == "confidence_update"
            assert entry["old_confidence"] == 0.5
            assert entry["new_confidence"] == 0.85
            assert entry["trigger_reason"] == "positive feedback"
            assert entry["level"] == "INFO"
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_evolution_confidence_rounded(self):
        """confidence 保留三位小数。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.evolution(rule_id="r/999", evolution_type="promote",
                             old_confidence=0.1234, new_confidence=0.5678)
            logger.shutdown()

            entry = json.loads(Path(tmpdir, "system.log").read_text(encoding="utf-8").strip())
            assert entry["old_confidence"] == 0.123
            assert entry["new_confidence"] == 0.568
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_evolution_empty_trigger(self):
        """trigger_reason 默认为空字符串。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.evolution(rule_id="r/001", evolution_type="decay")
            logger.shutdown()

            entry = json.loads(Path(tmpdir, "system.log").read_text(encoding="utf-8").strip())
            assert entry["trigger_reason"] == ""
        finally:
            import shutil
            shutil.rmtree(tmpdir)


class TestRuleLoggerError:
    """error() 结构化日志。"""

    def test_error_basic(self):
        """error() 写入 ERROR 级别的 system 类型条目。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.error(component="index", error_type="corruption",
                         message="Index data corrupted")
            logger.shutdown()

            entry = json.loads(Path(tmpdir, "system.log").read_text(encoding="utf-8").strip())
            assert entry["level"] == "ERROR"
            assert entry["log_type"] == "system"
            assert entry["component"] == "index"
            assert entry["error_type"] == "corruption"
            assert entry["message"] == "Index data corrupted"
            assert "stack_trace" in entry
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_error_with_stack_trace(self):
        """error() 可附带 stack_trace。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.error(component="db", error_type="sqlite_error",
                         message="disk I/O error",
                         stack_trace="Traceback (most recent call last):\n  ...")
            logger.shutdown()

            entry = json.loads(Path(tmpdir, "system.log").read_text(encoding="utf-8").strip())
            assert "Traceback" in entry["stack_trace"]
        finally:
            import shutil
            shutil.rmtree(tmpdir)


class TestRuleLoggerWarn:
    """warn() 结构化日志。"""

    def test_warn_basic(self):
        """warn() 写入 WARNING 级别条目。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.warn(component="cache", message="Cache miss rate high")
            logger.shutdown()

            entry = json.loads(Path(tmpdir, "system.log").read_text(encoding="utf-8").strip())
            assert entry["level"] == "WARNING"
            assert entry["log_type"] == "system"
            assert entry["component"] == "cache"
            assert entry["message"] == "Cache miss rate high"
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_warn_with_extra(self):
        """warn() 支持额外 kwargs 合并到 JSON。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.warn(component="search", message="Slow query",
                        latency_ms=2500, threshold=1000)
            logger.shutdown()

            entry = json.loads(Path(tmpdir, "system.log").read_text(encoding="utf-8").strip())
            assert entry["latency_ms"] == 2500
            assert entry["threshold"] == 1000
        finally:
            import shutil
            shutil.rmtree(tmpdir)


class TestRuleLoggerInfo:
    """info() 结构化日志。"""

    def test_info_basic(self):
        """info() 写入 INFO 级别条目。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.info(component="startup", message="System initialized")
            logger.shutdown()

            entry = json.loads(Path(tmpdir, "system.log").read_text(encoding="utf-8").strip())
            assert entry["level"] == "INFO"
            assert entry["log_type"] == "system"
            assert entry["component"] == "startup"
            assert entry["message"] == "System initialized"
        finally:
            import shutil
            shutil.rmtree(tmpdir)


class TestRuleLoggerMultiEntry:
    """多条目写入与读取。"""

    def test_multiple_entries_in_order(self):
        """连续写入多条日志，按写入顺序读取。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.query(query="first", latency_ms=1.0, result_count=1)
            logger.evolution(rule_id="r/001", evolution_type="create")
            logger.info(component="test", message="middle")
            logger.warn(component="test", message="warning")
            logger.error(component="test", error_type="fail", message="error")
            logger.query(query="last", latency_ms=2.0, result_count=2)
            logger.shutdown()

            lines = Path(tmpdir, "system.log").read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 6

            entries = [json.loads(line) for line in lines]
            assert entries[0]["log_type"] == "query"
            assert entries[0]["query"] == "first"
            assert entries[1]["log_type"] == "evolution"
            assert entries[2]["log_type"] == "system"
            assert entries[2]["level"] == "INFO"
            assert entries[3]["level"] == "WARNING"
            assert entries[4]["level"] == "ERROR"
            assert entries[5]["log_type"] == "query"
            assert entries[5]["query"] == "last"
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_timestamps_are_isoformat(self):
        """所有条目 timestamp 为 ISO 格式。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.query(query="q1")
            logger.evolution(rule_id="r/001", evolution_type="update")
            logger.shutdown()

            lines = Path(tmpdir, "system.log").read_text(encoding="utf-8").strip().split("\n")
            for line in lines:
                entry = json.loads(line)
                assert "T" in entry["timestamp"], f"Not ISO format: {entry['timestamp']}"
        finally:
            import shutil
            shutil.rmtree(tmpdir)


class TestRuleLoggerEdgeCases:
    """边界情况。"""

    def test_log_directory_nested(self):
        """深层嵌套的日志目录自动创建。"""
        tmpdir = tempfile.mkdtemp()
        nested = Path(tmpdir) / "a" / "b" / "c" / "logs"
        try:
            logger = RuleLogger(str(nested))
            logger.info(component="test", message="nested dir")
            logger.shutdown()
            assert nested.exists()
            assert (nested / "system.log").exists()
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_shutdown_clears_handlers(self):
        """shutdown() 后 logger 无 handlers。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.shutdown()
            assert len(logger._logger.handlers) == 0
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_double_shutdown_no_error(self):
        """两次 shutdown() 不报错。"""
        tmpdir = tempfile.mkdtemp()
        try:
            logger = RuleLogger(tmpdir)
            logger.shutdown()
            logger.shutdown()
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_reinitialize_clears_old_handlers(self):
        """重新初始化 RuleLogger 清除旧 handlers 并写入新位置。"""
        tmpdir = tempfile.mkdtemp()
        try:
            dir1 = Path(tmpdir) / "dir1"
            dir2 = Path(tmpdir) / "dir2"
            logger1 = RuleLogger(str(dir1))
            logger1.query(query="from dir1")
            logger1.shutdown()

            logger2 = RuleLogger(str(dir2))
            logger2.query(query="from dir2")
            logger2.shutdown()

            content1 = Path(dir1, "system.log").read_text(encoding="utf-8").strip()
            content2 = Path(dir2, "system.log").read_text(encoding="utf-8").strip()

            entry1 = json.loads(content1.split("\n")[0])
            entry2 = json.loads(content2.split("\n")[0])
            assert entry1["query"] == "from dir1"
            assert entry2["query"] == "from dir2"
        finally:
            import shutil
            shutil.rmtree(tmpdir)
