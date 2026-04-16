"""MockSQL Proxy: the main entry point that orchestrates dual-channel verification."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

import psycopg2

from mocksql.config import MockSQLConfig
from mocksql.metadata import MetadataExtractor
from mocksql.synthesizer import DataSynthesizer
from mocksql.plan_channel import PlanChannel, PlanChannelResult
from mocksql.exec_channel import ExecChannel, ExecChannelResult
from mocksql.feedback import FeedbackGenerator, MockSQLFeedback, Verdict
from mocksql.sql_parser import get_query_type

logger = logging.getLogger(__name__)


class MockSQLProxy:
    """Dual-channel verification middleware for LLM-generated SQL.

    Usage:
        config = MockSQLConfig(prod_dsn="postgresql://...")
        proxy = MockSQLProxy(config)
        feedback = proxy.verify("SELECT * FROM orders JOIN products ON ...")

        if feedback.verdict == Verdict.PASS:
            # Safe to execute on production
            result = proxy.execute(sql)
        else:
            # Return feedback to LLM for revision
            llm_feedback = feedback.to_llm_feedback()
    """

    def __init__(self, config: MockSQLConfig):
        self._config = config
        self._metadata = MetadataExtractor(
            dsn=config.prod_dsn,
            cache_ttl=config.metadata_cache_ttl_seconds,
        )
        self._synthesizer = DataSynthesizer(config)
        self._plan_channel = PlanChannel(config) if config.enable_plan_channel else None
        self._exec_channel = (
            ExecChannel(config, self._metadata, self._synthesizer)
            if config.enable_exec_channel
            else None
        )
        self._feedback_gen = FeedbackGenerator()

    def verify(self, sql: str) -> MockSQLFeedback:
        """Verify a SQL query through dual-channel analysis.

        Returns a MockSQLFeedback with verdict (PASS/WARNING/REJECT) and details.
        """
        query_type = get_query_type(sql)
        if not query_type or query_type.upper() == "UNKNOWN":
            logger.warning("MockSQL could not parse the query type.")
            return MockSQLFeedback(
                verdict=Verdict.REJECT,
                exec_issues=[
                    {
                        "severity": "CRITICAL",
                        "type": "EXECUTION_ERROR",
                        "detail": (
                            "MockSQL could not parse this query as a valid SELECT statement. "
                            "The SQL likely contains a syntax error."
                        ),
                        "suggestion": (
                            "Check the SQL keywords, table names, and clause order, then retry "
                            "with a valid SELECT query."
                        ),
                    }
                ],
                summary="1 critical semantic issue(s) detected.",
            )

        if query_type.upper() != "SELECT":
            logger.warning("MockSQL currently only validates SELECT; got %s", query_type)
            return MockSQLFeedback(
                verdict=Verdict.REJECT,
                exec_issues=[
                    {
                        "severity": "CRITICAL",
                        "type": "EXECUTION_ERROR",
                        "detail": (
                            f"MockSQL currently only validates SELECT statements; got {query_type}."
                        ),
                        "suggestion": (
                            "Use MockSQL only for read-only SELECT queries, or bypass verification "
                            "for write operations."
                        ),
                    }
                ],
                summary="1 critical semantic issue(s) detected.",
            )

        plan_result = None
        exec_result = None

        # Run both channels concurrently
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {}

            if self._plan_channel:
                futures["plan"] = pool.submit(self._plan_channel.analyze, sql)
            if self._exec_channel:
                futures["exec"] = pool.submit(self._exec_channel.analyze, sql)

            for key, future in futures.items():
                try:
                    result = future.result(timeout=30)
                    if key == "plan":
                        plan_result = result
                    else:
                        exec_result = result
                except Exception as e:
                    logger.error("Channel '%s' failed: %s", key, e)

        # Merge results
        feedback = self._feedback_gen.generate(plan_result, exec_result)
        return feedback

    def verify_and_execute(
        self, sql: str, max_retries: int = 0, revision_callback=None
    ) -> tuple[MockSQLFeedback, Optional[list[tuple]]]:
        """Verify SQL and execute if it passes.

        Args:
            sql: The SQL query to verify and execute.
            max_retries: Max number of revision attempts (0 = no retries).
            revision_callback: A callable(sql, feedback_text) -> revised_sql.
                Used for LLM-in-the-loop revision.

        Returns:
            (feedback, results) where results is None if rejected.
        """
        current_sql = sql

        for attempt in range(1 + max_retries):
            feedback = self.verify(current_sql)

            if feedback.verdict == Verdict.PASS:
                # Execute on production
                results = self.execute(current_sql)
                return feedback, results

            if feedback.verdict == Verdict.WARNING:
                # Execute but log the warning
                logger.warning("Query passed with warnings: %s", feedback.summary)
                results = self.execute(current_sql)
                return feedback, results

            # REJECT — try revision if callback provided
            if revision_callback and attempt < max_retries:
                llm_feedback = feedback.to_llm_feedback()
                logger.info(
                    "Attempt %d/%d rejected. Requesting revision.",
                    attempt + 1, max_retries + 1,
                )
                revised = revision_callback(current_sql, llm_feedback)
                if revised and revised != current_sql:
                    current_sql = revised
                    continue
                else:
                    break
            else:
                break

        return feedback, None

    def execute(self, sql: str) -> list[tuple]:
        """Execute SQL on the production database."""
        conn = psycopg2.connect(self._config.prod_dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall()
        finally:
            conn.close()

    def invalidate_cache(self, table_name: Optional[str] = None):
        """Invalidate metadata cache."""
        if table_name:
            self._metadata._cache.invalidate(table_name)
        else:
            self._metadata._cache.invalidate_all()
