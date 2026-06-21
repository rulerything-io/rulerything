#!/usr/bin/env python3
"""Query the local Rulerything rule knowledge base for Codex skills."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


DEFAULT_HOME = Path(__file__).resolve().parent.parent


def _resolve_home() -> Path:
    configured = os.environ.get("RULERYTHING_HOME")
    return Path(configured).expanduser() if configured else DEFAULT_HOME


def _load_index(home: Path):
    if not home.exists():
        raise RuntimeError(
            f"Rulerything project not found at {home}. "
            "Set RULERYTHING_HOME to the project directory."
        )

    sys.path.insert(0, str(home))
    from storage import RuleStorage  # noqa: PLC0415
    from index import EverythingStyleIndex  # noqa: PLC0415

    storage = RuleStorage(str(home / "data"))
    return EverythingStyleIndex(storage.list())


def query_rules(index, query: str, search_type: str, category: str | None, limit: int) -> list:
    category_filter = None if category in (None, "", "all") else category

    if search_type == "smart":
        return index.smart_search(query, category=category_filter, limit=limit)

    return index.search(query, search_type, category=category_filter, limit=limit)


def _rule_to_dict(rule) -> dict:
    return {
        "id": rule.id,
        "title": rule.title,
        "category": rule.category,
        "tags": rule.tags,
        "confidence": rule.confidence,
        "content": rule.content,
    }


def _print_text(rules: list, max_content_chars: int) -> None:
    if not rules:
        print("No matching Rulerything rules found.")
        return

    for i, rule in enumerate(rules, 1):
        content = (rule.content or "").replace("\r\n", "\n").strip()
        if len(content) > max_content_chars:
            content = content[:max_content_chars].rstrip() + "..."
        print(f"{i}. [{rule.id}] {rule.title}")
        print(f"   category={rule.category} confidence={rule.confidence}")
        if rule.tags:
            print(f"   tags={', '.join(map(str, rule.tags))}")
        print(f"   content={content.replace(chr(10), ' ')}")
        print("---")


def main() -> int:
    parser = argparse.ArgumentParser(description="Query local Rulerything rules.")
    parser.add_argument("query", nargs="+", help="Search query")
    parser.add_argument(
        "--type",
        choices=("smart", "exact", "prefix", "tag"),
        default="smart",
        help="Search strategy. smart merges exact, tag, and prefix.",
    )
    parser.add_argument("--category", default="all", help="Category filter or all")
    parser.add_argument("--limit", type=int, default=8, help="Maximum rules to return")
    parser.add_argument("--max-content-chars", type=int, default=900)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    query = " ".join(args.query).strip()
    if not query:
        parser.error("query cannot be empty")

    home = _resolve_home()
    index = _load_index(home)
    rules = query_rules(index, query, args.type, args.category, max(1, args.limit))

    if args.format == "json":
        print(json.dumps([_rule_to_dict(rule) for rule in rules], ensure_ascii=False, indent=2))
    else:
        _print_text(rules, max(80, args.max_content_chars))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
