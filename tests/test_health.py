"""
Health 模块测试 — StartupCheck, HealthCheck

覆盖: 单项检查运行, 启动自检套件, 数据目录检查, 磁盘空间检查,
      配置校验, 关键/非关键检查路由, run_all/run_critical 报告格式
"""

import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from health import HealthCheck, StartupCheck


# ── HealthCheck ───────────────────────────────────────────────────────────

class TestHealthCheck:
    """HealthCheck 单项检查。"""

    def test_run_ok(self):
        """通过检查返回 status=ok。"""
        hc = HealthCheck("test_check", "A test", lambda: (True, "all good"))
        result = hc.run()
        assert result["name"] == "test_check"
        assert result["description"] == "A test"
        assert result["status"] == "ok"
        assert result["message"] == "all good"
        assert result["critical"] is False
        assert "duration_ms" in result
        assert result["duration_ms"] >= 0

    def test_run_failed(self):
        """失败检查返回 status=failed。"""
        hc = HealthCheck("fail_check", "Failing check",
                         lambda: (False, "something is wrong"))
        result = hc.run()
        assert result["status"] == "failed"
        assert result["message"] == "something is wrong"

    def test_critical_flag(self):
        """critical 参数在结果中反映。"""
        hc = HealthCheck("critical_check", "Critical check",
                         lambda: (True, "ok"), critical=True)
        result = hc.run()
        assert result["critical"] is True

    def test_non_critical_default(self):
        """不传 critical 默认为 False。"""
        hc = HealthCheck("non_crit", "Non-critical", lambda: (True, "ok"))
        result = hc.run()
        assert result["critical"] is False

    def test_exception_handling(self):
        """检查函数抛出异常时返回 status=error。"""
        def broken():
            raise ValueError("internal error occurred")

        hc = HealthCheck("broken", "Broken check", broken)
        result = hc.run()
        assert result["status"] == "error"
        assert "internal error occurred" in result["message"]

    def test_duration_measured(self):
        """duration_ms 反映实际执行时间。"""
        def slow_check():
            time.sleep(0.01)
            return True, "done"

        hc = HealthCheck("slow", "Slow check", slow_check)
        result = hc.run()
        assert result["duration_ms"] >= 10  # at least 10ms

    def test_name_and_description_preserved_on_error(self):
        """异常时 name 和 description 仍保留。"""
        def crash():
            raise RuntimeError("crash")

        hc = HealthCheck("crash_test", "This crashes", crash)
        result = hc.run()
        assert result["name"] == "crash_test"
        assert result["description"] == "This crashes"
        assert result["status"] == "error"


# ── StartupCheck: _check_data_dir ─────────────────────────────────────────

class TestStartupCheckDataDir:
    """数据目录检查。"""

    def test_data_dir_exists(self):
        """存在的可写目录返回 ok。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(data_dir=tmpdir)
            ok, msg = sc._check_data_dir()
            assert ok is True
            assert msg == str(Path(tmpdir).resolve())
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_data_dir_missing(self):
        """不存在的目录返回失败。"""
        sc = StartupCheck(data_dir="Z:\\_nonexistent_dir_test_12345_")
        ok, msg = sc._check_data_dir()
        assert ok is False
        assert "不存在" in msg

    def test_data_dir_not_writable(self):
        """不可写目录（模拟权限错误）返回失败。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(data_dir=tmpdir)
            with patch.object(Path, "write_text") as mock_write:
                mock_write.side_effect = PermissionError("Access denied")
                ok, msg = sc._check_data_dir()
            assert ok is False
            assert "不可读写" in msg
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_data_dir_critical_failure_stops_startup(self):
        """data_dir 是关键检查，失败时 can_start=False。"""
        sc = StartupCheck(data_dir="Z:\\_nonexistent_dir_test_12345_")
        report = sc.run_all()
        assert report["all_ok"] is False
        assert report["critical_failure"] is True
        assert report["can_start"] is False


# ── StartupCheck: _check_disk_space ───────────────────────────────────────

