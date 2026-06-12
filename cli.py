#!/usr/bin/env python3

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
CLI 命令行工具 — 供人类使用的接入层

用法：
    python cli.py search <query> [--type exact|prefix|tag] [--category CAT]
    python cli.py list [--category CAT]
    python cli.py get <rule_id>
    python cli.py add <json_file>
    python cli.py dedup [--dry-run]
    python cli.py warmup [--category CAT]
    python cli.py health
    python cli.py stats
    python cli.py config            # 查看当前配置
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from config import load_config
from rule import Rule
from storage import RuleStorage
from index import EverythingStyleIndex
from logger import RuleLogger
from evolution import EvolutionEngine


# ANSI 颜色（Windows 兼容）
class Color:
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    GRAY = "\033[90m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def _print(d: dict, indent: int = 0):
    """递归打印字典。"""
    prefix = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            print(f"{prefix}{Color.CYAN}{k}:{Color.RESET}")
            _print(v, indent + 1)
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            print(f"{prefix}{Color.CYAN}{k}:{Color.RESET}")
            for i, item in enumerate(v):
                print(f"{prefix}  [{i}]")
                _print(item, indent + 2)
        elif isinstance(v, list):
            print(f"{prefix}{Color.CYAN}{k}:{Color.RESET} {len(v)} items")
            for item in v:
                print(f"{prefix}  - {item}")
        else:
            print(f"{prefix}{Color.CYAN}{k}:{Color.RESET} {v}")


def cmd_search(args, index):
    """搜索规则。"""
    results = index.search(args.query, args.type, args.category, limit=20)
    if not results:
        print(f"{Color.YELLOW}未找到匹配规则。{Color.RESET}")
        return

    print(f"{Color.GREEN}找到 {len(results)} 条规则:{Color.RESET}\n")
    for i, r in enumerate(results, 1):
        print(f"{Color.BOLD}{i}. [{r.id}]{Color.RESET} {r.title}")
        print(f"   分类: {r.category}  ", end="")
        print(f"置信度: {Color.YELLOW}{r.confidence}{Color.RESET}  ", end="")
        print(f"命中: {r.hit_count}")
        if r.tags:
            print(f"   标签: {', '.join(r.tags)}")
        # 只显示前 200 字符的内容
        content_preview = r.content[:200].replace("\n", " ")
        print(f"   内容: {Color.GRAY}{content_preview}...{Color.RESET}"
              if len(r.content) > 200 else f"   内容: {r.content}")
        print()


def cmd_list(args, storage):
    """列出规则。"""
    rules = storage.list(category=args.category)
    if not rules:
        print(f"{Color.YELLOW}没有规则。{Color.RESET}")
        return

    print(f"{Color.GREEN}共 {len(rules)} 条规则:{Color.RESET}\n")
    # 按分类分组
    by_cat = {}
    for r in rules:
        by_cat.setdefault(r.category, []).append(r)

    for cat, cat_rules in sorted(by_cat.items()):
        print(f"{Color.BOLD}{Color.BLUE}── {cat} ({len(cat_rules)}) ──{Color.RESET}")
        for r in sorted(cat_rules, key=lambda x: x.id):
            bar = _confidence_bar(r.confidence)
            print(f"  {Color.CYAN}{r.id:<20}{Color.RESET} "
                  f"{r.title:<35} {bar} {r.confidence:.2f}  "
                  f"{Color.GRAY}hits:{r.hit_count}{Color.RESET}")
        print()


def _confidence_bar(conf: float, width: int = 10) -> str:
    filled = int(conf * width)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}]"


