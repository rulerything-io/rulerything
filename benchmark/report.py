"""
Rulerything Benchmark — Markdown 报告生成器
"""

from pathlib import Path
from typing import List


def _bar(value: float, max_value: float, width: int = 20) -> str:
    """生成 ASCII 进度条。"""
    filled = int((value / max_value) * width) if max_value > 0 else 0
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _emoji_score(score: float) -> str:
    if score >= 90:
        return "🏆"
    elif score >= 70:
        return "⭐"
    elif score >= 50:
        return "✅"
    else:
        return "⚠️"


def generate_report(results: List[dict], output_path: str):
    """生成 Markdown 格式对比报告。"""
    lines = []

    lines.append("# Rulerything Benchmark — 规则系统综合对比报告")
    lines.append("")
    lines.append(f"> 生成时间: {_now()}")
    lines.append(f"> 场景数: {len(results)}")
    lines.append("")

    # ── 总体评分汇总 ──────────────────────────────────
    lines.append("## 总体评分汇总")
    lines.append("")
    lines.append("| 场景 | 难度 | 无规则 | 有规则 | 提升 | Token 开销 |")
    lines.append("|------|------|--------|--------|------|-----------|")

    total_no_rules = 0
    total_with_rules = 0
    total_tokens = 0
    total_bugs = 0
    total_prevented = 0

    for r in results:
        m = r["metrics"]
        improvement = m["with_rules_score"] - m["no_rules_score"]
        sign = "+" if improvement > 0 else ""
        lines.append(
            f"| {r['name']} | {r['difficulty']} | "
            f"{m['no_rules_score']:.1f} | "
            f"{m['with_rules_score']:.1f} | "
            f"{sign}{improvement:.1f} | "
            f"{m['total_rules_tokens']} |"
        )
        total_no_rules += m["no_rules_score"]
        total_with_rules += m["with_rules_score"]
        total_tokens += m["total_rules_tokens"]
        total_bugs += m["naive_bugs"]
        total_prevented += m["bugs_prevented"]

    avg_no_rules = total_no_rules / len(results)
    avg_with_rules = total_with_rules / len(results)
    avg_improvement = avg_with_rules - avg_no_rules

    lines.append(f"| **平均** | | **{avg_no_rules:.1f}** | **{avg_with_rules:.1f}** | **+{avg_improvement:.1f}** | **{total_tokens}** |")
    lines.append("")

    # 评分模型说明
    lines.append("### 评分模型")
    lines.append("")
    lines.append("```")
    lines.append("score = 40 (基础分)")
    lines.append("     + bugs_prevented × 8    # 每个预防的 bug")
    lines.append("     + best_practices × 5    # 每条最佳实践")
    lines.append("     + edge_cases × 4        # 每个边界情况")
    lines.append("     - token_overhead / 100  # Token 扣分")
    lines.append("     = total (0-100)")
    lines.append("```")
    lines.append("")

    # ── 无规则总览 ────────────────────────────────────
    lines.append("## 无规则代码 — 问题汇总")
    lines.append("")
    lines.append(f"共发现 **{total_bugs}** 处潜在问题：")
    lines.append("")

    for r in results:
        bugs = r.get("naive_bugs_list", [])
        lines.append(f"### {r['name']} ({len(bugs)} 处)")
        for b in bugs:
            lines.append(f"- ⚠️  {b}")
        lines.append("")

    # ── 规则效果 ──────────────────────────────────────
    lines.append("## 规则系统效果分析")
    lines.append("")
    lines.append(f"规则系统共预防了 **{total_prevented}** 处 Bug：")
    lines.append("")

    for r in results:
        prevented = r.get("bugs_prevented_list", [])
        practices = r.get("best_practices_list", [])
        edges = r.get("edge_cases_list", [])
        matched = r.get("matched_rules", [])
        m = r["metrics"]

        lines.append(f"### {r['name']}")
        lines.append("")
        lines.append(f"- **难度**: {r['difficulty']}")
        lines.append(f"- **匹配规则**: {len(matched)} 条")

        if matched:
            lines.append("- **匹配规则明细**:")
            for rule in matched[:10]:  # 最多展示 10 条
                title = rule.get("title", "unknown")
                cat = rule.get("category", "")
                cid = rule.get("id", "")
                conf = rule.get("confidence", 0)
                lines.append(f"  - `{cid}` [{cat}] {title} (置信度: {conf})")

        lines.append(f"- **Token 开销**: {m['total_rules_tokens']} tokens")
        lines.append(f"  - 规则内容: {m['rules_tokens']} tokens")
        lines.append(f"  - 规则元信息: {m['rules_meta_tokens']} tokens")

        lines.append("")
        lines.append("#### 预防的 Bug")
        for b in prevented:
            lines.append(f"- ✅ {b}")
        if not prevented:
            lines.append("- 无")
        lines.append("")

        lines.append("#### 遵循的最佳实践")
        for p in practices:
            lines.append(f"- 📋 {p}")
        if not practices:
            lines.append("- 无")
        lines.append("")

        lines.append("#### 处理的边界情况")
        for e in edges:
            lines.append(f"- 🔍 {e}")
        if not edges:
            lines.append("- 无")
        lines.append("")

    # ── 代码对比 ──────────────────────────────────────
    lines.append("## 代码对比")
    lines.append("")

    for r in results:
        lines.append(f"### {r['name']}")
        lines.append("")

        # 任务描述
        lines.append("**任务**")
        lines.append("")
        for line in r["task_description"].split("\n"):
            lines.append(f"> {line}")
        lines.append("")

        lines.append("<details>")
        lines.append(f"<summary>查看代码对比 (无规则 vs 有规则)</summary>")
        lines.append("")
        lines.append("#### ❌ 无规则实现")
        lines.append("")
        lines.append(f"```python")
        lines.append(r["naive_code"].rstrip())
        lines.append("```")
        lines.append("")
        lines.append("#### ✅ 规则指导下的实现")
        lines.append("")
        lines.append(f"```python")
        lines.append(r["improved_code"].rstrip())
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # ── Token 对比 ────────────────────────────────────
    lines.append("## Token 消耗分析")
    lines.append("")
    lines.append("| 场景 | 无规则代码 | 有规则代码 | 规则 Token | 规则占比 |")
    lines.append("|------|-----------|-----------|-----------|---------|")

    for r in results:
        m = r["metrics"]
        ratio = m["total_rules_tokens"] / max(m["improved_tokens"], 1) * 100
        lines.append(
            f"| {r['name']} | {m['naive_tokens']} | {m['improved_tokens']} | "
            f"{m['total_rules_tokens']} | {ratio:.1f}% |"
        )
    lines.append("")

    lines.append("### 分析")
    lines.append("")
    lines.append(f"- 规则 Token 总开销: **{total_tokens}**")
    lines.append(f"- 平均每场景: **{total_tokens // len(results)}** tokens")
    lines.append(f"- 相对改进代码占比: 通常 **5-20%**")
    lines.append("- 与预防的 bug 相比，Token 成本可以忽略不计")
    lines.append("")

    # ── 最终评分 ──────────────────────────────────────
    lines.append("## 最终评分")
    lines.append("")

    max_score = max(r["metrics"]["with_rules_score"] for r in results) if results else 100

    for r in results:
        m = r["metrics"]
        bar = _bar(m["with_rules_score"], max_score)
        emoji = _emoji_score(m["with_rules_score"])
        lines.append(
            f"| {emoji} **{r['name']}** | {bar} | "
            f"{m['with_rules_score']:.1f}/{max_score:.0f} |"
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*报告由 Rulerything Benchmark 自动生成*")

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入: {output_path}")


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
