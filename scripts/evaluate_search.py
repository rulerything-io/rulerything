#!/usr/bin/env python3
"""
Rulerything Search Quality Evaluation

Evaluates search quality against a set of standard queries.

Usage:
    # Evaluate with live server API
    python scripts/evaluate_search.py

    # Evaluate directly using local index (no server needed)
    python scripts/evaluate_search.py --direct

    # Update baseline after search changes
    python scripts/evaluate_search.py --update-baseline

    # Output JSON
    python scripts/evaluate_search.py --format json
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml


# ── Load test cases ───────────────────────────────────────

def load_cases(path: Path) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or []


# ── Searcher abstraction ──────────────────────────────────

class ApiSearcher:
    """Search via HTTP API."""
    def __init__(self, base_url: str = "http://127.0.0.1:8001"):
        self.base_url = base_url.rstrip("/")

    def search(self, query: str, category: Optional[str] = None,
               limit: int = 10, lang: Optional[str] = None) -> List[dict]:
        import urllib.request
        import urllib.error
        payload = {
            "query": query,
            "search_type": "smart",
            "limit": limit,
        }
        if category:
            payload["category"] = category
        if lang:
            payload["lang"] = lang
        data = json.dumps(payload).encode()
        url = f"{self.base_url}/search"
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return result.get("results", [])
        except Exception as e:
            print(f"  [API Error] '{query}': {e}", file=sys.stderr)
            return []


class DirectSearcher:
    """Search directly via local index."""
    def __init__(self):
        from config import load_config
        from core.repository import create_repository
        from index import EverythingStyleIndex

        config = load_config()
        data_dir = str(PROJECT_ROOT / "data")
        storage, _ = create_repository(config, data_dir, data_dir)
        rules = storage.list()
        self.index = EverythingStyleIndex(rules)
        print(f"  Index built: {len(rules)} rules loaded", file=sys.stderr)

    def search(self, query: str, category: Optional[str] = None,
               limit: int = 10, lang: Optional[str] = None) -> List[dict]:
        results = self.index.smart_search(
            query, category=category, limit=limit, lang=lang
        )
        return [
            {
                "id": r.id,
                "title": r.title,
                "category": r.category,
                "confidence": r.confidence,
                "score": getattr(r, "_score", r.confidence),
            }
            for r in results
        ]


# ── Metrics ───────────────────────────────────────────────

def compute_metrics(results: Dict[str, dict],
                    baseline: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compute evaluation metrics from search results."""
    total = len(results)
    if total == 0:
        return {"error": "no results"}

    top1_cat_match = 0.0
    cat_recall_at_5 = 0.0
    has_ground_truth = False
    ground_truth_count = 0
    recall_at_5_total = 0.0
    mrr_total = 0.0
    no_result_count = 0
    latencies = []

    for case_id, data in results.items():
        case = data["case"]
        hits = data["hits"]
        latency = data.get("latency_ms", 0)
        latencies.append(latency)

        # No result
        if not hits:
            no_result_count += 1
            continue

        expected_cat = case.get("category")

        # Category match@1: top result belongs to expected category
        if expected_cat:
            top_hit = hits[0]
            if top_hit.get("category") == expected_cat:
                top1_cat_match += 1.0

            # Category recall@5: at least one result from expected category in top 5
            top5_cats = {h.get("category") for h in hits[:5]}
            if expected_cat in top5_cats:
                cat_recall_at_5 += 1.0

        # Metrics using ground-truth rule IDs (when available)
        expected_ids = set(case.get("expected_rule_ids", []))
        relevant_ids = set(case.get("relevant_rule_ids", []))
        all_expected = expected_ids | relevant_ids
        if all_expected:
            has_ground_truth = True
            ground_truth_count += 1

            # Recall@5
            top5_ids = {h["id"] for h in hits[:5]}
            matched = len(all_expected & top5_ids)
            recall_at_5_total += matched / len(all_expected)

            # MRR
            for rank, h in enumerate(hits, 1):
                if h["id"] in all_expected:
                    mrr_total += 1.0 / rank
                    break
            else:
                mrr_total += 0.0

    n = total
    latencies.sort()
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    p95_idx = int(len(latencies) * 0.95)
    p95_latency = latencies[p95_idx] if latencies else 0

    metrics = {
        "total_queries": n,
        "top1_category_match": round(top1_cat_match / n, 4),
        "category_recall@5": round(cat_recall_at_5 / n, 4),
        "no_result_rate": round(no_result_count / n, 4),
        "avg_latency_ms": round(avg_latency, 2),
        "p95_latency_ms": round(p95_latency, 2),
        "has_ground_truth": has_ground_truth,
    }

    if has_ground_truth:
        metrics["recall_at_5"] = round(recall_at_5_total / ground_truth_count, 4)
        metrics["mrr"] = round(mrr_total / ground_truth_count, 4)
    else:
        metrics["recall_at_5"] = None
        metrics["mrr"] = None

    if baseline:
        metrics["regressions"] = _detect_regression(metrics, baseline)

    return metrics


