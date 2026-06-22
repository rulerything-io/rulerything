# Copyright 2026 rulerything-io
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

# Copyright 2026 rulerything-io
"""
Health — 系统健康检查与启动自检（v3.0 Phase C）

启动自检:
  - 数据库完整性 (PRAGMA integrity_check)
  - 索引一致性
  - 配置有效性
  - 磁盘空间检查

用法:
    check = StartupCheck(storage_v2, index, config)
    report = check.run_all()
"""

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


class HealthCheck:
    """单项健康检查。"""

    def __init__(self, name: str, description: str,
                 check_fn: Callable[[], Tuple[bool, str]],
                 critical: bool = False):
        self.name = name
        self.description = description
        self.check_fn = check_fn
        self.critical = critical

    def run(self) -> dict:
        start = time.perf_counter()
        try:
            ok, message = self.check_fn()
            return {
                "name": self.name,
                "description": self.description,
                "status": "ok" if ok else "failed",
                "message": message,
                "critical": self.critical,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            }
        except Exception as e:
            return {
                "name": self.name,
                "description": self.description,
                "status": "error",
                "message": str(e),
                "critical": self.critical,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            }


class StartupCheck:
    """启动自检套件。

    注册一系列检查项，全部运行后输出报告。
    """

    def __init__(self, storage=None, index=None, config: Optional[dict] = None,
                 data_dir: str = "data"):
        self.storage = storage
        self.index = index
        self.config = config or {}
        self.data_dir = Path(data_dir)
        self.checks: List[HealthCheck] = []
        self._register_default_checks()

    def _register_default_checks(self):
        """注册默认检查项。"""
        # 1. 数据库完整性
        if self.storage and hasattr(self.storage, 'integrity_check'):
            self.add_check("sqlite_integrity", "SQLite 数据库完整性检查",
                           self._check_sqlite_integrity, critical=True)

        # 2. 索引存在性
        if self.index:
            self.add_check("index_ready", "内存索引就绪状态",
                           self._check_index_ready, critical=True)

        # 3. 数据目录
        self.add_check("data_dir", "数据目录可读写",
                       self._check_data_dir, critical=True)

        # 4. 配置有效性
        if self.config:
            self.add_check("config_valid", "配置有效性检查",
                           self._check_config, critical=False)

        # 5. 磁盘空间
        self.add_check("disk_space", "磁盘空间检查（至少 100MB 可用）",
                       self._check_disk_space, critical=False)

    def add_check(self, name: str, description: str,
                  check_fn: Callable[[], Tuple[bool, str]],
                  critical: bool = False):
        """注册自定义检查项。"""
        self.checks.append(HealthCheck(name, description, check_fn, critical))

    def run_all(self) -> dict:
        """运行所有检查项。"""
        results = []
        all_ok = True
        has_critical_failure = False

        for check in self.checks:
            result = check.run()
            results.append(result)
            if result["status"] != "ok":
                all_ok = False
                if check.critical:
                    has_critical_failure = True

        return {
            "timestamp": datetime.now().isoformat(),
            "all_ok": all_ok,
            "can_start": not has_critical_failure,
            "critical_failure": has_critical_failure,
            "checks": results,
            "summary": {
                "total": len(results),
                "passed": sum(1 for r in results if r["status"] == "ok"),
                "failed": sum(1 for r in results if r["status"] != "ok"),
                "critical_failed": sum(
                    1 for r in results if r["status"] != "ok" and r["critical"]
                ),
            },
        }

    def run_critical(self) -> dict:
        """仅运行关键检查项。"""
        results = []
        all_ok = True

        for check in self.checks:
            if not check.critical:
                continue
            result = check.run()
            results.append(result)
            if result["status"] != "ok":
                all_ok = False

        return {
            "timestamp": datetime.now().isoformat(),
            "all_ok": all_ok,
            "can_start": all_ok,
            "checks": results,
        }

    # ── 默认检查实现 ────────────────────────────────

    def _check_sqlite_integrity(self) -> Tuple[bool, str]:
        """SQLite 完整性检查。"""
        if not self.storage:
            return False, "存储层未初始化"
        errors = self.storage.integrity_check()
        if errors:
            return False, f"完整性检查失败: {errors[:3]}"
        return True, "ok"

    def _check_index_ready(self) -> Tuple[bool, str]:
        """索引就绪检查。

        冷启动时索引和存储均为空属于正常状态，不阻断启动。
        仅当存储中有规则但索引未加载时才视为构建失败。
        """
        if not self.index:
            return False, "索引未初始化"
        ready = getattr(self.index, 'is_ready', False)
        if ready:
            rules_count = len(getattr(self.index, '_rules', []))
            return True, f"就绪, {rules_count} 条规则"

        # 索引为空：检查存储中是否有规则，区分冷启动和构建失败
        storage_has_rules = False
        if self.storage is not None:
            try:
                storage_has_rules = bool(self.storage.list())
            except Exception:
                pass
        if storage_has_rules:
            return False, "索引未就绪（存储有规则但未加载到索引）"
        return True, "索引为空（冷启动正常状态，管理循环首次 tick 后将重建）"

    def _check_data_dir(self) -> Tuple[bool, str]:
        """数据目录检查。"""
        if not self.data_dir.exists():
            return False, f"目录不存在: {self.data_dir}"
        test_file = self.data_dir / ".health_check_tmp"
        try:
            test_file.write_text("ok")
            test_file.unlink()
            return True, str(self.data_dir)
        except (OSError, PermissionError) as e:
            return False, f"不可读写: {e}"

    def _check_config(self) -> Tuple[bool, str]:
        """配置有效性检查。"""
        issues = []
        v3cfg = self.config.get("v3", {})
        # 检查关键配置
        if v3cfg.get("enabled", False):
            if v3cfg.get("storage") not in ("sqlite", "jsonl"):
                issues.append(f"storage 无效: {v3cfg.get('storage')}")
            ai = v3cfg.get("ai_bridge", {})
            if ai.get("enabled", False) and not ai.get("api_key_env"):
                issues.append("ai_bridge.enabled=true 但未设置 api_key_env")
        if issues:
            return False, "; ".join(issues)
        return True, "配置有效"

    def _check_disk_space(self) -> Tuple[bool, str]:
        """磁盘空间检查（Windows/Linux 兼容）。"""
        try:
            import shutil
            usage = shutil.disk_usage(str(self.data_dir))
            free_mb = usage.free / (1024 * 1024)
            if free_mb < 100:
                return False, f"磁盘空间不足: {free_mb:.0f}MB 可用"
            total_gb = usage.total / (1024 ** 3)
            return True, f"{free_mb:.0f}MB 可用 / {total_gb:.0f}GB 总容量"
        except Exception as e:
            return False, f"磁盘检查错误: {e}"