def cmd_get(args, storage):
    """获取单条规则详情。"""
    r = storage.get(args.rule_id)
    if r is None:
        print(f"{Color.RED}规则 {args.rule_id} 不存在。{Color.RESET}")
        return

    print(f"{Color.BOLD}{r.id}{Color.RESET} — {r.title}")
    print(f"{'=' * 50}")
    print(f"  分类: {r.category}")
    print(f"  标签: {', '.join(r.tags) if r.tags else '(无)'}")
    print(f"  置信度: {_confidence_bar(r.confidence)} {r.confidence}")
    print(f"  版本: v{r.version}")
    print(f"  验证: {r.verifier}")
    print(f"  命中: {r.hit_count} 次")
    print(f"  创建: {r.created_at}")
    if r.parent_id:
        print(f"  父规则: {r.parent_id}")
    if r.duplicate_of:
        print(f"  重复指向: {r.duplicate_of}")
    if r.evolution_log:
        print(f"  进化历史:")
        for log in r.evolution_log:
            print(f"    - {log}")
    print(f"\n{Color.BOLD}内容:{Color.RESET}")
    print(f"{r.content}")


def cmd_add(args, storage, index, logger):
    """添加规则。"""
    with open(args.file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        results = []
        for item in data:
            ok, msg = _add_single(item, storage, index, logger)
            results.append({"id": item.get("id", "?"), "ok": ok, "msg": msg})
        for res in results:
            status = f"{Color.GREEN}✓{Color.RESET}" if res["ok"] else f"{Color.RED}✗{Color.RESET}"
            print(f"  {status} {res['id']}: {res['msg']}")
    else:
        ok, msg = _add_single(data, storage, index, logger)
        status = f"{Color.GREEN}✓{Color.RESET}" if ok else f"{Color.RED}✗{Color.RESET}"
        print(f"  {status} {data.get('id', '?')}: {msg}")


def _add_single(data: dict, storage, index, logger) -> tuple:
    try:
        rule = Rule.from_dict(data)
        ok, msg = storage.add(rule)
        if ok:
            index.add(rule)
            logger.info("cli", f"添加规则 {rule.id}", rule_id=rule.id)
        return ok, msg
    except Exception as e:
        return False, str(e)


def cmd_dedup(args, storage, index, logger):
    """去重操作。"""
    if args.dry_run:
        previews = storage.dedup_dry_run()
        if not previews:
            print(f"{Color.GREEN}没有发现重复规则。{Color.RESET}")
            return
        print(f"{Color.YELLOW}发现 {len(previews)} 组重复:{Color.RESET}\n")
        for p in previews:
            print(f"  主规则: {Color.CYAN}{p['master_id']}{Color.RESET} "
                  f"「{p['master_title']}」({p['master_confidence']})")
            for dup in p["duplicates"]:
                print(f"    → 重复: {dup['id']}「{dup['title']}」({dup['confidence']})")
        print(f"\n运行 {Color.BOLD}dedup --apply{Color.RESET} 执行去重")
    else:
        results = storage.dedup_apply()
        logger.info("cli", f"去重完成: {len(results)} 条")
        print(f"{Color.GREEN}去重完成: {len(results)} 条规则被标记为重复{Color.RESET}")
        for r in results:
            print(f"  {r['rule_id']} → {r['duplicate_of']}")


def cmd_warmup(args, index):
    """预热缓存。"""
    result = index.warmup(category=args.category)
    print(f"{Color.GREEN}预热完成: {result['loaded']} 条规则加载到热缓存 "
          f"({result['elapsed_ms']}ms){Color.RESET}")


def cmd_evolve(args, evolution):
    """触发规则进化。"""
    changes = evolution.apply_pending_evolutions(dry_run=args.dry_run)

    if args.dry_run:
        print(f"{Color.YELLOW}=== 预览进化（{len(changes)} 项待处理）==={Color.RESET}\n")
    else:
        print(f"{Color.GREEN}=== 执行进化（{len(changes)} 项变更）==={Color.RESET}\n")

    if not changes:
        print(f"  无待处理进化。\n")
        return

    for c in changes:
        evo_type = c["evolution_type"]
        evo_label = {
            "confidence_adjust": "置信度调整",
            "content_refine": "内容细化",
            "split_rule": "拆分规则",
            "merge_rules": "合并规则",
            "deprecate_rule": "标记过时",
            "add_example": "增加示例",
        }.get(evo_type, evo_type)

        print(f"  {Color.BOLD}{c['rule_id']}{Color.RESET}")
        print(f"    类型: {evo_label}")
        print(f"    置信度: {Color.YELLOW}{c['old_confidence']:.2f} → {c['new_confidence']:.2f}{Color.RESET}")
        print(f"    触发: {c['trigger_reason']}")
        if c["old_content"] != c["new_content"]:
            diff = c["new_content"].replace(c["old_content"], "")
            if diff:
                print(f"    新增内容: {diff.strip()[:100]}")
        print()

    if not args.dry_run and changes:
        stat = evolution.stats()
        print(f"{Color.GRAY}归档版本总数: {stat['archived_versions']}{Color.RESET}")


def cmd_rollback(args, evolution):
    """回滚规则。"""
    success = evolution.rollback(args.rule_id, args.version)
    if success:
        rule = evolution.storage.get(args.rule_id)
        print(f"{Color.GREEN}回滚成功: {args.rule_id} → v{args.version}{Color.RESET}")
        print(f"  当前版本: v{rule.version} | 置信度: {rule.confidence}")
    else:
        print(f"{Color.RED}回滚失败: 版本 v{args.version} 不存在{Color.RESET}")


def cmd_evolution_stats(evolution):
    """进化引擎统计。"""
    s = evolution.stats()
    print(f"{Color.BOLD}进化引擎统计{Color.RESET}")
    print(f"{'=' * 40}")
    _print(s)


def cmd_evolution_pending(evolution):
    """查看待处理进化。"""
    pending = evolution.pending_evolutions
    if not pending:
        print(f"{Color.GREEN}没有待处理的进化。{Color.RESET}")
        return
    print(f"{Color.YELLOW}待处理进化（{len(pending)} 项）:{Color.RESET}\n")
    for p in pending:
        print(f"  {Color.CYAN}{p['rule_id']}{Color.RESET} → {p['evolution_type']}")
        if p.get("params"):
            for k, v in p["params"].items():
                print(f"    {k}: {v}")
        print()


def cmd_evolution_versions(args, evolution):
    """查看规则的归档版本。"""
    versions = evolution.list_archived_versions(args.rule_id)
    if not versions:
        print(f"{Color.YELLOW}规则 {args.rule_id} 没有归档版本。{Color.RESET}")
        return
    print(f"{Color.BOLD}规则 {args.rule_id} 的归档版本:{Color.RESET}")
    for v in versions:
        print(f"  v{v}")
    print(f"\n{Color.GRAY}共 {len(versions)} 个版本{Color.RESET}")


def cmd_health(index, storage):
    """健康检查。"""
    idx_stats = index.stats()
    stg_stats = storage.stats()
    print(f"{Color.BOLD}系统状态{Color.RESET}")
    print(f"{'=' * 40}")
    print(f"  状态: {Color.GREEN}运行中{Color.RESET}")
    print(f"  索引版本: {idx_stats['index_version']}")
    print(f"  有效规则: {stg_stats['active_rules']} / {stg_stats['total_rules']} 总计")
    print(f"  分类: {', '.join(stg_stats['categories'])}")
    print(f"  热缓存: {idx_stats['hot_cache_size']} 条")
    print(f"  冷区: {idx_stats['cold_count']} 条")
    print(f"  搜索总量: {idx_stats['total_searches']}")
    print(f"  缓存命中率: {idx_stats['cache_hit_rate']}%")


def cmd_stats(index, storage):
    """详细统计。"""
    idx_stats = index.stats()
    stg_stats = storage.stats()

    print(f"{Color.BOLD}存储统计{Color.RESET}")
    print(f"{'=' * 40}")
    _print(stg_stats)
    print()
    print(f"{Color.BOLD}索引统计{Color.RESET}")
    print(f"{'=' * 40}")
    _print(idx_stats)


def cmd_config():
    """显示当前配置。"""
    config = load_config()
    print(f"{Color.BOLD}当前配置{Color.RESET}")
    print(f"{'=' * 40}")
    _print(config)


def main():
    parser = argparse.ArgumentParser(
        description="Rulerything — CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # search
    p_search = sub.add_parser("search", help="搜索规则")
    p_search.add_argument("query", help="搜索关键词")
    p_search.add_argument("--type", "-t", choices=["exact", "prefix", "tag"],
                          default="exact", help="搜索类型")
    p_search.add_argument("--category", "-c", help="分类过滤")

    # list
    p_list = sub.add_parser("list", help="列出规则")
    p_list.add_argument("--category", "-c", help="分类过滤")

    # get
    p_get = sub.add_parser("get", help="查看规则详情")
    p_get.add_argument("rule_id", help="规则 ID")

    # add
    p_add = sub.add_parser("add", help="添加规则（JSON 文件）")
    p_add.add_argument("file", help="JSON 文件路径")

    # dedup
    p_dedup = sub.add_parser("dedup", help="去重管理")
    p_dedup.add_argument("--dry-run", action="store_true", help="仅预览，不执行")
    p_dedup.add_argument("--apply", dest="dry_run", action="store_false",
                         help="执行去重")

    # warmup
    p_warmup = sub.add_parser("warmup", help="预热缓存")
    p_warmup.add_argument("--category", "-c", help="指定分类")

    # evolve
    p_evolve = sub.add_parser("evolve", help="触发规则进化")
    p_evolve.add_argument("--dry-run", "-n", action="store_true",
                          help="仅预览，不执行")

    # rollback
    p_rollback = sub.add_parser("rollback", help="回滚规则到指定版本")
    p_rollback.add_argument("rule_id", help="规则 ID")
    p_rollback.add_argument("version", type=int, help="目标版本号")

    # evolution
    p_evo = sub.add_parser("evolution", help="进化管理")
    p_evo_sub = p_evo.add_subparsers(dest="evo_command", help="进化子命令")
    p_evo_sub.add_parser("stats", help="进化引擎统计")
    p_evo_pending = p_evo_sub.add_parser("pending", help="查看待处理进化")
    p_evo_vers = p_evo_sub.add_parser("versions", help="查看规则归档版本")
    p_evo_vers.add_argument("rule_id", help="规则 ID")

    # health
    sub.add_parser("health", help="健康检查")

    # stats
    sub.add_parser("stats", help="系统统计")

    # config
    sub.add_parser("config", help="查看配置")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 初始化核心组件
    config = load_config()

    # 检测 v3 模式，优先使用 SQLite 存储
    v3_enabled = config.get("v3", {}).get("enabled", False)
    storage = None
    if v3_enabled:
        try:
            from storage_v2 import RuleStorageV2
            storage = RuleStorageV2(str(Path("data") / "rules.db"))
        except ImportError:
            pass
    if storage is None:
        from storage import RuleStorage
        storage = RuleStorage("data")

    logger = RuleLogger("logs", level=config["logging"]["level"])
    index = EverythingStyleIndex(storage.list())
    index.HOT_THRESHOLD = config["index"]["hot_threshold"]
    evolution = EvolutionEngine(storage, index, logger)

    # 快速命令（不需 storage/index）
    if args.command == "config":
        cmd_config()
        return

    # 快速命令（不需 evolution）
    if args.command in ("health", "stats", "list", "get", "search",
                        "add", "dedup", "warmup"):
        commands = {
            "search": lambda: cmd_search(args, index),
            "list": lambda: cmd_list(args, storage),
            "get": lambda: cmd_get(args, storage),
            "add": lambda: cmd_add(args, storage, index, logger),
            "dedup": lambda: cmd_dedup(args, storage, index, logger),
            "warmup": lambda: cmd_warmup(args, index),
            "health": lambda: cmd_health(index, storage),
            "stats": lambda: cmd_stats(index, storage),
        }
        commands[args.command]()
        return

    # 进化命令
    if args.command == "evolve":
        cmd_evolve(args, evolution)
    elif args.command == "rollback":
        cmd_rollback(args, evolution)
    elif args.command == "evolution":
        if args.evo_command == "stats":
            cmd_evolution_stats(evolution)
        elif args.evo_command == "pending":
            cmd_evolution_pending(evolution)
        elif args.evo_command == "versions":
            cmd_evolution_versions(args, evolution)
        else:
            print(f"{Color.YELLOW}进化子命令: stats | pending | versions <rule_id>{Color.RESET}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