class TestStartupCheckDiskSpace:
    """磁盘空间检查。"""

    def test_disk_space_ok(self):
        """有足够磁盘空间时返回 ok。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(data_dir=tmpdir)
            ok, msg = sc._check_disk_space()
            assert ok is True
            assert "MB 可用" in msg
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_disk_space_low(self):
        """可用空间不足 100MB 时返回失败。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(data_dir=tmpdir)
            with patch("shutil.disk_usage") as mock_du:
                # shutil.disk_usage returns a namedtuple(total, used, free)
                from collections import namedtuple
                DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])
                mock_du.return_value = DiskUsage(
                    total=500 * 1024**3,
                    used=450 * 1024**3,
                    free=50 * 1024**2,
                )
                ok, msg = sc._check_disk_space()
            assert ok is False
            assert "不足" in msg
            assert "50MB" in msg
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_disk_space_error(self):
        """disk_usage 异常时返回失败。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(data_dir=tmpdir)
            with patch("shutil.disk_usage") as mock_du:
                mock_du.side_effect = PermissionError("Access denied")
                ok, msg = sc._check_disk_space()
            assert ok is False
            assert "错误" in msg
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_disk_space_boundary_99mb(self):
        """边界值 99MB 应失败。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(data_dir=tmpdir)
            from collections import namedtuple
            DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])
            with patch("shutil.disk_usage") as mock_du:
                free_bytes = 99 * 1024 * 1024
                mock_du.return_value = DiskUsage(
                    total=500 * 1024**3,
                    used=500 * 1024**3 - free_bytes,
                    free=free_bytes,
                )
                ok, msg = sc._check_disk_space()
            assert ok is False
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_disk_space_non_critical(self):
        """磁盘检查非关键，不影响 can_start。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(data_dir=tmpdir)
            from collections import namedtuple
            DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])
            with patch("shutil.disk_usage") as mock_du:
                mock_du.return_value = DiskUsage(
                    total=500 * 1024**3,
                    used=450 * 1024**3,
                    free=50 * 1024**2,
                )
                report = sc.run_all()
            assert report["all_ok"] is False
            assert report["can_start"] is True  # 非关键检查不影响启动
        finally:
            import shutil
            shutil.rmtree(tmpdir)


# ── StartupCheck: _check_config ───────────────────────────────────────────

class TestStartupCheckConfig:
    """配置有效性检查。"""

    def test_empty_config_valid(self):
        """空配置视为有效。"""
        sc = StartupCheck(config={})
        ok, msg = sc._check_config()
        assert ok is True
        assert msg == "配置有效"

    def test_valid_sqlite_storage(self):
        """sqlite 存储类型有效。"""
        sc = StartupCheck(config={"v3": {"enabled": True, "storage": "sqlite"}})
        ok, msg = sc._check_config()
        assert ok is True

    def test_valid_jsonl_storage(self):
        """jsonl 存储类型有效。"""
        sc = StartupCheck(config={"v3": {"enabled": True, "storage": "jsonl"}})
        ok, msg = sc._check_config()
        assert ok is True

    def test_invalid_storage(self):
        """不支持的存储类型报错。"""
        sc = StartupCheck(config={"v3": {"enabled": True, "storage": "mysql"}})
        ok, msg = sc._check_config()
        assert ok is False
        assert "无效" in msg
        assert "mysql" in msg

    def test_disabled_v3_valid(self):
        """v3.enabled=false 时跳过存储检查。"""
        sc = StartupCheck(config={"v3": {"enabled": False, "storage": "anything"}})
        ok, msg = sc._check_config()
        assert ok is True

    def test_ai_bridge_missing_api_key_env(self):
        """ai_bridge 启用但未设置 api_key_env 时报错。"""
        sc = StartupCheck(config={
            "v3": {
                "enabled": True,
                "storage": "sqlite",
                "ai_bridge": {"enabled": True},
            }
        })
        ok, msg = sc._check_config()
        assert ok is False
        assert "api_key_env" in msg

    def test_ai_bridge_disabled_ok(self):
        """ai_bridge 禁用时不需要 api_key_env。"""
        sc = StartupCheck(config={
            "v3": {
                "enabled": True,
                "storage": "sqlite",
                "ai_bridge": {"enabled": False},
            }
        })
        ok, msg = sc._check_config()
        assert ok is True

    def test_ai_bridge_with_api_key_env_ok(self):
        """ai_bridge 启用且设置 api_key_env 视为有效。"""
        sc = StartupCheck(config={
            "v3": {
                "enabled": True,
                "storage": "sqlite",
                "ai_bridge": {"enabled": True, "api_key_env": "MY_API_KEY"},
            }
        })
        ok, msg = sc._check_config()
        assert ok is True


# ── StartupCheck: _check_sqlite_integrity ─────────────────────────────────

class TestStartupCheckSQLite:
    """SQLite 完整性检查。"""

    def test_integrity_ok(self):
        """完整检查通过。"""
        tmpdir = tempfile.mkdtemp()
        try:
            storage = MagicMock()
            storage.integrity_check.return_value = []
            sc = StartupCheck(storage=storage, data_dir=tmpdir)
            ok, msg = sc._check_sqlite_integrity()
            assert ok is True
            assert msg == "ok"
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_integrity_errors(self):
        """完整检查失败时返回错误详情。"""
        tmpdir = tempfile.mkdtemp()
        try:
            storage = MagicMock()
            storage.integrity_check.return_value = ["page corruption at offset 1024"]
            sc = StartupCheck(storage=storage, data_dir=tmpdir)
            ok, msg = sc._check_sqlite_integrity()
            assert ok is False
            assert "完整性检查失败" in msg
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_integrity_errors_truncated(self):
        """错误列表超过 3 条时截断。"""
        tmpdir = tempfile.mkdtemp()
        try:
            storage = MagicMock()
            storage.integrity_check.return_value = ["e1", "e2", "e3", "e4", "e5"]
            sc = StartupCheck(storage=storage, data_dir=tmpdir)
            ok, msg = sc._check_sqlite_integrity()
            assert ok is False
            parts = msg.split(": ")
            assert len(parts[1].split(", ")) <= 3
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_no_storage(self):
        """storage 为 None 时返回失败。"""
        sc = StartupCheck(storage=None)
        ok, msg = sc._check_sqlite_integrity()
        assert ok is False
        assert "未初始化" in msg


# ── StartupCheck: _check_index_ready ──────────────────────────────────────

class TestStartupCheckIndex:
    """索引就绪检查。"""

    def test_index_ready(self):
        """索引就绪且包含规则。"""
        tmpdir = tempfile.mkdtemp()
        try:
            index = MagicMock()
            index.is_ready = True
            index._rules = [1, 2, 3]
            sc = StartupCheck(index=index, data_dir=tmpdir)
            ok, msg = sc._check_index_ready()
            assert ok is True
            assert "3" in msg
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_index_not_ready_cold_start(self):
        """冷启动：存储和索引均为空，视为正常。"""
        tmpdir = tempfile.mkdtemp()
        try:
            index = MagicMock()
            index.is_ready = False
            sc = StartupCheck(index=index, data_dir=tmpdir)
            ok, msg = sc._check_index_ready()
            assert ok is True
            assert "冷启动" in msg
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_index_not_ready_with_storage(self):
        """存储有规则但索引未就绪 → 构建失败，阻断启动。"""
        tmpdir = tempfile.mkdtemp()
        try:
            storage = MagicMock()
            storage.list.return_value = [{"id": "test/001"}]
            index = MagicMock()
            index.is_ready = False
            sc = StartupCheck(storage=storage, index=index, data_dir=tmpdir)
            ok, msg = sc._check_index_ready()
            assert ok is False
            assert "未就绪" in msg
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_index_empty_rules(self):
        """索引就绪但无规则也算成功。"""
        tmpdir = tempfile.mkdtemp()
        try:
            index = MagicMock()
            index.is_ready = True
            index._rules = []
            sc = StartupCheck(index=index, data_dir=tmpdir)
            ok, msg = sc._check_index_ready()
            assert ok is True
            assert "0" in msg
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_no_index(self):
        """index 为 None 时返回失败。"""
        sc = StartupCheck(index=None)
        ok, msg = sc._check_index_ready()
        assert ok is False
        assert "未初始化" in msg


# ── StartupCheck: run_all / run_critical / add_check ──────────────────────

class TestStartupCheckSuite:
    """自检套件运行。"""

    def test_run_all_report_format(self):
        """run_all() 返回完整报告格式。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(data_dir=tmpdir)
            report = sc.run_all()
            assert "timestamp" in report
            assert "all_ok" in report
            assert "can_start" in report
            assert "critical_failure" in report
            assert "checks" in report
            assert isinstance(report["checks"], list)
            assert "summary" in report
            assert "total" in report["summary"]
            assert "passed" in report["summary"]
            assert "failed" in report["summary"]
            assert "critical_failed" in report["summary"]
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_run_all_counts(self):
        """summary 统计数正确。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(data_dir=tmpdir)
            report = sc.run_all()
            assert report["summary"]["total"] == len(report["checks"])
            assert report["summary"]["passed"] + report["summary"]["failed"] == report["summary"]["total"]
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_run_critical_returns_only_critical(self):
        """run_critical() 只返回 critical=True 的检查。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(data_dir=tmpdir)
            report = sc.run_critical()
            assert "checks" in report
            for c in report["checks"]:
                assert c["critical"] is True
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_run_critical_report_format(self):
        """run_critical() 报告包含必要字段。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(data_dir=tmpdir)
            report = sc.run_critical()
            assert "timestamp" in report
            assert "all_ok" in report
            assert "can_start" in report
            assert "checks" in report
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_add_check(self):
        """add_check() 注册自定义检查。"""
        sc = StartupCheck()
        sc.add_check("custom_check", "My custom check",
                     lambda: (True, "ok"), critical=False)
        found = any(c.name == "custom_check" for c in sc.checks)
        assert found is True

    def test_add_check_executed_in_run_all(self):
        """自定义检查在 run_all 中执行。"""
        sc = StartupCheck()
        marker = {"called": False}

        def custom():
            marker["called"] = True
            return True, "custom ok"

        sc.add_check("custom", "Custom", custom, critical=False)
        sc.run_all()
        assert marker["called"] is True

    def test_add_check_failure_in_summary(self):
        """自定义检查失败计入 summary。"""
        sc = StartupCheck()
        sc.add_check("failing", "Failing", lambda: (False, "fail"), critical=False)
        report = sc.run_all()
        assert report["all_ok"] is False
        assert report["summary"]["failed"] >= 1
        assert any(c["name"] == "failing" for c in report["checks"])


class TestStartupCheckEdgeCases:
    """边界情况。"""

    def test_all_passing_then_can_start(self):
        """全部检查通过时 can_start=True。"""
        tmpdir = tempfile.mkdtemp()
        try:
            sc = StartupCheck(
                config={"v3": {"enabled": True, "storage": "sqlite"}},
                data_dir=tmpdir,
            )
            report = sc.run_all()
            assert report["all_ok"] is True
            assert report["can_start"] is True
            assert report["critical_failure"] is False
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_empty_startup_check_no_crash(self):
        """最小化构造（全 None）不崩溃。"""
        sc = StartupCheck(storage=None, index=None, config={})
        report = sc.run_all()
        assert isinstance(report, dict)
        assert "checks" in report

    def test_config_valid_none(self):
        """config 为 None 时视为空字典。"""
        sc = StartupCheck(config=None)
        ok, msg = sc._check_config()
        assert ok is True

    def test_check_registration_count(self):
        """使用 storage+index+config 时注册的检查项数量正确。"""
        tmpdir = tempfile.mkdtemp()
        try:
            storage = MagicMock()
            storage.integrity_check.return_value = []
            index = MagicMock()
            index.is_ready = True
            index._rules = [1]

            sc = StartupCheck(storage=storage, index=index,
                              config={"key": "val"}, data_dir=tmpdir)
            # 应注册: sqlite_integrity, index_ready, data_dir, config_valid, disk_space
            names = [c.name for c in sc.checks]
            assert "sqlite_integrity" in names
            assert "index_ready" in names
            assert "data_dir" in names
            assert "config_valid" in names
            assert "disk_space" in names
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_index_ready_is_critical(self):
        """index_ready 在存储有规则但索引未加载时阻断启动。"""
        tmpdir = tempfile.mkdtemp()
        try:
            storage = MagicMock()
            storage.list.return_value = [{"id": "test/001"}]
            index = MagicMock()
            index.is_ready = False
            sc = StartupCheck(storage=storage, index=index, data_dir=tmpdir)
            report = sc.run_all()
            assert report["critical_failure"] is True
            assert report["can_start"] is False
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_sqlite_integrity_is_critical(self):
        """sqlite_integrity 是关键检查。"""
        tmpdir = tempfile.mkdtemp()
        try:
            storage = MagicMock()
            storage.integrity_check.return_value = ["corruption"]
            sc = StartupCheck(storage=storage, data_dir=tmpdir)
            report = sc.run_all()
            assert report["critical_failure"] is True
            assert report["can_start"] is False
        finally:
            import shutil
            shutil.rmtree(tmpdir)
