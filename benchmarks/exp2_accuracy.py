"""Experiment 2 (Section 5.3): Text-to-SQL Accuracy Improvement.

Measures execution accuracy (EX) on Spider/BIRD with and without MockSQL feedback loop.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from mocksql.config import MockSQLConfig
from mocksql.proxy import MockSQLProxy
from mocksql.feedback import Verdict
from mocksql.llm_client import LLMClient, build_schema_prompt
from mocksql.metadata import MetadataExtractor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_spider_dev(spider_path: str = "./spider") -> list[dict]:
    """Load Spider dev set questions."""
    dev_file = os.path.join(spider_path, "dev.json")
    with open(dev_file) as f:
        data = json.load(f)
    return data


def load_bird_dev(bird_path: str = "./bird") -> list[dict]:
    """Load BIRD dev set questions."""
    dev_file = os.path.join(bird_path, "dev", "dev.json")
    with open(dev_file) as f:
        data = json.load(f)
    return data


def execute_and_compare(dsn: str, predicted_sql: str, gold_sql: str, db_id: str) -> bool:
    """Execute both predicted and gold SQL, compare results (execution accuracy)."""
    import psycopg2

    try:
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            # Execute gold SQL
            cur.execute(gold_sql)
            gold_result = set(cur.fetchall())

            # Execute predicted SQL
            cur.execute(predicted_sql)
            pred_result = set(cur.fetchall())

        conn.close()
        return pred_result == gold_result
    except Exception as e:
        logger.debug("Execution comparison failed: %s", e)
        return False


def run_accuracy_experiment(
    dsn: str,
    dataset: str = "spider",
    dataset_path: str = "./spider",
    llm_configs: Optional[list[dict]] = None,
    max_queries: int = -1,
    max_retries: int = 3,
    output_file: str = "results/exp2_accuracy.json",
):
    """Run Text-to-SQL accuracy experiment.

    Args:
        dsn: PostgreSQL connection string
        dataset: "spider" or "bird"
        dataset_path: Path to dataset files
        llm_configs: List of LLM configurations, each with:
            {"name": "GPT-4o", "provider": "openai", "model": "gpt-4o",
             "api_key": "...", "base_url": "..."}
        max_queries: Limit number of queries (-1 = all)
        max_retries: Max MockSQL retry attempts
    """
    # Load dataset
    if dataset == "spider":
        questions = load_spider_dev(dataset_path)
    elif dataset == "bird":
        questions = load_bird_dev(dataset_path)
    else:
        raise ValueError(f"Unknown dataset: {dataset}")

    if max_queries > 0:
        questions = questions[:max_queries]

    logger.info("Loaded %d questions from %s", len(questions), dataset)

    # Default LLM configs if not provided
    if llm_configs is None:
        llm_configs = [
            {
                "name": "GPT-4o",
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": os.environ.get("OPENAI_API_KEY", ""),
            },
            {
                "name": "Claude-Sonnet-4",
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
                "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
            },
            {
                "name": "DeepSeek-V3",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
                "base_url": "https://api.deepseek.com/v1",
            },
            {
                "name": "Qwen2.5-72B",
                "provider": "qwen",
                "model": "qwen2.5-72b-instruct",
                "api_key": os.environ.get("QWEN_API_KEY", ""),
                "base_url": os.environ.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            },
        ]

    all_results = {}
    metadata_extractor = MetadataExtractor(dsn)

    for llm_cfg in llm_configs:
        if not llm_cfg.get("api_key"):
            logger.warning("Skipping %s: no API key", llm_cfg["name"])
            continue

        logger.info("=== Testing LLM: %s ===", llm_cfg["name"])

        config = MockSQLConfig(
            prod_dsn=dsn,
            llm_provider=llm_cfg["provider"],
            llm_model=llm_cfg["model"],
            llm_api_key=llm_cfg["api_key"],
            llm_base_url=llm_cfg.get("base_url"),
            max_retries=max_retries,
        )
        proxy = MockSQLProxy(config)
        llm_client = LLMClient(config)

        # Track results
        baseline_correct = 0
        mocksql_correct = 0
        retry_stats = {1: 0, 2: 0, 3: 0}
        total = 0

        for i, question in enumerate(questions):
            db_id = question.get("db_id", "")
            nl_question = question.get("question", "")
            gold_sql = question.get("query", question.get("SQL", ""))

            if not nl_question or not gold_sql:
                continue

            total += 1

            # Get schema info for prompt
            try:
                tables = _extract_tables_from_schema(question, dataset)
                tables_info = metadata_extractor.get_tables_info(tables)
                schema_prompt = build_schema_prompt(tables_info)
            except Exception as e:
                logger.debug("Schema extraction failed for %s: %s", db_id, e)
                schema_prompt = f"Database: {db_id}"

            # --- Baseline: generate SQL without MockSQL ---
            try:
                baseline_sql = llm_client.generate_sql(nl_question, schema_prompt)
                if execute_and_compare(dsn, baseline_sql, gold_sql, db_id):
                    baseline_correct += 1
            except Exception as e:
                logger.debug("Baseline generation failed: %s", e)
                baseline_sql = ""

            # --- MockSQL: generate SQL with feedback loop ---
            try:
                current_sql = llm_client.generate_sql(nl_question, schema_prompt)
                success = False

                for attempt in range(1, max_retries + 1):
                    feedback = proxy.verify(current_sql)

                    if feedback.verdict == Verdict.PASS:
                        if execute_and_compare(dsn, current_sql, gold_sql, db_id):
                            success = True
                            retry_stats[attempt] = retry_stats.get(attempt, 0) + 1
                        break
                    elif feedback.verdict == Verdict.WARNING:
                        if execute_and_compare(dsn, current_sql, gold_sql, db_id):
                            success = True
                            retry_stats[attempt] = retry_stats.get(attempt, 0) + 1
                        break
                    else:  # REJECT
                        if attempt < max_retries:
                            revised = llm_client.revise_sql(
                                current_sql, feedback.to_llm_feedback()
                            )
                            if revised and revised != current_sql:
                                current_sql = revised
                            else:
                                break
                        else:
                            # Last attempt: check anyway
                            if execute_and_compare(dsn, current_sql, gold_sql, db_id):
                                success = True

                if success:
                    mocksql_correct += 1

            except Exception as e:
                logger.debug("MockSQL pipeline failed: %s", e)

            if (i + 1) % 50 == 0:
                logger.info(
                    "  [%s] Progress: %d/%d | Baseline: %.1f%% | +MockSQL: %.1f%%",
                    llm_cfg["name"], i + 1, len(questions),
                    baseline_correct / total * 100,
                    mocksql_correct / total * 100,
                )

        # Compute final metrics
        baseline_ex = baseline_correct / total * 100 if total > 0 else 0
        mocksql_ex = mocksql_correct / total * 100 if total > 0 else 0
        delta = mocksql_ex - baseline_ex

        all_results[llm_cfg["name"]] = {
            "dataset": dataset,
            "total_queries": total,
            "baseline_correct": baseline_correct,
            "baseline_ex": round(baseline_ex, 1),
            "mocksql_correct": mocksql_correct,
            "mocksql_ex": round(mocksql_ex, 1),
            "delta": round(delta, 1),
            "retry_stats": retry_stats,
        }

        logger.info(
            "[%s] Final: Baseline=%.1f%%, +MockSQL=%.1f%%, Delta=+%.1f",
            llm_cfg["name"], baseline_ex, mocksql_ex, delta,
        )

    # Save results
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2)

    # Print summary table
    print("\n" + "=" * 90)
    print(f"Text-to-SQL Accuracy Results on {dataset.upper()} (Table 4 in paper)")
    print("=" * 90)
    print(f"{'Model':<20} {'Base EX':>10} {'+MockSQL EX':>12} {'Delta':>8} {'Queries':>10}")
    print("-" * 90)
    for name, r in all_results.items():
        print(
            f"{name:<20}"
            f" {r['baseline_ex']:>9.1f}%"
            f" {r['mocksql_ex']:>11.1f}%"
            f" {'+' + str(r['delta']):>7}"
            f" {r['total_queries']:>10}"
        )
    print("=" * 90)

    return all_results


def _extract_tables_from_schema(question: dict, dataset: str) -> list[str]:
    """Extract table names from dataset question metadata."""
    if dataset == "spider":
        # Spider has table_names_original in the schema
        return question.get("table_names_original", [])
    elif dataset == "bird":
        return question.get("table_names", [])
    return []


if __name__ == "__main__":
    import sys
    dsn = sys.argv[1] if len(sys.argv) > 1 else "postgresql://localhost:5432/mocksql_bench"
    dataset = sys.argv[2] if len(sys.argv) > 2 else "spider"
    dataset_path = sys.argv[3] if len(sys.argv) > 3 else f"./{dataset}"
    run_accuracy_experiment(dsn, dataset=dataset, dataset_path=dataset_path)
