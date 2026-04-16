"""Plan Channel: EXPLAIN-based performance risk detection on production database."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from mocksql.config import MockSQLConfig

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class RiskType(str, Enum):
    CARTESIAN_PRODUCT = "CARTESIAN_PRODUCT"
    ROW_EXPLOSION = "ROW_EXPLOSION"
    FULL_TABLE_SCAN = "FULL_TABLE_SCAN"
    SORT_OVERFLOW = "SORT_OVERFLOW"
    HIGH_COST = "HIGH_COST"


@dataclass
class PlanIssue:
    """A single risk detected from the execution plan."""
    severity: Severity
    risk_type: RiskType
    detail: str
    suggestion: str
    node_type: str = ""
    estimated_rows: Optional[int] = None
    estimated_cost: Optional[float] = None
    table_name: Optional[str] = None


@dataclass
class PlanChannelResult:
    """Result from Plan Channel analysis."""
    issues: list[PlanIssue] = field(default_factory=list)
    raw_plan: Optional[dict] = None
    latency_ms: float = 0.0

    @property
    def has_critical(self) -> bool:
        return any(i.severity == Severity.CRITICAL for i in self.issues)

    @property
    def has_warning(self) -> bool:
        return any(i.severity == Severity.WARNING for i in self.issues)

    @property
    def is_clean(self) -> bool:
        return len(self.issues) == 0


class PlanChannel:
    """Analyze query execution plans from production database to detect performance risks."""

    def __init__(self, config: MockSQLConfig):
        self._config = config
        self._dsn = config.prod_dsn

    def analyze(self, sql: str) -> PlanChannelResult:
        """Run EXPLAIN on production DB and analyze the plan."""
        import time
        start = time.perf_counter()

        result = PlanChannelResult()

        try:
            conn = psycopg2.connect(self._dsn)
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    # EXPLAIN (FORMAT JSON) — read-only, no data access
                    cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                    plan_rows = cur.fetchall()
                    if plan_rows:
                        plan_json = plan_rows[0][0]
                        if isinstance(plan_json, list) and len(plan_json) > 0:
                            result.raw_plan = plan_json[0]
                            plan_tree = plan_json[0].get("Plan", {})
                            self._analyze_plan_node(plan_tree, result, sql)
            finally:
                conn.close()
        except psycopg2.Error as e:
            logger.error("Plan Channel EXPLAIN failed: %s", e)
            result.issues.append(PlanIssue(
                severity=Severity.WARNING,
                risk_type=RiskType.HIGH_COST,
                detail=f"EXPLAIN failed: {e}",
                suggestion="Check SQL syntax and table references.",
            ))

        result.latency_ms = (time.perf_counter() - start) * 1000
        return result

    def _analyze_plan_node(
        self, node: dict, result: PlanChannelResult, sql: str, depth: int = 0
    ):
        """Recursively analyze plan tree nodes."""
        if not isinstance(node, dict):
            return

        node_type = node.get("Node Type", "")
        plan_rows = node.get("Plan Rows", 0)
        total_cost = node.get("Total Cost", 0)
        relation_name = node.get("Relation Name", "")

        # --- Check 1: Row explosion ---
        if plan_rows > self._config.plan_row_explosion_threshold:
            result.issues.append(PlanIssue(
                severity=Severity.CRITICAL,
                risk_type=RiskType.ROW_EXPLOSION,
                detail=(
                    f"Plan node '{node_type}' estimates {plan_rows:,} rows"
                    + (f" on table '{relation_name}'" if relation_name else "")
                    + f", exceeding threshold of "
                    f"{self._config.plan_row_explosion_threshold:,}."
                ),
                suggestion=(
                    "Add WHERE clauses or LIMIT to restrict the result set. "
                    "Consider adding appropriate indexes."
                ),
                node_type=node_type,
                estimated_rows=plan_rows,
                estimated_cost=total_cost,
                table_name=relation_name or None,
            ))

        # --- Check 2: Cartesian product ---
        if node_type == "Nested Loop":
            join_filter = node.get("Join Filter")
            # A nested loop without any join filter is a Cartesian product
            if not join_filter:
                # Check if there are inner-side index conditions (parameterized)
                inner = node.get("Plans", [{}])
                has_index_cond = any(
                    p.get("Index Cond") or p.get("Filter")
                    for p in inner if isinstance(p, dict)
                )
                if not has_index_cond and plan_rows > 10000:
                    result.issues.append(PlanIssue(
                        severity=Severity.CRITICAL,
                        risk_type=RiskType.CARTESIAN_PRODUCT,
                        detail=(
                            f"Nested Loop join without join condition detected. "
                            f"Estimated {plan_rows:,} rows."
                        ),
                        suggestion=(
                            "Add a JOIN condition (e.g., ON t1.id = t2.t1_id) "
                            "to avoid a Cartesian product."
                        ),
                        node_type=node_type,
                        estimated_rows=plan_rows,
                    ))

        # --- Check 3: Full table scan without LIMIT ---
        if node_type == "Seq Scan" and relation_name:
            if plan_rows > self._config.plan_full_scan_row_threshold:
                if not _has_limit_above(node, sql):
                    result.issues.append(PlanIssue(
                        severity=Severity.WARNING,
                        risk_type=RiskType.FULL_TABLE_SCAN,
                        detail=(
                            f"Sequential scan on '{relation_name}' "
                            f"({plan_rows:,} rows) without LIMIT."
                        ),
                        suggestion=(
                            f"Add a WHERE clause to filter '{relation_name}' "
                            f"or add LIMIT to restrict output rows."
                        ),
                        node_type=node_type,
                        estimated_rows=plan_rows,
                        table_name=relation_name,
                    ))

        # --- Check 4: External sort (disk-based) ---
        if node_type == "Sort":
            sort_method = node.get("Sort Method", "")
            if "external" in sort_method.lower() or "disk" in sort_method.lower():
                result.issues.append(PlanIssue(
                    severity=Severity.WARNING,
                    risk_type=RiskType.SORT_OVERFLOW,
                    detail=(
                        f"Sort operation uses external (disk-based) sorting. "
                        f"Sort method: {sort_method}. "
                        f"This may cause significant I/O overhead."
                    ),
                    suggestion=(
                        "Consider increasing work_mem, adding an index "
                        "on the sort column, or reducing the result set size."
                    ),
                    node_type=node_type,
                    estimated_rows=plan_rows,
                ))

        # --- Check 5: Total cost threshold (only at root) ---
        if depth == 0 and total_cost > self._config.plan_total_cost_threshold:
            result.issues.append(PlanIssue(
                severity=Severity.WARNING,
                risk_type=RiskType.HIGH_COST,
                detail=(
                    f"Total estimated cost ({total_cost:.1f}) exceeds threshold "
                    f"({self._config.plan_total_cost_threshold:.1f})."
                ),
                suggestion=(
                    "Review the query for optimization opportunities: "
                    "add indexes, restrict result set, or simplify joins."
                ),
                node_type=node_type,
                estimated_cost=total_cost,
            ))

        # Recurse into child plans
        for child in node.get("Plans", []):
            self._analyze_plan_node(child, result, sql, depth + 1)


def _has_limit_above(node: dict, sql: str) -> bool:
    """Heuristic check: does the SQL have a LIMIT clause?"""
    return bool(re.search(r"\bLIMIT\b", sql, re.IGNORECASE))
