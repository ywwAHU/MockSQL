"""Experiment 5 (Section 5.6): Ablation Study.

Tests different MockSQL configurations to quantify component contributions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mocksql.config import MockSQLConfig
from mocksql.proxy import MockSQLProxy
from mocksql.feedback import Verdict

from benchmarks.exp1_hazard_detection import run_hazard_detection
from benchmarks.exp3_false_positives import run_false_positive_analysis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run_ablation_study(
    dsn: str,
    hazard_file: str = "benchmarks/hazardous_queries.json",
    correct_file: str = "benchmarks/correct_queries.json",
    output_file: str = "results/exp5_ablation.json",
):
    """Run ablation study with different configurations."""

    configs = {
        "plan_only": {
            "enable_plan_channel": True,
            "enable_exec_channel": False,
            "whitelist_patterns": [],
        },
        "exec_only": {
            "enable_plan_channel": False,
            "enable_exec_channel": True,
            "whitelist_patterns": [],
        },
        "dual": {
            "enable_plan_channel": True,
            "enable_exec_channel": True,
            "whitelist_patterns": [],
        },
        "dual_whitelist": {
            "enable_plan_channel": True,
            "enable_exec_channel": True,
            "whitelist_patterns": [
                # Whitelist non-equi join patterns to reduce FP
                r".*\bBETWEEN\b.*\bAND\b.*",
                r".*[<>]=?\s+\w+\.\w+.*",
            ],
        },
    }

    results = {}

    for config_name, overrides in configs.items():
        logger.info("=== Ablation: %s ===", config_name)

        config = MockSQLConfig(prod_dsn=dsn, **overrides)
        proxy = MockSQLProxy(config)

        # --- Detection rate on hazardous queries ---
        with open(hazard_file) as f:
            hazardous = json.load(f)

        detected = 0
        total_h = len(hazardous)
        for q in hazardous:
            try:
                feedback = proxy.verify(q["sql"])
                if feedback.verdict in (Verdict.REJECT, Verdict.WARNING):
                    detected += 1
            except Exception:
                pass

        detection_rate = detected / total_h * 100 if total_h > 0 else 0

        # --- False positive rate on correct queries ---
        with open(correct_file) as f:
            correct = json.load(f)

        false_rejects = 0
        total_c = len(correct)
        for q in correct:
            try:
                feedback = proxy.verify(q["sql"])
                if feedback.verdict == Verdict.REJECT:
                    false_rejects += 1
            except Exception:
                pass

        fp_rate = false_rejects / total_c * 100 if total_c > 0 else 0

        results[config_name] = {
            "detection_rate": round(detection_rate, 1),
            "fp_rate": round(fp_rate, 1),
            "hazardous_total": total_h,
            "hazardous_detected": detected,
            "correct_total": total_c,
            "false_rejects": false_rejects,
        }

        logger.info(
            "  %s: detection=%.1f%%, FP=%.1f%%",
            config_name, detection_rate, fp_rate,
        )

    # Save results
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    # Print summary table (Table 7 in paper)
    print("\n" + "=" * 65)
    print("Ablation Study (Table 7 in paper)")
    print("=" * 65)
    print(f"{'Configuration':<25} {'Detection Rate':>15} {'FP Rate':>10}")
    print("-" * 65)
    for name, r in results.items():
        print(
            f"{name:<25}"
            f" {r['detection_rate']:>14.1f}%"
            f" {r['fp_rate']:>9.1f}%"
        )
    print("=" * 65)

    return results


if __name__ == "__main__":
    import sys
    dsn = sys.argv[1] if len(sys.argv) > 1 else "postgresql://localhost:5432/mocksql_bench"
    run_ablation_study(dsn)
