"""Exec Channel: sandbox execution with DuckDB and semantic validation."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import duckdb

from mocksql.config import MockSQLConfig
from mocksql.metadata import TableInfo, MetadataExtractor
from mocksql.synthesizer import DataSynthesizer
from mocksql.sql_parser import extract_tables

logger = logging.getLogger(__name__)


class ExecSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class ExecIssueType(str, Enum):
    EMPTY_RESULT = "EMPTY_RESULT"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    AGGREGATION_ANOMALY = "AGGREGATION_ANOMALY"
    ROW_EXPLOSION = "ROW_EXPLOSION"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    EXECUTION_ERROR = "EXECUTION_ERROR"


@dataclass
class ExecIssue:
    """A single issue detected from sandbox execution."""
    severity: ExecSeverity
    issue_type: ExecIssueType
    detail: str
    suggestion: str
    column_name: Optional[str] = None
    actual_value: Optional[Any] = None


@dataclass
class ExecChannelResult:
    """Result from Exec Channel analysis."""
    issues: list[ExecIssue] = field(default_factory=list)
    row_count: Optional[int] = None
    column_names: Optional[list[str]] = None
    column_types: Optional[list[str]] = None
    sample_rows: Optional[list[tuple]] = None
    latency_ms: float = 0.0
    synthesis_ms: float = 0.0
    execution_ms: float = 0.0

    @property
    def has_critical(self) -> bool:
        return any(i.severity == ExecSeverity.CRITICAL for i in self.issues)

    @property
    def has_warning(self) -> bool:
        return any(i.severity == ExecSeverity.WARNING for i in self.issues)

    @property
    def is_clean(self) -> bool:
        return len(self.issues) == 0


class ExecChannel:
    """Execute SQL in an ephemeral DuckDB sandbox and validate results."""

    def __init__(
        self,
        config: MockSQLConfig,
        metadata_extractor: MetadataExtractor,
        synthesizer: DataSynthesizer,
    ):
        self._config = config
        self._metadata = metadata_extractor
        self._synthesizer = synthesizer

    def analyze(self, sql: str) -> ExecChannelResult:
        """Run SQL in sandbox and check results."""
        start = time.perf_counter()
        result = ExecChannelResult()

        try:
            # Step 1: Extract referenced tables
            table_names = extract_tables(sql)
            if not table_names:
                result.latency_ms = (time.perf_counter() - start) * 1000
                return result

            # Step 2: Fetch metadata for referenced tables
            tables_info = self._metadata.get_tables_info(table_names)

            # Step 3: Synthesize data
            synth_start = time.perf_counter()
            synthetic_data = self._synthesizer.synthesize(tables_info)
            result.synthesis_ms = (time.perf_counter() - synth_start) * 1000

            # Step 4: Create DuckDB sandbox and load data
            conn = duckdb.connect(":memory:")
            try:
                self._load_data_into_sandbox(conn, tables_info, synthetic_data)

                # Step 5: Execute query
                exec_start = time.perf_counter()
                try:
                    cursor = conn.execute(sql)
                    rows = cursor.fetchall()
                    desc = cursor.description

                    result.execution_ms = (time.perf_counter() - exec_start) * 1000
                    result.row_count = len(rows)
                    result.column_names = [d[0] for d in desc] if desc else []
                    result.column_types = [str(d[1]) for d in desc] if desc else []
                    result.sample_rows = rows[:10]  # Keep first 10 for inspection

                    # Step 6: Validate results
                    self._validate_results(
                        result, tables_info, synthetic_data, sql
                    )

                except duckdb.Error as e:
                    result.execution_ms = (time.perf_counter() - exec_start) * 1000
                    error_msg = str(e)
                    result.issues.append(ExecIssue(
                        severity=ExecSeverity.CRITICAL,
                        issue_type=ExecIssueType.EXECUTION_ERROR,
                        detail=f"SQL execution failed in sandbox: {error_msg}",
                        suggestion=self._suggest_fix_for_error(error_msg),
                    ))
            finally:
                conn.close()

        except Exception as e:
            logger.error("Exec Channel failed: %s", e, exc_info=True)
            result.issues.append(ExecIssue(
                severity=ExecSeverity.WARNING,
                issue_type=ExecIssueType.EXECUTION_ERROR,
                detail=f"Exec Channel internal error: {e}",
                suggestion="This may be a MockSQL internal issue. The query was not validated.",
            ))

        result.latency_ms = (time.perf_counter() - start) * 1000
        return result

    def _load_data_into_sandbox(
        self,
        conn: duckdb.DuckDBPyConnection,
        tables_info: dict[str, TableInfo],
        synthetic_data: dict[str, list[dict]],
    ):
        """Create tables and insert synthetic data into DuckDB sandbox."""
        for table_name, rows in synthetic_data.items():
            if not rows:
                continue

            info = tables_info.get(table_name)
            if not info:
                continue

            # Build CREATE TABLE statement
            col_defs = []
            for col in info.columns:
                duckdb_type = _pg_to_duckdb_type(col.data_type)
                col_defs.append(f'"{col.name}" {duckdb_type}')

            create_sql = f'CREATE TABLE "{table_name}" ({", ".join(col_defs)})'
            try:
                conn.execute(create_sql)
            except duckdb.Error as e:
                logger.warning("Failed to create table %s: %s", table_name, e)
                continue

            # Insert rows
            if rows:
                columns = [col.name for col in info.columns]
                placeholders = ", ".join(["?"] * len(columns))
                col_names = ", ".join(f'"{c}"' for c in columns)
                insert_sql = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'

                for row in rows:
                    values = [row.get(c) for c in columns]
                    try:
                        conn.execute(insert_sql, values)
                    except duckdb.Error:
                        # Skip rows that violate constraints
                        pass

    def _validate_results(
        self,
        result: ExecChannelResult,
        tables_info: dict[str, TableInfo],
        synthetic_data: dict[str, list[dict]],
        sql: str,
    ):
        """Apply semantic validation rules to execution results."""
        # Rule 1: Empty result check
        self._check_empty_result(result, sql, tables_info)

        # Rule 2: Type mismatch check (via column type inspection)
        self._check_type_mismatch(result, sql)

        # Rule 3: Aggregation anomaly check
        self._check_aggregation_anomaly(result, tables_info)

        # Rule 4: Row explosion check
        self._check_row_explosion(result, synthetic_data)

        # Rule 5: Duplicate key check
        self._check_duplicate_keys(result, tables_info, sql)

    def _check_empty_result(
        self,
        result: ExecChannelResult,
        sql: str,
        tables_info: dict[str, TableInfo],
    ):
        """Flag queries that return zero rows when non-empty results are expected."""
        if result.row_count is not None and result.row_count == 0:
            sql_upper = sql.upper()
            # Don't flag if it's a COUNT/aggregate-only query (will return 1 row)
            # Don't flag if query has HAVING (can legitimately be empty)
            if "HAVING" not in sql_upper:
                # Check if query has JOINs — empty results on JOINs likely means
                # wrong join condition
                has_join = "JOIN" in sql_upper
                if has_join:
                    result.issues.append(ExecIssue(
                        severity=ExecSeverity.WARNING,
                        issue_type=ExecIssueType.EMPTY_RESULT,
                        detail=(
                            "Query returned 0 rows on synthetic data. "
                            "This may indicate an incorrect JOIN condition."
                        ),
                        suggestion=(
                            "Check that JOIN conditions reference the correct "
                            "columns with matching data types. Verify that "
                            "foreign key relationships are correct."
                        ),
                    ))
                elif "WHERE" in sql_upper:
                    result.issues.append(ExecIssue(
                        severity=ExecSeverity.INFO,
                        issue_type=ExecIssueType.EMPTY_RESULT,
                        detail=(
                            "Query returned 0 rows on synthetic data. "
                            "WHERE predicates may be overly restrictive."
                        ),
                        suggestion=(
                            "Check WHERE conditions for contradictions or "
                            "overly specific literal values."
                        ),
                    ))

    def _check_type_mismatch(self, result: ExecChannelResult, sql: str):
        """Detect aggregation functions applied to incompatible types."""
        import re
        # Check for SUM/AVG on likely-text columns
        agg_pattern = re.compile(
            r"\b(SUM|AVG)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\)",
            re.IGNORECASE,
        )
        for match in agg_pattern.finditer(sql):
            func = match.group(1).upper()
            col_ref = match.group(2)
            # Check if the result type suggests a type mismatch
            if result.column_types:
                for i, ctype in enumerate(result.column_types):
                    ctype_lower = ctype.lower()
                    if "varchar" in ctype_lower or "text" in ctype_lower:
                        result.issues.append(ExecIssue(
                            severity=ExecSeverity.CRITICAL,
                            issue_type=ExecIssueType.TYPE_MISMATCH,
                            detail=(
                                f"{func}() applied to column '{col_ref}' "
                                f"which appears to be a text type ({ctype})."
                            ),
                            suggestion=(
                                f"Column '{col_ref}' is text/varchar. "
                                f"{func}() requires a numeric column."
                            ),
                            column_name=col_ref,
                        ))

    def _check_aggregation_anomaly(
        self,
        result: ExecChannelResult,
        tables_info: dict[str, TableInfo],
    ):
        """Flag suspicious aggregation results (e.g., negative sums on non-negative columns)."""
        if not result.sample_rows or not result.column_names:
            return

        # Heuristic: column names containing 'amount', 'price', 'total', 'balance',
        # 'quantity', 'count' are expected to be non-negative
        positive_keywords = {"amount", "price", "total", "balance", "quantity", "cost", "revenue"}

        for i, col_name in enumerate(result.column_names):
            col_lower = col_name.lower()
            if any(kw in col_lower for kw in positive_keywords):
                for row in result.sample_rows:
                    val = row[i] if i < len(row) else None
                    if isinstance(val, (int, float)) and val < 0:
                        result.issues.append(ExecIssue(
                            severity=ExecSeverity.WARNING,
                            issue_type=ExecIssueType.AGGREGATION_ANOMALY,
                            detail=(
                                f"Column '{col_name}' has negative value ({val}), "
                                f"but the column name suggests non-negative values."
                            ),
                            suggestion=(
                                f"Check arithmetic expressions involving '{col_name}'. "
                                f"Verify that subtraction or negation is intended."
                            ),
                            column_name=col_name,
                            actual_value=val,
                        ))
                        break  # One warning per column is enough

    def _check_row_explosion(
        self,
        result: ExecChannelResult,
        synthetic_data: dict[str, list[dict]],
    ):
        """Detect result cardinality explosion indicating a Cartesian product."""
        if result.row_count is None:
            return

        # Calculate max expected rows = product of input table sizes / selectivity
        total_input_rows = sum(len(rows) for rows in synthetic_data.values())
        if total_input_rows == 0:
            return

        # If result is much larger than the sum of input sizes, flag it
        explosion_factor = result.row_count / max(1, total_input_rows)
        if explosion_factor > 5.0 and result.row_count > 500:
            result.issues.append(ExecIssue(
                severity=ExecSeverity.WARNING,
                issue_type=ExecIssueType.ROW_EXPLOSION,
                detail=(
                    f"Result has {result.row_count} rows from "
                    f"{total_input_rows} total input rows "
                    f"(explosion factor: {explosion_factor:.1f}x). "
                    f"Possible Cartesian product."
                ),
                suggestion=(
                    "Check JOIN conditions. A missing or incorrect ON clause "
                    "can produce a Cartesian product."
                ),
            ))

    def _check_duplicate_keys(
        self,
        result: ExecChannelResult,
        tables_info: dict[str, TableInfo],
        sql: str,
    ):
        """Detect duplicates in columns that should be unique."""
        if not result.sample_rows or not result.column_names:
            return

        # Collect all PK/unique column names across tables
        unique_cols = set()
        for info in tables_info.values():
            for col in info.columns:
                if col.is_primary_key or col.is_unique:
                    unique_cols.add(col.name.lower())

        # Check result columns that match unique column names
        sql_upper = sql.upper()
        has_group_by = "GROUP BY" in sql_upper
        has_distinct = "DISTINCT" in sql_upper

        if has_group_by or has_distinct:
            return  # GROUP BY / DISTINCT may legitimately deduplicate

        for i, col_name in enumerate(result.column_names):
            if col_name.lower() in unique_cols:
                values = [row[i] for row in result.sample_rows if i < len(row)]
                seen = set()
                has_dup = False
                for v in values:
                    if v is not None:
                        if v in seen:
                            has_dup = True
                            break
                        seen.add(v)

                if has_dup:
                    result.issues.append(ExecIssue(
                        severity=ExecSeverity.WARNING,
                        issue_type=ExecIssueType.DUPLICATE_KEY,
                        detail=(
                            f"Column '{col_name}' is defined as unique/primary key "
                            f"but has duplicate values in the result."
                        ),
                        suggestion=(
                            f"This may indicate a missing GROUP BY clause or "
                            f"an incorrect JOIN that multiplies rows."
                        ),
                        column_name=col_name,
                    ))

    def _suggest_fix_for_error(self, error_msg: str) -> str:
        """Generate a fix suggestion based on the error message."""
        error_lower = error_msg.lower()

        if "does not exist" in error_lower or "not found" in error_lower:
            return "Check table and column names for typos."
        elif "type" in error_lower and ("mismatch" in error_lower or "cast" in error_lower):
            return "Check data types in comparisons and function arguments."
        elif "ambiguous" in error_lower:
            return "Qualify column names with table aliases to resolve ambiguity."
        elif "syntax" in error_lower:
            return "Check SQL syntax near the indicated position."
        elif "division by zero" in error_lower:
            return "Add a NULLIF or CASE to avoid division by zero."
        else:
            return "Review the SQL query for correctness."


def _pg_to_duckdb_type(pg_type: str) -> str:
    """Convert PostgreSQL type name to DuckDB type."""
    mapping = {
        "int2": "SMALLINT",
        "int4": "INTEGER",
        "int8": "BIGINT",
        "smallint": "SMALLINT",
        "integer": "INTEGER",
        "bigint": "BIGINT",
        "float4": "FLOAT",
        "float8": "DOUBLE",
        "real": "FLOAT",
        "double precision": "DOUBLE",
        "double": "DOUBLE",
        "numeric": "DECIMAL",
        "decimal": "DECIMAL",
        "money": "DECIMAL",
        "text": "VARCHAR",
        "varchar": "VARCHAR",
        "char": "VARCHAR",
        "bpchar": "VARCHAR",
        "character varying": "VARCHAR",
        "character": "VARCHAR",
        "name": "VARCHAR",
        "citext": "VARCHAR",
        "bool": "BOOLEAN",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "timestamptz": "TIMESTAMP",
        "timestamp without time zone": "TIMESTAMP",
        "timestamp with time zone": "TIMESTAMP",
        "json": "VARCHAR",
        "jsonb": "VARCHAR",
        "uuid": "VARCHAR",
        "bytea": "BLOB",
        "interval": "INTERVAL",
    }
    return mapping.get(pg_type.lower(), "VARCHAR")
