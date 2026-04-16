"""Experiment 4 (Section 5.5): Latency Overhead Measurement.

Measures per-component latency breakdown of MockSQL verification.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np

from mocksql.config import MockSQLConfig
from mocksql.proxy import MockSQLProxy
from mocksql.plan_channel import PlanChannel
from mocksql.exec_channel import ExecChannel
from mocksql.metadata import MetadataExtractor
from mocksql.synthesizer import DataSynthesizer
from mocksql.feedback import FeedbackGenerator
from mocksql.sql_parser import extract_tables

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# Representative queries for latency measurement
LATENCY_QUERIES = [
    # Simple single-table
    "SELECT c_name, c_acctbal FROM customer WHERE c_acctbal > 1000 LIMIT 50",
    # Two-table join
    "SELECT c.c_name, o.o_totalprice FROM customer c JOIN orders o ON c.c_custkey = o.o_custkey LIMIT 100",
    # Three-table join
    "SELECT c.c_name, o.o_orderdate, l.l_extendedprice FROM customer c "
    "JOIN orders o ON c.c_custkey = o.o_custkey "
    "JOIN lineitem l ON o.o_orderkey = l.l_orderkey LIMIT 50",
    # Five-table join
    "SELECT c.c_name, o.o_orderdate, l.l_extendedprice, p.p_name, s.s_name FROM customer c "
    "JOIN orders o ON c.c_custkey = o.o_custkey "
    "JOIN lineitem l ON o.o_orderkey = l.l_orderkey "
    "JOIN part p ON l.l_partkey = p.p_partkey "
    "JOIN supplier s ON l.l_suppkey = s.s_suppkey LIMIT 20",
    # Aggregation
    "SELECT o_custkey, COUNT(*) as cnt, SUM(o_totalprice) as total "
    "FROM orders GROUP BY o_custkey HAVING COUNT(*) > 5 LIMIT 50",
    # Subquery
    "SELECT c_name FROM customer WHERE c_custkey IN "
    "(SELECT o_custkey FROM orders WHERE o_totalprice > 10000) LIMIT 50",
]


def run_latency_experiment(
    dsn: str,
    n_iterations: int = 100,
    output_file: str = "results/exp4_latency.json",
):
    """Measure per-component latency breakdown."""

    config = MockSQLConfig(prod_dsn=dsn)
    metadata_extractor = MetadataExtractor(dsn, cache_ttl=config.metadata_cache_ttl_seconds)
    synthesizer = DataSynthesizer(config)
    plan_channel = PlanChannel(config)
    exec_channel = ExecChannel(config, metadata_extractor, synthesizer)
    feedback_gen = FeedbackGenerator()

    # Warm up metadata cache
    logger.info("Warming up metadata cache...")
    for sql in LATENCY_QUERIES:
        tables = extract_tables(sql)
        metadata_extractor.get_tables_info(tables)

    # Collect latencies
    plan_latencies = []
    exec_latencies = []
    synthesis_latencies = []
    execution_latencies = []
    feedback_latencies = []
    total_latencies = []

    logger.info("Running %d iterations x %d queries...", n_iterations, len(LATENCY_QUERIES))

    for iteration in range(n_iterations):
        for sql in LATENCY_QUERIES:
            # Plan Channel
            plan_result = plan_channel.analyze(sql)
            plan_latencies.append(plan_result.latency_ms)

            # Exec Channel (with breakdown)
            exec_result = exec_channel.analyze(sql)
            exec_latencies.append(exec_result.latency_ms)
            synthesis_latencies.append(exec_result.synthesis_ms)
            execution_latencies.append(exec_result.execution_ms)

            # Feedback generation
            fb_start = time.perf_counter()
            feedback = feedback_gen.generate(plan_result, exec_result)
            fb_ms = (time.perf_counter() - fb_start) * 1000
            feedback_latencies.append(fb_ms)

            # Total (max of concurrent channels + feedback)
            total = max(plan_result.latency_ms, exec_result.latency_ms) + fb_ms
            total_latencies.append(total)

        if (iteration + 1) % 20 == 0:
            logger.info("  Iteration %d/%d", iteration + 1, n_iterations)

    # Compute statistics
    def stats(values):
        arr = np.array(values)
        return {
            "median": round(float(np.median(arr)), 1),
            "mean": round(float(np.mean(arr)), 1),
            "p95": round(float(np.percentile(arr, 95)), 1),
            "p99": round(float(np.percentile(arr, 99)), 1),
            "min": round(float(np.min(arr)), 1),
            "max": round(float(np.max(arr)), 1),
            "std": round(float(np.std(arr)), 1),
        }

    results = {
        "n_iterations": n_iterations,
        "n_queries": len(LATENCY_QUERIES),
        "total_measurements": len(total_latencies),
        "components": {
            "plan_channel": stats(plan_latencies),
            "exec_channel_total": stats(exec_latencies),
            "data_synthesis": stats(synthesis_latencies),
            "sandbox_execution": stats(execution_latencies),
            "feedback_generation": stats(feedback_latencies),
            "mocksql_total": stats(total_latencies),
        },
    }

    # Save results
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    # Print summary table (Table 6 in paper)
    print("\n" + "=" * 70)
    print("Latency Overhead (Table 6 in paper)")
    print("=" * 70)
    print(f"{'Component':<25} {'Median(ms)':>12} {'P95(ms)':>10} {'P99(ms)':>10}")
    print("-" * 70)
    for comp_name, comp_stats in results["components"].items():
        display = comp_name.replace("_", " ").title()
        print(
            f"{display:<25}"
            f" {comp_stats['median']:>12.1f}"
            f" {comp_stats['p95']:>10.1f}"
            f" {comp_stats['p99']:>10.1f}"
        )
    print("=" * 70)

    return results


if __name__ == "__main__":
    import sys
    dsn = sys.argv[1] if len(sys.argv) > 1 else "postgresql://localhost:5432/mocksql_bench"
    run_latency_experiment(dsn)
