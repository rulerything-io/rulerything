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
v3.0 数据迁移工具 — JSONL → SQLite

用法:
    python migrate_v3.py                    # 迁移并校验
    python migrate_v3.py --dry-run           # 仅预览，不写 SQLite
    python migrate_v3.py --rollback          # 回滚（SQLite → JSONL 备份）
    python migrate_v3.py --check             # 仅校验一致性

Everything 原则：
  - 向后兼容：JSONL 文件不做任何修改，保留完整备份
  - 可回滚：迁移前自动备份，--rollback 可恢复
  - 幂等：重复执行安全，不会重复迁移
"""

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# 确保能找到项目模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rule import Rule
from storage_v2 import RuleStorageV2


def _find_jsonl_files(data_dir: Path) -> List[Path]:
    """查找所有 JSONL 规则文件（排除 feedback.jsonl）。"""
    return sorted([
        f for f in data_dir.glob("*.jsonl")
        if f.name != "feedback.jsonl"
    ])


def count_jsonl_rules(data_dir: Path) -> int:
    """统计 JSONL 中的规则总数。"""
    count = 0
    for fpath in _find_jsonl_files(data_dir):
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    count += 1
    return count


def load_jsonl_rules(data_dir: Path) -> List[Rule]:
    """从 JSONL 加载所有规则。"""
    rules = []
    errors = []
    for fpath in _find_jsonl_files(data_dir):
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    rule = Rule.from_dict(data)
                    rules.append(rule)
                except (json.JSONDecodeError, KeyError) as e:
                    errors.append({"file": fpath.name, "error": str(e)})
    return rules, errors


def migrate(data_dir: str, dry_run: bool = False) -> dict:
    """执行 JSONL → SQLite 迁移。

    Args:
        data_dir: data 目录路径
        dry_run: True=仅预览，False=执行

    Returns:
        迁移结果统计
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        return {"success": False, "error": f"目录不存在: {data_dir}"}

    # 1. 加载 JSONL 规则
    rules, errors = load_jsonl_rules(data_path)
    jsonl_count = count_jsonl_rules(data_path)

    # 2. 统计 SQLite 已有内容
    storage = RuleStorageV2(data_dir)
    existing_count = len(storage.list())

    result = {
        "dry_run": dry_run,
        "jsonl_rules": jsonl_count,
        "jsonl_files": [f.name for f in _find_jsonl_files(data_path)],
        "sqlite_existing": existing_count,
        "load_errors": errors,
        "migrated": 0,
        "skipped": 0,
    }

    if dry_run:
        to_migrate = max(0, jsonl_count - existing_count)
        result["would_migrate"] = to_migrate
        return result

    # 3. 备份 JSONL（首次迁移时）
    backup_dir = data_path / ".jsonl_backup"
    if existing_count == 0 and jsonl_count > 0:
        backup_dir.mkdir(exist_ok=True)
        for fpath in _find_jsonl_files(data_path):
            shutil.copy2(fpath, backup_dir / fpath.name)
        result["backup_path"] = str(backup_dir)

    # 4. 写入 SQLite
    for rule in rules:
        # 检查是否已存在
        existing = storage.get(rule.id)
        if existing:
            result["skipped"] += 1
            continue

        ok, msg = storage.add(rule)
        if ok:
            result["migrated"] += 1
        elif "内容重复" in msg:
            result["skipped"] += 1
            result.setdefault("skipped_details", []).append(
                {"id": rule.id, "reason": msg}
            )
        else:
            result.setdefault("add_errors", []).append(
                {"id": rule.id, "error": msg}
            )

    # 5. 校验（考虑内容去重导致的差异）
    final_count = len(storage.list())
    expected = max(existing_count, jsonl_count)
    result["sqlite_final"] = final_count
    result["skipped_count"] = result.get("skipped", 0) + len(result.get("add_errors", []))
    result["consistent"] = (final_count + result["skipped"] == expected) or (final_count == expected)

    return result


