"""Stats-Aware Data Synthesizer: generate constraint-preserving synthetic data from catalog statistics."""

from __future__ import annotations

import logging
import random
import string
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np

from mocksql.metadata import TableInfo, ColumnInfo
from mocksql.config import MockSQLConfig

logger = logging.getLogger(__name__)

# Mapping from PostgreSQL type names to Python type categories
_NUMERIC_TYPES = {
    "int2", "int4", "int8", "smallint", "integer", "bigint",
    "float4", "float8", "real", "double precision", "double",
    "numeric", "decimal", "money",
}
_TEXT_TYPES = {
    "text", "varchar", "char", "bpchar", "character varying", "character",
    "name", "citext",
}
_DATE_TYPES = {"date"}
_TIMESTAMP_TYPES = {"timestamp", "timestamptz", "timestamp without time zone",
                     "timestamp with time zone"}
_BOOL_TYPES = {"bool", "boolean"}


class DataSynthesizer:
    """Generate synthetic data for sandbox execution based on catalog statistics."""

    def __init__(self, config: MockSQLConfig):
        self._config = config
        self._rng = np.random.default_rng(seed=42)

    def synthesize(
        self,
        tables_info: dict[str, TableInfo],
    ) -> dict[str, list[dict[str, Any]]]:
        """Generate synthetic data for all tables, respecting FK dependencies.

        Returns: {table_name: [row_dict, ...]}
        """
        # Determine generation order: parent tables (referenced by FK) first
        order = self._resolve_dependency_order(tables_info)
        generated: dict[str, list[dict[str, Any]]] = {}

        for table_name in order:
            info = tables_info[table_name]
            n_rows = self._compute_row_count(info)
            rows = self._generate_table_data(info, n_rows, generated)
            generated[table_name] = rows

        return generated

    def _resolve_dependency_order(self, tables_info: dict[str, TableInfo]) -> list[str]:
        """Topological sort: tables referenced by FKs come first."""
        # Build adjacency: if table A has FK -> table B, then B must come before A
        all_tables = set(tables_info.keys())
        deps: dict[str, set[str]] = {t: set() for t in all_tables}

        for tname, tinfo in tables_info.items():
            for col in tinfo.columns:
                if col.foreign_key:
                    ref_table = col.foreign_key[0].lower()
                    if ref_table in all_tables:
                        deps[tname].add(ref_table)

        # Kahn's algorithm
        in_degree = {t: len(d) for t, d in deps.items()}
        queue = [t for t, d in in_degree.items() if d == 0]
        order = []

        while queue:
            node = queue.pop(0)
            order.append(node)
            for t, d in deps.items():
                if node in d:
                    d.remove(node)
                    in_degree[t] -= 1
                    if in_degree[t] == 0:
                        queue.append(t)

        # Add any remaining (circular deps — break cycles)
        for t in all_tables:
            if t not in order:
                order.append(t)

        return order

    def _compute_row_count(self, info: TableInfo) -> int:
        """Determine how many rows to generate: max(default, ndv_multiplier * NDV_max)."""
        ndv_max = info.ndv_max
        return max(
            self._config.default_rows_per_table,
            self._config.ndv_multiplier * ndv_max,
        )

    def _generate_table_data(
        self,
        info: TableInfo,
        n_rows: int,
        generated: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Generate n_rows of synthetic data for a single table."""
        rows: list[dict[str, Any]] = []

        # Pre-generate primary key values
        pk_values: dict[str, list[Any]] = {}
        for pk_col_name in info.primary_keys:
            col = info.get_column(pk_col_name)
            if col:
                pk_values[pk_col_name] = self._generate_pk_values(col, n_rows)

        for i in range(n_rows):
            row = {}
            for col in info.columns:
                if col.name in pk_values:
                    row[col.name] = pk_values[col.name][i]
                elif col.foreign_key:
                    row[col.name] = self._generate_fk_value(col, generated)
                else:
                    row[col.name] = self._generate_column_value(col, i, n_rows)
            rows.append(row)

        return rows

    def _generate_pk_values(self, col: ColumnInfo, n_rows: int) -> list[Any]:
        """Generate monotonically increasing PK values."""
        dtype = col.data_type.lower()
        if dtype in _NUMERIC_TYPES:
            return list(range(1, n_rows + 1))
        elif dtype in _TEXT_TYPES:
            return [f"{col.name}_{i}" for i in range(1, n_rows + 1)]
        else:
            return list(range(1, n_rows + 1))

    def _generate_fk_value(
        self,
        col: ColumnInfo,
        generated: dict[str, list[dict[str, Any]]],
    ) -> Any:
        """Sample a FK value from the referenced table's already-generated PK values."""
        if col.foreign_key is None:
            return None

        ref_table, ref_col = col.foreign_key
        ref_table = ref_table.lower()

        if ref_table in generated and generated[ref_table]:
            ref_rows = generated[ref_table]
            ref_values = [r.get(ref_col) for r in ref_rows if r.get(ref_col) is not None]
            if ref_values:
                # Inject null with probability null_frac
                if col.null_frac > 0 and random.random() < col.null_frac:
                    return None
                return random.choice(ref_values)

        # Fallback: generate a compatible value
        return self._generate_column_value(col, 0, 1)

    def _generate_column_value(self, col: ColumnInfo, row_idx: int, n_rows: int) -> Any:
        """Generate a single column value based on statistics."""
        # Null injection
        if col.is_nullable and col.null_frac > 0:
            if random.random() < col.null_frac:
                return None

        dtype = col.data_type.lower()

        # Use most_common_vals if available
        if col.most_common_vals and col.most_common_freqs:
            if random.random() < sum(col.most_common_freqs):
                return self._sample_from_mcv(col)

        # Use histogram bounds if available
        if col.histogram_bounds and len(col.histogram_bounds) >= 2:
            return self._sample_from_histogram(col, dtype)

        # Fallback: type-based generation
        return self._generate_by_type(col, dtype, row_idx, n_rows)

    def _sample_from_mcv(self, col: ColumnInfo) -> Any:
        """Sample from most_common_vals with given frequencies."""
        vals = col.most_common_vals
        freqs = col.most_common_freqs
        if not vals or not freqs:
            return None

        # Normalize frequencies
        total = sum(freqs)
        if total <= 0:
            return random.choice(vals)

        probs = [f / total for f in freqs]
        idx = self._rng.choice(len(vals), p=probs)
        val = vals[idx]

        # Cast to appropriate Python type
        return self._cast_value(val, col.data_type.lower())

    def _sample_from_histogram(self, col: ColumnInfo, dtype: str) -> Any:
        """Sample from histogram bounds with uniform interpolation within buckets."""
        bounds = col.histogram_bounds
        if not bounds or len(bounds) < 2:
            return self._generate_by_type(col, dtype, 0, 1)

        # Pick a random bucket
        bucket_idx = random.randint(0, len(bounds) - 2)
        lo = bounds[bucket_idx]
        hi = bounds[bucket_idx + 1]

        if dtype in _NUMERIC_TYPES:
            try:
                lo_f = float(lo)
                hi_f = float(hi)
                val = lo_f + random.random() * (hi_f - lo_f)
                if dtype in ("int2", "int4", "int8", "smallint", "integer", "bigint"):
                    return int(round(val))
                return round(val, 2)
            except (ValueError, TypeError):
                return self._generate_by_type(col, dtype, 0, 1)

        elif dtype in _DATE_TYPES or dtype in _TIMESTAMP_TYPES:
            try:
                lo_dt = _parse_datetime(str(lo))
                hi_dt = _parse_datetime(str(hi))
                if lo_dt and hi_dt:
                    delta = (hi_dt - lo_dt).total_seconds()
                    offset = random.random() * delta
                    result = lo_dt + timedelta(seconds=offset)
                    if dtype in _DATE_TYPES:
                        return result.date()
                    return result
            except Exception:
                pass
            return self._generate_by_type(col, dtype, 0, 1)

        elif dtype in _TEXT_TYPES:
            # For text, return one of the bounds
            return random.choice([lo, hi])

        return self._generate_by_type(col, dtype, 0, 1)

    def _generate_by_type(
        self, col: ColumnInfo, dtype: str, row_idx: int, n_rows: int
    ) -> Any:
        """Fallback: generate value based purely on data type."""
        if dtype in _NUMERIC_TYPES:
            if dtype in ("int2", "int4", "int8", "smallint", "integer", "bigint"):
                return random.randint(1, max(1000, n_rows * 10))
            return round(random.uniform(0.01, 10000.0), 2)

        elif dtype in _TEXT_TYPES:
            length = min(10, max(3, n_rows // 10))
            return "".join(random.choices(string.ascii_lowercase, k=length))

        elif dtype in _DATE_TYPES:
            base = datetime(2020, 1, 1)
            offset = timedelta(days=random.randint(0, 1500))
            return (base + offset).date()

        elif dtype in _TIMESTAMP_TYPES:
            base = datetime(2020, 1, 1)
            offset = timedelta(seconds=random.randint(0, 1500 * 86400))
            return base + offset

        elif dtype in _BOOL_TYPES:
            return random.choice([True, False])

        else:
            # Unknown type — return a string
            return f"val_{row_idx}"

    def _cast_value(self, val: Any, dtype: str) -> Any:
        """Cast a string value from pg_stats to the appropriate Python type."""
        if val is None:
            return None
        try:
            if dtype in _NUMERIC_TYPES:
                if dtype in ("int2", "int4", "int8", "smallint", "integer", "bigint"):
                    return int(float(str(val)))
                return float(str(val))
            elif dtype in _BOOL_TYPES:
                s = str(val).lower()
                return s in ("t", "true", "1", "yes")
            elif dtype in _DATE_TYPES:
                dt = _parse_datetime(str(val))
                return dt.date() if dt else str(val)
            elif dtype in _TIMESTAMP_TYPES:
                return _parse_datetime(str(val)) or str(val)
            else:
                return str(val)
        except (ValueError, TypeError):
            return str(val)


def _parse_datetime(s: str) -> Optional[datetime]:
    """Try to parse a datetime string in common formats."""
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None