def _detect_regression(current: dict, baseline: dict) -> List[dict]:
    """Detect regressions compared to baseline."""
    KEY_THRESHOLDS = {
        "top1_category_match": -0.02,
        "category_recall@5": -0.02,
        "no_result_rate": 0.02,
    }
    if current.get("has_ground_truth"):
        KEY_THRESHOLDS["recall_at_5"] = -0.02
        KEY_THRESHOLDS["mrr"] = -0.02
    regressions = []
    for key, threshold in KEY_THRESHOLDS.items():
        old = baseline.get(key, 0)
        new = current.get(key, 0)
        diff = new - old
        if (threshold < 0 and diff < threshold) or (threshold > 0 and diff > threshold):
            regressions.append({
                "metric": key,
                "baseline": old,
                "current": new,
                "diff": round(diff, 4),
                "threshold": threshold,
            })
    return regressions


def _get_case_category(case: dict) -> str:
    return case.get("category", "unknown")


def _get_lang_suffix(case: dict) -> str:
    return case.get("lang", "unknown")


# ── Main evaluation ───────────────────────────────────────

def run_evaluation(cases: List[dict], searcher,
                   update_baseline: bool = False,
                   baseline_path: Optional[Path] = None) -> Dict[str, Any]:
    """Run evaluation against all test cases."""
    results: Dict[str, dict] = {}

    for case in cases:
        case_id = case["id"]
        query = case["query"]
        category = case.get("category")
        lang = case.get("lang")

        start = time.monotonic()
        hits = searcher.search(query, category=None,
                                limit=10, lang=None)
        elapsed = (time.monotonic() - start) * 1000

        results[case_id] = {
            "case": case,
            "hits": hits,
            "latency_ms": round(elapsed, 2),
        }

    # Compute metrics
    metrics = compute_metrics(results)

    # Per-category breakdown
    categories = {}
    for case_id, data in results.items():
        cat = _get_case_category(data["case"])
        if cat not in categories:
            categories[cat] = {}
        categories[cat][case_id] = data

    per_category = {}
    for cat, cat_results in categories.items():
        per_category[cat] = compute_metrics(cat_results)

    output = {
        "metrics": metrics,
        "per_category": per_category,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_cases": len(cases),
    }

    if update_baseline and baseline_path:
        baseline_data = {
            "metrics": metrics,
            "per_category": per_category,
            "timestamp": output["timestamp"],
        }
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(baseline_data, f, ensure_ascii=False, indent=2)
        output["baseline_updated"] = str(baseline_path)

    return output