def check_integrity(data_dir: str) -> dict:
    """校验 JSONL 和 SQLite 一致性。"""
    data_path = Path(data_dir)
    storage = RuleStorageV2(data_dir)

    jsonl_rules, errors = load_jsonl_rules(data_path)
    sqlite_rules = storage.list()

    jsonl_ids = {r.id for r in jsonl_rules}
    sqlite_ids = {r.id for r in sqlite_rules}

    only_in_jsonl = jsonl_ids - sqlite_ids
    only_in_sqlite = sqlite_ids - jsonl_ids

    return {
        "jsonl_count": len(jsonl_rules),
        "sqlite_count": len(sqlite_rules),
        "only_in_jsonl": sorted(only_in_jsonl)[:20],
        "only_in_sqlite": sorted(only_in_sqlite)[:20],
        "load_errors": errors,
        "consistent": len(only_in_jsonl) == 0 and len(only_in_sqlite) == 0,
    }


def rollback(data_dir: str) -> dict:
    """回滚：从 .jsonl_backup 恢复 JSONL，清空 SQLite。"""
    data_path = Path(data_dir)
    backup_dir = data_path / ".jsonl_backup"

    if not backup_dir.exists():
        return {"success": False, "error": "未找到备份目录 .jsonl_backup"}

    # 恢复 JSONL
    restored = 0
    for fpath in sorted(backup_dir.glob("*.jsonl")):
        shutil.copy2(fpath, data_path / fpath.name)
        restored += 1

    # 清空 SQLite
    storage = RuleStorageV2(data_dir)
    with storage._lock:
        from storage_v2 import _connect
        # storage_v2 uses sqlite3 internally
        import sqlite3
        conn = sqlite3.connect(str(storage.db_path))
        conn.execute("DROP TABLE IF EXISTS rules")
        conn.execute("DROP TABLE IF EXISTS rules_cold")
        conn.execute("DROP TABLE IF EXISTS rule_relations")
        conn.execute("DROP TABLE IF EXISTS query_log")
        conn.execute("DROP TABLE IF EXISTS metrics_log")
        conn.execute("DROP TABLE IF EXISTS config_runtime")
        conn.commit()
        conn.close()

    # 重建表
    storage._init_db()

    return {
        "success": True,
        "restored_files": restored,
        "note": "JSONL 已恢复，SQLite 已清空",
    }


if __name__ == "__main__":
    import sys
    # 确保 stdout 能用 utf-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    data_dir = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "data"

    if "--dry-run" in sys.argv:
        result = migrate(data_dir, dry_run=True)
        print(f"[dry-run] JSONL: {result['jsonl_rules']} 条规则, "
              f"SQLite 已有: {result['sqlite_existing']} 条, "
              f"需迁移: {result.get('would_migrate', 0)} 条")
        if result.get("load_errors"):
            print(f"[warn] {len(result['load_errors'])} 条加载错误")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif "--check" in sys.argv:
        result = check_integrity(data_dir)
        status = "✅ 一致" if result["consistent"] else "❌ 不一致"
        print(f"JSONL: {result['jsonl_count']} 条, SQLite: {result['sqlite_count']} 条 → {status}")
        if not result["consistent"]:
            if result["only_in_jsonl"]:
                print(f"  仅在 JSONL 中: {result['only_in_jsonl'][:10]}")
            if result["only_in_sqlite"]:
                print(f"  仅在 SQLite 中: {result['only_in_sqlite'][:10]}")

    elif "--rollback" in sys.argv:
        result = rollback(data_dir)
        if result["success"]:
            print(f"✅ 回滚完成，已恢复 {result['restored_files']} 个 JSONL 文件")
        else:
            print(f"❌ 回滚失败: {result.get('error', '未知错误')}")

    else:
        result = migrate(data_dir)
        status = "✅" if result.get("consistent") else "⚠️"
        content_dupes = len(result.get("skipped_details", []))
        print(f"迁移完成: JSONL {result['jsonl_rules']} 条 → "
              f"SQLite {result['sqlite_final']} 条 "
              f"(新增 {result['migrated']}, 内容重复跳过 {content_dupes}) {status}")
        if result.get("load_errors"):
            print(f"[warn] {len(result['load_errors'])} 条加载错误:")
            for e in result["load_errors"]:
                print(f"  - {e['file']}: {e['error']}")
        if content_dupes:
            print(f"[info] 内容重复的规则: {[d['id'] for d in result.get('skipped_details', [])]}")
        if result.get("backup_path"):
            print(f"JSONL 已备份到: {result['backup_path']}")
