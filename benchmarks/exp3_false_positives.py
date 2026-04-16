"""Experiment 3 (Section 5.4): False Positive Analysis.

Run known-correct queries through MockSQL and measure false rejection rate.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from mocksql.config import MockSQLConfig
from mocksql.proxy import MockSQLProxy
from mocksql.feedback import Verdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run_false_positive_analysis(
    dsn: str,
    correct_file: str = "benchmarks/correct_queries.json",
    output_file: str = "results/exp3_false_positives.json",
):
    """Run false positive analysis on known-correct queries."""

    with open(correct_file) as f:
        queries = json.load(f)

    config = MockSQLConfig(prod_dsn=dsn)
    proxy = MockSQLProxy(config)

    category_counts = defaultdict(lambda: {"total": 0, "false_rejects": 0, "false_warnings": 0})

    for i, query in enumerate(queries):
        sql = query["sql"]
        category = query["category"]

        try:
            feedback = proxy.verify(sql)
            is_rejected = feedback.verdict == Verdict.REJECT
            is_warned = feedback.verdict == Verdict.WARNING
        except Exception as e:
            logger.warning("Query %d failed: %s", i, e)
            is_rejected = False
            is_warned = False

        category_counts[category]["total"] += 1
        if is_rejected:
            category_counts[category]["false_rejects"] += 1
        if is_warned:
            category_counts[category]["false_warnings"] += 1

        if (i + 1) % 100 == 0:
            logger.info("Progress: %d/%d", i + 1, len(queries))

    # Compute rates
    results = {}
    total_queries = 0
    total_fp = 0

    category_display = {
        "single_table": "Single-table",
        "equi_join_2": "Equi-join (2 tables)",
        "equi_join_3plus": "Equi-join (3+ tables)",
        "non_equi_join": "Non-equi join",
        "subquery": "Subquery",
    }

    for category in ["single_table", "equi_join_2", "equi_join_3plus", "non_equi_join", "subquery"]:
        c = category_counts[category]
        fp = c["false_rejects"]
        fp_rate = fp / c["total"] * 100 if c["total"] > 0 else 0
        results[category] = {
            "display_name": category_display.get(category, category),
            "total": c["total"],
            "false_rejects": fp,
            "false_warnings": c["false_warnings"],
            "fp_rate": round(fp_rate, 1),
        }
        total_queries += c["total"]
        total_fp += fp

    results["overall"] = {
        "display_name": "Overall",
        "total": total_queries,
        "false_rejects": total_fp,
        "fp_rate": round(total_fp / total_queries * 100, 1) if total_queries > 0 else 0,
    }

    # Save results
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    # Print summary table (Table 5 in paper)
    print("\n" + "=" * 70)
    print("False Positive Analysis (Table 5 in paper)")
    print("=" * 70)
    print(f"{'Query Class':<25} {'Queries':>10} {'False Rejects':>15} {'FP Rate':>10}")
    print("-" * 70)
    for category in ["single_table", "equi_join_2", "equi_join_3plus", "non_equi_join", "subquery", "overall"]:
        r = results[category]
        print(
            f"{r['display_name']:<25}"
            f" {r['total']:>10}"
            f" {r['false_rejects']:>15}"
            f" {r['fp_rate']:>9.1f}%"
        )
    print("=" * 70)

    return results


if __name__ == "__main__":
    import sys
    dsn = sys.argv[1] if len(sys.argv) > 1 else "postgresql://localhost:5432/mocksql_bench"
    run_false_positive_analysis(dsn)
