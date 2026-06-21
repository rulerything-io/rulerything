"""
Rulerything Benchmark — 规则系统对比测试运行器

用法:
    python benchmark/runner.py
    python benchmark/runner.py --scene security  # 只跑单个场景
    python benchmark/runner.py --json             # 输出 JSON
"""

import argparse
import json
import sys
import time
from pathlib import Path

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenarios import get_all, get, estimate_tokens
from report import generate_report


# ── 规则系统查询 ────────────────────────────────────────

def query_rules(keyword: str) -> list[dict]:
    """通过本地 API 查询规则系统。"""
    import urllib.request
    import urllib.error

    url = "http://127.0.0.1:8001/search"
    payload = json.dumps({"query": keyword, "search_type": "exact"}).encode()
    req = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("results", [])
    except (urllib.error.URLError, ConnectionRefusedError, json.JSONDecodeError) as e:
        print(f"  [警告] 规则系统查询失败 ('{keyword}'): {e}")
        return []


# ── 评分模型 ────────────────────────────────────────────

def compute_score(
    bugs_prevented: int,
    best_practices: int,
    edge_cases: int,
    token_overhead: int,
) -> float:
    """
    综合评分模型 (0-100)。

    base(40) + bugs_prevented*8 + best_practices*5 + edge_cases*4 - token/100
    """
    score = 40.0
    score += bugs_prevented * 8
    score += best_practices * 5
    score += edge_cases * 4
    score -= token_overhead / 100  # 每 100 token 扣 1 分
    return max(0.0, min(100.0, score))


# ── 场景运行 ────────────────────────────────────────────

def run_scene(scene) -> dict:
    """运行单个 benchmark 场景。"""
    print(f"\n{'='*60}")
    print(f"场景: {scene.name} ({scene.difficulty})")
    print(f"{'='*60}")
    print(f"描述: {scene.description}")

    # 1. 查询规则系统
    print(f"\n[1/4] 查询规则系统...")
    matched_rules = []
    matched_rule_ids = set()
    for query in scene.get_rule_queries():
        results = query_rules(query)
        for r in results:
            if r.get("id") not in matched_rule_ids:
                matched_rules.append(r)
                matched_rule_ids.add(r.get("id"))
        print(f"  '{query}': {len(results)} 条结果")

    print(f"  去重后共匹配 {len(matched_rules)} 条规则")

    # 2. 计算 token
    naive_code = scene.get_naive_code()
    improved_code = scene.get_improved_code()

    naive_tokens = estimate_tokens(naive_code)
    improved_tokens = estimate_tokens(improved_code)
    rules_tokens = sum(estimate_tokens(r.get("content", "") or "")
                       for r in matched_rules)
    rules_meta_tokens = sum(
        estimate_tokens(r.get("title", "") or "" + r.get("category", "") or "")
        for r in matched_rules
    )
    total_rules_tokens = rules_tokens + rules_meta_tokens

    print(f"\n[2/4] Token 分析:")
    print(f"  无规则代码: {naive_tokens} tokens")
    print(f"  有规则代码: {improved_tokens} tokens")
    print(f"  规则内容: {rules_tokens} tokens")
    print(f"  规则元信息: {rules_meta_tokens} tokens")
    print(f"  规则总开销: {total_rules_tokens} tokens")

    # 3. 质量指标
    bugs = scene.count_prevented_bugs()
    practices = scene.count_best_practices()
    edges = scene.count_edge_cases()
    naive_bugs = scene.count_naive_bugs()

    print(f"\n[3/4] 质量分析:")
    print(f"  无规则代码潜在 bug: {len(naive_bugs)} 处")
    for b in naive_bugs:
        print(f"    ⚠ {b}")
    print(f"  规则预防的 bug: {len(bugs)} 个")
    print(f"  遵循的最佳实践: {len(practices)} 条")
    print(f"  处理的边界情况: {len(edges)} 个")

    # 4. 综合评分
    score = compute_score(
        bugs_prevented=len(bugs),
        best_practices=len(practices),
        edge_cases=len(edges),
        token_overhead=total_rules_tokens,
    )
    no_rules_score = compute_score(
        bugs_prevented=0,
        best_practices=3,  # 基础水平：会做一些基本正确的事
        edge_cases=2,
        token_overhead=0,
    )

    print(f"\n[4/4] 综合评分:")
    print(f"  无规则: {no_rules_score:.1f}/100")
    print(f"  有规则: {score:.1f}/100")
    print(f"  提升: +{score - no_rules_score:.1f}")

    return {
        "name": scene.name,
        "description": scene.description,
        "difficulty": scene.difficulty,
        "category": scene.category,
        "task_description": scene.get_task_description(),
        "naive_code": naive_code,
        "improved_code": improved_code,
        "rule_queries": scene.get_rule_queries(),
        "matched_rules": [
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "content": (r.get("content") or "")[:200],
                "confidence": r.get("confidence"),
                "category": r.get("category"),
            }
            for r in matched_rules
        ],
        "metrics": {
            "naive_tokens": naive_tokens,
            "improved_tokens": improved_tokens,
            "rules_tokens": rules_tokens,
            "rules_meta_tokens": rules_meta_tokens,
            "total_rules_tokens": total_rules_tokens,
            "naive_bugs": len(naive_bugs),
            "bugs_prevented": len(bugs),
            "best_practices": len(practices),
            "edge_cases": len(edges),
            "no_rules_score": round(no_rules_score, 1),
            "with_rules_score": round(score, 1),
            "improvement": round(score - no_rules_score, 1),
        },
        "bugs_prevented_list": bugs,
        "best_practices_list": practices,
        "edge_cases_list": edges,
        "naive_bugs_list": naive_bugs,
    }


def main():
    parser = argparse.ArgumentParser(description="Rulerything Benchmark Runner")
    parser.add_argument("--scene", type=str, help="只运行指定场景")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    if args.scene:
        scenes = [get(args.scene)]
        if scenes[0] is None:
            print(f"未知场景: {args.scene}，可选: {[s.name for s in get_all()]}")
            sys.exit(1)
    else:
        scenes = get_all()

    print("=" * 60)
    print("  Rulerything Benchmark — 规则系统综合对比测试")
    print("=" * 60)
    print(f"\n场景数: {len(scenes)}")
    print(f"规则系统: http://127.0.0.1:8001")

    # 运行所有场景
    all_results = []
    start = time.time()
    for scene in scenes:
        result = run_scene(scene)
        all_results.append(result)

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"完成! 耗时: {elapsed:.1f}s")

    # 生成报告
    report_dir = Path(__file__).parent / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    if args.json:
        report_path = report_dir / "benchmark_results.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f"JSON 报告: {report_path}")
    else:
        report_path = report_dir / "comparison_report.md"
        generate_report(all_results, report_path)
        print(f"Markdown 报告: {report_path}")

    # 汇总
    total_no_rules = sum(r["metrics"]["no_rules_score"] for r in all_results)
    total_with_rules = sum(r["metrics"]["with_rules_score"] for r in all_results)
    avg_improvement = (total_with_rules - total_no_rules) / len(all_results)
    total_tokens = sum(r["metrics"]["total_rules_tokens"] for r in all_results)

    print(f"\n{'='*60}")
    print(f"  总体汇总")
    print(f"{'='*60}")
    print(f"  无规则平均分: {total_no_rules / len(all_results):.1f}")
    print(f"  有规则平均分: {total_with_rules / len(all_results):.1f}")
    print(f"  平均提升: +{avg_improvement:.1f}")
    print(f"  规则 Token 总开销: {total_tokens}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
