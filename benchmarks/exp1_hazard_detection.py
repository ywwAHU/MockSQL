"""Experiment 1 (Section 5.2): Hazard Detection Effectiveness.

Measures detection rate for Plan-only, Exec-only, and Dual-channel
across 4 categories of hazardous queries.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from mocksql.config import MockSQLConfig
from mocksql.proxy import MockSQLProxy
from mocksql.feedback import Verdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run_hazard_detection(
    dsn: str,
    hazard_file: str = "benchmarks/hazardous_queries.json",
    output_file: str = "results/exp1_hazard_detection.json",
):
    """Run hazard detection experiment across 3 MockSQL configurations."""

    with open(hazard_file) as f:
        queries = json.load(f)

    configs = {
        "plan_only": MockSQLConfig(
            prod_dsn=dsn, enable_plan_channel=True, enable_exec_channel=False
        ),
        "exec_only": MockSQLConfig(
            prod_dsn=dsn, enable_plan_channel=False, enable_exec_channel=True
        ),
        "dual": MockSQLConfig(
            prod_dsn=dsn, enable_plan_channel=True, enable_exec_channel=True
        ),
    }

    all_results = {}

    for config_name, config in configs.items():
        logger.info("=== Running config: %s ===", config_name)
        proxy = MockSQLProxy(config)

        category_counts = defaultdict(lambda: {"total": 0, "detected": 0})

        for i, query in enumerate(queries):
            sql = query["sql"]
            category = query["category"]

            try:
                feedback = proxy.verify(sql)
                detected = feedback.verdict in (Verdict.REJECT, Verdict.WARNING)
            except Exception as e:
                logger.warning("Query %d failed: %s", i, e)
                detected = False

            category_counts[category]["total"] += 1
            if detected:
                category_counts[category]["detected"] += 1

            if (i + 1) % 20 == 0:
                logger.info("  Progress: %d/%d", i + 1, len(queries))

        # Compute rates
        results = {}
        total_detected = 0
        total_queries = 0

        for category in ["CARTESIAN", "FULL_SCAN", "EMPTY_JOIN", "AGG_ERROR"]:
            c = category_counts[category]
            rate = c["detected"] / c["total"] if c["total"] > 0 else 0
            results[category] = {
                "total": c["total"],
                "detected": c["detected"],
                "rate": round(rate * 100, 1),
            }
            total_detected += c["detected"]
            total_queries += c["total"]

        results["overall"] = {
            "total": total_queries,
            "detected": total_detected,
            "rate": round(total_detected / total_queries * 100, 1) if total_queries > 0 else 0,
        }

        all_results[config_name] = results
        logger.info("Config %s overall: %s", config_name, results["overall"])

    # Save results
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2)

    # Print summary table
    print("\n" + "=" * 80)
    print("Hazard Detection Results (Table 3 in paper)")
    print("=" * 80)
    print(f"{'Config':<15} {'Cartesian':>12} {'Full Scan':>12} {'Empty Join':>12} {'Agg Error':>12} {'Overall':>12}")
    print("-" * 80)
    for config_name, results in all_results.items():
        print(
            f"{config_name:<15}"
            f" {results.get('CARTESIAN', {}).get('rate', 0):>11.1f}%"
            f" {results.get('FULL_SCAN', {}).get('rate', 0):>11.1f}%"
            f" {results.get('EMPTY_JOIN', {}).get('rate', 0):>11.1f}%"
            f" {results.get('AGG_ERROR', {}).get('rate', 0):>11.1f}%"
            f" {results.get('overall', {}).get('rate', 0):>11.1f}%"
        )
    print("=" * 80)

    return all_results


if __name__ == "__main__":
    import sys
    dsn = sys.argv[1] if len(sys.argv) > 1 else "postgresql://localhost:5432/mocksql_bench"
    run_hazard_detection(dsn)