def print_report(output: Dict[str, Any]):
    """Print human-readable evaluation report."""
    metrics = output["metrics"]
    print("=" * 60)
    print("  Rulerything Search Quality Evaluation")
    print("=" * 60)
    print(f"  Queries:         {metrics['total_queries']}")
    print(f"  Top-1 Cat Match: {metrics['top1_category_match']:.2%}")
    print(f"  Category Rec@5:  {metrics['category_recall@5']:.2%}")
    if metrics.get("recall_at_5") is not None:
        print(f"  Recall@5:        {metrics['recall_at_5']:.2%}")
        print(f"  MRR:             {metrics['mrr']:.4f}")
    else:
        print(f"  Recall@5:        N/A (no ground truth)")
        print(f"  MRR:             N/A (no ground truth)")
    print(f"  No Result:       {metrics['no_result_rate']:.2%}")
    print(f"  Avg Latency:     {metrics['avg_latency_ms']}ms")
    print(f"  P95 Latency:     {metrics['p95_latency_ms']}ms")
    print()

    if "regressions" in metrics:
        reg = metrics["regressions"]
        if reg:
            print("  [WARN] REGRESSIONS DETECTED:")
            for r in reg:
                print(f"    {r['metric']}: {r['baseline']:.4f} → {r['current']:.4f} "
                      f"(threshold: {r['threshold']:+})")
            print()
        else:
            print("  [OK] No regressions detected")
            print()

    if "baseline_updated" in output:
        print(f"  Baseline saved: {output['baseline_updated']}")
        print()

    # Per category
    print("-" * 60)
    print("  Per-Category Breakdown")
    print("-" * 60)
    for cat, cat_m in sorted(output.get("per_category", {}).items()):
        r5 = f"{cat_m['recall_at_5']:.2%}" if cat_m.get('recall_at_5') is not None else "N/A"
        mrr = f"{cat_m['mrr']:.4f}" if cat_m.get('mrr') is not None else "N/A"
        print(f"  {cat:12s} | Cat@1: {cat_m['top1_category_match']:.2%}  "
              f"CatR@5: {cat_m['category_recall@5']:.2%}  "
              f"R@5: {r5}  MRR: {mrr}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Rulerything Search Quality Evaluation")
    parser.add_argument("--direct", action="store_true",
                        help="Evaluate using local index (no server needed)")
    parser.add_argument("--update-baseline", action="store_true",
                        help="Update benchmark baseline")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format")
    parser.add_argument("--cases", default=None,
                        help="Path to search cases YAML (default: benchmark/search_cases.yaml)")
    parser.add_argument("--baseline", default=None,
                        help="Path to baseline JSON (default: benchmark/benchmark_baseline.json)")
    args = parser.parse_args()

    # Paths
    cases_path = Path(args.cases) if args.cases else (
        PROJECT_ROOT / "benchmark" / "search_cases.yaml"
    )
    baseline_path = Path(args.baseline) if args.baseline else (
        PROJECT_ROOT / "benchmark" / "benchmark_baseline.json"
    )

    if not cases_path.exists():
        print(f"Error: cases file not found: {cases_path}", file=sys.stderr)
        sys.exit(1)

    # Load cases
    cases = load_cases(cases_path)
    print(f"Loaded {len(cases)} test cases from {cases_path}", file=sys.stderr)

    # Create searcher
    if args.direct:
        print("Using local index (direct mode)...", file=sys.stderr)
        searcher = DirectSearcher()
    else:
        print("Using API at http://127.0.0.1:8001...", file=sys.stderr)
        searcher = ApiSearcher()

    # Load baseline
    baseline = None
    if baseline_path.exists() and not args.update_baseline:
        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline = json.load(f)
        print(f"Loaded baseline from {baseline_path}", file=sys.stderr)

    # Run
    output = run_evaluation(cases, searcher,
                            update_baseline=args.update_baseline,
                            baseline_path=baseline_path)

    # Compare with baseline
    if baseline:
        output["metrics"]["baseline"] = baseline.get("metrics")
        output["metrics"]["regressions"] = _detect_regression(
            output["metrics"], baseline["metrics"]
        )

    # Output
    if args.format == "json":
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print_report(output)

    # Exit with error if regressions
    regressions = output.get("metrics", {}).get("regressions", [])
    if regressions:
        print("\n[FAIL] Evaluation FAILED: regressions detected", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
