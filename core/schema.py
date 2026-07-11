"""
Rulerything — 规则 Schema v1 定义与校验

用法:
    from core.schema import RuleValidator, RuleSchemaV1

    validator = RuleValidator()
    errors = validator.validate(rule_dict)
    report = validator.validate_all(rules)

CLI:
    rulerything rules validate --strict
    rulerything rules migrate --from 0 --to 1 --dry-run
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Pydantic 是核心依赖（FastAPI 需要），这里直接使用
try:
    from pydantic import BaseModel, Field, field_validator, model_validator
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False


# ── 注册的分类列表（与 data/ 目录一致）───────────────────

REGISTERED_CATEGORIES = {
    "ai", "api", "blockchain", "cpp", "css", "database", "devops",
    "docker", "dotnet", "embedded", "erlang", "general", "git", "go",
    "java", "javascript", "lua", "mobile", "nodejs", "pattern",
    "performance", "philosophy", "php", "process", "python", "react",
    "ruby", "rust", "security", "shell", "takealot-erp", "typescript",
    "vue", "zig",
}


# ── Schema v1 Pydantic Model ──────────────────────────────

if HAS_PYDANTIC:

    class RuleSchemaV1(BaseModel):
        """Rule Schema v1 — 严格的规则数据结构。"""
        schema_version: int = Field(default=1, ge=1, le=1)
        id: str = Field(pattern=r"^[a-zA-Z][\w./-]{1,64}$")
        title: str = Field(min_length=1, max_length=200)
        category: str = Field(min_length=1)
        content: str = Field(min_length=1)
        tags: List[str] = Field(default_factory=list, max_length=20)

        confidence: float = Field(default=0.5, ge=0.0, le=1.0)
        status: str = Field(default="active", pattern=r"^(active|deprecated|draft|stable)$")
        lang: str = Field(default="zh", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")

        scope_languages: List[str] = Field(default_factory=list, alias="scope.languages")
        scope_frameworks: List[str] = Field(default_factory=list, alias="scope.frameworks")
        scope_environments: List[str] = Field(default_factory=list, alias="scope.environments")

        source_type: str = Field(default="manual", alias="source.type")
        source_reference: str = Field(default="", alias="source.reference")

        related_rules: List[str] = Field(default_factory=list)
        conflicts_with: List[str] = Field(default_factory=list)

        created_at: Optional[str] = None
        updated_at: Optional[str] = None

        @field_validator("category")
        @classmethod
        def validate_category(cls, v: str) -> str:
            if v not in REGISTERED_CATEGORIES:
                raise ValueError(
                    f"Unknown category '{v}'. "
                    f"Registered: {', '.join(sorted(REGISTERED_CATEGORIES))}"
                )
            return v

        @field_validator("tags")
        @classmethod
        def validate_tags(cls, v: List[str]) -> List[str]:
            normalized = []
            for tag in v:
                tag_str = tag.strip().lower().replace(" ", "-")
                if tag_str:
                    normalized.append(tag_str)
            return normalized

        @field_validator("confidence")
        @classmethod
        def validate_confidence(cls, v: float) -> float:
            return max(0.0, min(1.0, v))

        @model_validator(mode="after")
        def validate_conflicts_reference_existing(self):
            """conflicts_with 中引用的规则 ID 格式有效即可（跨文件依赖运行时检查）。"""
            for ref in self.conflicts_with + self.related_rules:
                if not ref:
                    continue
                if not isinstance(ref, str) or len(ref) < 3:
                    raise ValueError(f"Invalid rule reference: '{ref}'")
            return self

else:
    # Pydantic 不可用时的回退
    class RuleSchemaV1:
        def __init__(self, **data):
            self.__data = data


# ── 验证报告 ──────────────────────────────────────────────

class ValidationError:
    """单条校验错误。"""

    def __init__(self, rule_id: str, field: str, message: str,
                 severity: str = "error", line: Optional[int] = None):
        self.rule_id = rule_id
        self.field = field
        self.message = message
        self.severity = severity
        self.line = line

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "field": self.field,
            "message": self.message,
            "severity": self.severity,
            "line": self.line,
        }

    def __repr__(self) -> str:
        return f"[{self.severity.upper()}] {self.rule_id}.{self.field}: {self.message}"


class ValidationReport:
    """批量校验报告。"""

    def __init__(self):
        self.errors: List[ValidationError] = []
        self.total_rules: int = 0
        self.valid_count: int = 0
        self.invalid_count: int = 0

    def add_error(self, error: ValidationError):
        self.errors.append(error)

    @property
    def passed(self) -> bool:
        return len([e for e in self.errors if e.severity == "error"]) == 0

    def summary(self) -> dict:
        by_severity: Dict[str, int] = {}
        by_field: Dict[str, int] = {}
        for e in self.errors:
            by_severity[e.severity] = by_severity.get(e.severity, 0) + 1
            by_field[e.field] = by_field.get(e.field, 0) + 1

        return {
            "total_rules": self.total_rules,
            "valid": self.valid_count,
            "invalid": self.invalid_count,
            "total_errors": len(self.errors),
            "errors_by_severity": by_severity,
            "errors_by_field": by_field,
        }

    def print_report(self, verbose: bool = False):
        """打印人类可读的校验报告。"""
        s = self.summary()
        print(f"\n{'=' * 50}")
        print(f"  Schema Validation Report")
        print(f"{'=' * 50}")
        print(f"  Total rules: {s['total_rules']}")
        print(f"  Valid:       {s['valid']}")
        print(f"  Invalid:     {s['invalid']}")
        print(f"  Errors:      {s['total_errors']}")

        if s.get("errors_by_severity"):
            print(f"\n  By severity:")
            for sev, count in sorted(s["errors_by_severity"].items()):
                print(f"    {sev}: {count}")

        if verbose and self.errors:
            print(f"\n  Details:")
            for err in self.errors[:50]:
                line_info = f" (line {err.line})" if err.line else ""
                print(f"    [{err.severity.upper()}] {err.rule_id}.{err.field}{line_info}")
                print(f"      {err.message}")
            if len(self.errors) > 50:
                print(f"    ... and {len(self.errors) - 50} more errors")
        print(f"{'=' * 50}")


# ── 校验器 ────────────────────────────────────────────────

class RuleValidator:
    """校验规则数据是否符合 Schema v1。"""

    def __init__(self, strict: bool = False):
        self.strict = strict

    def validate_one(self, rule: dict) -> List[ValidationError]:
        """校验单条规则字典。"""
        errors: List[ValidationError] = []
        rule_id = rule.get("id", "<unknown>")

        if not HAS_PYDANTIC:
            # 无 Pydantic 时的基本校验
            if not rule.get("id"):
                errors.append(ValidationError(rule_id, "id", "ID is required"))
            if not rule.get("title"):
                errors.append(ValidationError(rule_id, "title", "Title is required"))
            if not rule.get("content"):
                errors.append(ValidationError(rule_id, "content", "Content is required"))
            cat = rule.get("category", "")
            if cat and cat not in REGISTERED_CATEGORIES:
                errors.append(ValidationError(rule_id, "category",
                                              f"Unknown category '{cat}'"))
            conf = rule.get("confidence", 0.5)
            if not (0 <= conf <= 1):
                errors.append(ValidationError(rule_id, "confidence",
                                              "Must be between 0 and 1"))
            return errors

        try:
            RuleSchemaV1(**rule)
        except Exception as e:
            # Pydantic 验证错误解析
            err_msg = str(e)
            errors.append(ValidationError(rule_id, "schema", err_msg[:200]))
            # 提取字段名
            for line in err_msg.split("\n"):
                for field in ["id", "title", "content", "category",
                              "confidence", "tags", "status", "lang"]:
                    if f"'{field}'" in line or f"  {field} " in line:
                        errors.append(ValidationError(rule_id, field, line.strip()[:150]))

        return errors

    def validate_all(self, rules: List[dict],
                     file_path: Optional[str] = None) -> ValidationReport:
        """校验规则列表并生成报告。"""
        report = ValidationReport()
        report.total_rules = len(rules)

        for rule in rules:
            rule_errors = self.validate_one(rule)
            if rule_errors:
                report.invalid_count += 1
                for err in rule_errors:
                    if file_path:
                        err.line = 0  # 简化：行号信息在此级别不可用
                    # 严重性按配置处理
                    if self.strict:
                        err.severity = "error"
                    report.add_error(err)
            else:
                report.valid_count += 1

        return report


# ── 数据一致性检查 ────────────────────────────────────────

def check_id_uniqueness(rules: List[dict]) -> List[ValidationError]:
    """检查规则 ID 全局唯一性。"""
    seen: Dict[str, int] = {}
    errors: List[ValidationError] = []
    for rule in rules:
        rid = rule.get("id", "<unknown>")
        seen[rid] = seen.get(rid, 0) + 1
    for rid, count in seen.items():
        if count > 1:
            errors.append(ValidationError(rid, "id",
                                          f"Duplicate ID '{rid}' appears {count} times"))
    return errors


def check_disjoint_ids(rules_by_file: Dict[str, List[dict]]) -> List[ValidationError]:
    """跨文件检查 ID 唯一性。"""
    global_seen: Dict[str, str] = {}  # id → file
    errors: List[ValidationError] = []
    for file_path, rules in rules_by_file.items():
        for rule in rules:
            rid = rule.get("id", "<unknown>")
            if rid in global_seen:
                errors.append(ValidationError(
                    rid, "id",
                    f"ID '{rid}' conflicts with {global_seen[rid]} (also in {file_path})"
                ))
            else:
                global_seen[rid] = file_path
    return errors


def check_conflicts_reference_existing(rules: List[dict]) -> List[ValidationError]:
    """检查 conflicts_with/related_rules 引用的 ID 是否存在。"""
    existing_ids = {r.get("id") for r in rules if r.get("id")}
    errors: List[ValidationError] = []
    for rule in rules:
        rid = rule.get("id", "<unknown>")
        for field in ("conflicts_with", "related_rules"):
            refs = rule.get(field, [])
            if not isinstance(refs, list):
                continue
            for ref in refs:
                if ref and ref not in existing_ids:
                    errors.append(ValidationError(
                        rid, field,
                        f"References non-existent rule '{ref}'"
                    ))
    # 环检测 (简单版本: 检查直接相互冲突)
    for rule in rules:
        rid = rule.get("id", "")
        if not rid:
            continue
        for conflict_id in rule.get("conflicts_with", []):
            if conflict_id == rid:
                errors.append(ValidationError(
                    rid, "conflicts_with",
                    f"Rule conflicts with itself"
                ))
    return errors


# ── 迁移命令支持 ──────────────────────────────────────────

def estimate_schema_version(rule: dict) -> int:
    """根据字段推测规则使用的 Schema 版本。"""
    if rule.get("schema_version"):
        return int(rule["schema_version"])
    # 有 scope 或 source 结构 → v1
    if any(k.startswith("scope.") or k.startswith("source.") for k in rule):
        return 1
    return 0


def migrate_to_v1(rule: dict) -> dict:
    """将旧格式规则迁移到 Schema v1。"""
    v1 = dict(rule)
    v1["schema_version"] = 1

    # 默认 status
    if "status" not in v1:
        v1["status"] = "active"

    # scope 展开
    for prefix in ("scope", "source"):
        for key in list(v1.keys()):
            if key.startswith(f"{prefix}."):
                # 已经在 v1 格式
                pass

    return v1


def migrate_all(rules: List[dict], dry_run: bool = True) -> Tuple[List[dict], dict]:
    """批量迁移规则到 Schema v1。"""
    migrated = []
    stats = {"total": len(rules), "already_v1": 0, "migrated": 0, "skipped": 0}

    for rule in rules:
        sv = estimate_schema_version(rule)
        if sv >= 1:
            stats["already_v1"] += 1
            migrated.append(rule)
        else:
            stats["migrated"] += 1
            migrated.append(migrate_to_v1(rule))

    return (rules if dry_run else migrated, stats)
