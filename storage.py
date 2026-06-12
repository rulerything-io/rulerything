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
JSONL 存储层 — 规则的持久化、CRUD、去重管理
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rule import Rule


class RuleStorage:
    """基于 JSON Lines 的规则存储。

    特性：
    - 按分类分文件存储
    - content_hash 去重保护
    - duplicate_of 重定向
    - 软删除（expires_at）
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._rules: Dict[str, Rule] = {}
        self._load_all()

    # ── 内部方法 ──────────────────────────────────────

    def _category_path(self, category: str) -> Path:
        return self.data_dir / f"{category}.jsonl"

    def _load_all(self):
        """从所有分类文件加载规则到内存。"""
        self._rules.clear()
        for fpath in sorted(self.data_dir.glob("*.jsonl")):
            if fpath.name == "feedback.jsonl":
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        rule = Rule.from_dict(data)
                        self._rules[rule.id] = rule
                    except (json.JSONDecodeError, KeyError) as e:
                        print(f"[warn] 跳过损坏规则: {fpath.name} — {e}")

    def _save_category(self, category: str):
        """将指定分类的所有规则写回文件。"""
        fpath = self._category_path(category)
        with open(fpath, "w", encoding="utf-8") as f:
            for rule in self._rules.values():
                if rule.category == category:
                    f.write(json.dumps(rule.to_dict(), ensure_ascii=False) + "\n")

    # ── CRUD ──────────────────────────────────────────

    def add(self, rule: Rule) -> Tuple[bool, str]:
        """添加规则（含去重检测）。

        Returns:
            (True, "ok") 或 (False, "原因")
        """
        if rule.id in self._rules:
            return False, f"规则 {rule.id} 已存在"

        # 同分类下 content_hash 去重
        for existing in self._rules.values():
            if (existing.category == rule.category
                    and existing.content_hash == rule.content_hash
                    and not existing.is_duplicate):
                return (False,
                        f"内容重复: 与 {existing.id}「{existing.title}」内容相同")

        self._rules[rule.id] = rule
        self._save_category(rule.category)
        return True, "ok"

    def get(self, rule_id: str) -> Optional[Rule]:
        """获取规则（自动跟随 duplicate_of 重定向）。"""
        rule = self._rules.get(rule_id)
        if rule is None:
            return None
        if rule.duplicate_of:
            return self._rules.get(rule.duplicate_of)
        return rule

    def update(self, rule_id: str, **kwargs) -> bool:
        """更新规则字段。"""
        rule = self._rules.get(rule_id)
        if rule is None:
            return False
        for key, val in kwargs.items():
            if hasattr(rule, key):
                setattr(rule, key, val)
        self._save_category(rule.category)
        return True

    def delete(self, rule_id: str) -> bool:
        """软删除：设置 expires_at = now。"""
        rule = self._rules.get(rule_id)
        if rule is None:
            return False
        rule.expires_at = datetime.now()
        self._save_category(rule.category)
        return True

    def hard_delete(self, rule_id: str) -> bool:
        """物理删除（仅用于测试/清理）。"""
        rule = self._rules.pop(rule_id, None)
        if rule is None:
            return False
        self._save_category(rule.category)
        return True

    def list(self, category: Optional[str] = None) -> List[Rule]:
        """列出有效规则，可选按分类过滤。"""
        result = []
        for rule in self._rules.values():
            if rule.is_expired or rule.is_duplicate:
                continue
            if category and rule.category != category:
                continue
            result.append(rule)
        return result

    # ── 去重 ──────────────────────────────────────────

    def find_duplicates(self) -> Dict[str, List[Rule]]:
        """查找所有重复规则组（按 category + content_hash）。"""
        groups: Dict[str, List[Rule]] = {}
        for rule in self._rules.values():
            if rule.is_expired or rule.is_duplicate:
                continue
            key = f"{rule.category}:::{rule.content_hash}"
            groups.setdefault(key, []).append(rule)
        return {k: g for k, g in groups.items() if len(g) > 1}

    def dedup_dry_run(self) -> List[dict]:
        """预览去重结果。"""
        previews = []
        for h, group in self.find_duplicates().items():
            sorted_group = sorted(group, key=lambda r: r.confidence, reverse=True)
            master = sorted_group[0]
            previews.append({
                "content_hash": h,
                "master_id": master.id,
                "master_title": master.title,
                "master_confidence": master.confidence,
                "duplicates": [
                    {"id": r.id, "title": r.title, "confidence": r.confidence}
                    for r in sorted_group[1:]
                ],
            })
        return previews

    def dedup_apply(self) -> List[dict]:
        """执行去重：重复规则设置 duplicate_of 指向主规则。"""
        results = []
        for h, group in self.find_duplicates().items():
            sorted_group = sorted(group, key=lambda r: r.confidence, reverse=True)
            master = sorted_group[0]
            for dup in sorted_group[1:]:
                dup.duplicate_of = master.id
                dup.evolve(f"标记为 {master.id} 的重复")
                self._save_category(dup.category)
                results.append({
                    "rule_id": dup.id,
                    "duplicate_of": master.id,
                })
        return results

    # ── 统计 ──────────────────────────────────────────

    def stats(self) -> dict:
        """存储统计信息。"""
        active = self.list()
        return {
            "total_rules": len(self._rules),
            "active_rules": len(active),
            "categories": sorted({r.category for r in active}),
            "duplicate_groups": len(self.find_duplicates()),
        }
