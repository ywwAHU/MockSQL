"""Metadata extractor: fetch schema and column statistics from production database."""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


@dataclass
class ColumnInfo:
    """Column-level schema and statistics."""
    name: str
    data_type: str
    is_nullable: bool = True
    is_primary_key: bool = False
    is_unique: bool = False
    # Foreign key: (referenced_table, referenced_column)
    foreign_key: Optional[tuple[str, str]] = None

    # Statistics from pg_stats
    n_distinct: Optional[float] = None  # negative means fraction
    most_common_vals: Optional[list[Any]] = None
    most_common_freqs: Optional[list[float]] = None
    histogram_bounds: Optional[list[Any]] = None
    null_frac: float = 0.0


@dataclass
class TableInfo:
    """Table-level schema and statistics."""
    name: str
    schema_name: str = "public"
    columns: list[ColumnInfo] = field(default_factory=list)
    row_count: int = 0  # estimated from pg_class
    primary_keys: list[str] = field(default_factory=list)

    def get_column(self, col_name: str) -> Optional[ColumnInfo]:
        for col in self.columns:
            if col.name.lower() == col_name.lower():
                return col
        return None

    @property
    def ndv_max(self) -> int:
        """Maximum number of distinct values across all columns."""
        max_ndv = 1
        for col in self.columns:
            if col.n_distinct is not None:
                if col.n_distinct > 0:
                    ndv = int(col.n_distinct)
                else:
                    # Negative means fraction of rows
                    ndv = max(1, int(abs(col.n_distinct) * self.row_count))
                max_ndv = max(max_ndv, ndv)
        return max_ndv


class MetadataCache:
    """TTL-based cache for table metadata."""

    def __init__(self, ttl_seconds: int = 300):
        self._cache: dict[str, tuple[float, TableInfo]] = {}
        self._ttl = ttl_seconds

    def get(self, table_name: str) -> Optional[TableInfo]:
        key = table_name.lower()
        if key in self._cache:
            ts, info = self._cache[key]
            if time.time() - ts < self._ttl:
                return info
            del self._cache[key]
        return None

    def put(self, table_name: str, info: TableInfo):
        self._cache[table_name.lower()] = (time.time(), info)

    def invalidate(self, table_name: str):
        self._cache.pop(table_name.lower(), None)

    def invalidate_all(self):
        self._cache.clear()


class MetadataExtractor:
    """Extract schema and statistics from PostgreSQL."""

    def __init__(self, dsn: str, cache_ttl: int = 300):
        self._dsn = dsn
        self._cache = MetadataCache(cache_ttl)

    def _get_conn(self):
        return psycopg2.connect(self._dsn)

    def get_table_info(self, table_name: str, schema: str = "public") -> TableInfo:
        """Get table info with caching."""
        cached = self._cache.get(table_name)
        if cached is not None:
            return cached

        info = self._fetch_table_info(table_name, schema)
        self._cache.put(table_name, info)
        return info

    def get_tables_info(self, table_names: list[str], schema: str = "public") -> dict[str, TableInfo]:
        """Get info for multiple tables."""
        result = {}
        for name in table_names:
            result[name] = self.get_table_info(name, schema)
        return result

    def _fetch_table_info(self, table_name: str, schema: str = "public") -> TableInfo:
        """Fetch complete table info from database catalog."""
        conn = self._get_conn()
        try:
            info = TableInfo(name=table_name, schema_name=schema)

            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                # 1. Get estimated row count from pg_class
                cur.execute("""
                    SELECT reltuples::bigint AS row_count
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relname = %s AND n.nspname = %s
                """, (table_name, schema))
                row = cur.fetchone()
                info.row_count = max(0, int(row["row_count"])) if row else 0

                # 2. Get columns from information_schema
                cur.execute("""
                    SELECT column_name, data_type, is_nullable,
                           column_default, udt_name
                    FROM information_schema.columns
                    WHERE table_name = %s AND table_schema = %s
                    ORDER BY ordinal_position
                """, (table_name, schema))
                columns_raw = cur.fetchall()

                # 3. Get primary keys
                cur.execute("""
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid
                        AND a.attnum = ANY(i.indkey)
                    JOIN pg_class c ON c.oid = i.indrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relname = %s AND n.nspname = %s AND i.indisprimary
                """, (table_name, schema))
                pk_cols = {r["attname"] for r in cur.fetchall()}
                info.primary_keys = sorted(pk_cols)

                # 4. Get unique constraints
                cur.execute("""
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid
                        AND a.attnum = ANY(i.indkey)
                    JOIN pg_class c ON c.oid = i.indrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relname = %s AND n.nspname = %s
                        AND i.indisunique AND NOT i.indisprimary
                """, (table_name, schema))
                unique_cols = {r["attname"] for r in cur.fetchall()}

                # 5. Get foreign keys
                cur.execute("""
                    SELECT
                        kcu.column_name,
                        ccu.table_name AS referenced_table,
                        ccu.column_name AS referenced_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                        ON ccu.constraint_name = tc.constraint_name
                        AND ccu.table_schema = tc.table_schema
                    WHERE tc.table_name = %s
                        AND tc.table_schema = %s
                        AND tc.constraint_type = 'FOREIGN KEY'
                """, (table_name, schema))
                fk_map = {}
                for r in cur.fetchall():
                    fk_map[r["column_name"]] = (
                        r["referenced_table"], r["referenced_column"]
                    )

                # 6. Get column statistics from pg_stats
                cur.execute("""
                    SELECT attname, n_distinct, null_frac,
                           most_common_vals::text, most_common_freqs::text,
                           histogram_bounds::text
                    FROM pg_stats
                    WHERE tablename = %s AND schemaname = %s
                """, (table_name, schema))
                stats_map = {}
                for r in cur.fetchall():
                    stats_map[r["attname"]] = r

                # 7. Build ColumnInfo objects
                for col_raw in columns_raw:
                    col_name = col_raw["column_name"]
                    col = ColumnInfo(
                        name=col_name,
                        data_type=col_raw["udt_name"] or col_raw["data_type"],
                        is_nullable=(col_raw["is_nullable"] == "YES"),
                        is_primary_key=(col_name in pk_cols),
                        is_unique=(col_name in unique_cols or col_name in pk_cols),
                        foreign_key=fk_map.get(col_name),
                    )

                    # Attach statistics
                    if col_name in stats_map:
                        s = stats_map[col_name]
                        col.n_distinct = s["n_distinct"]
                        col.null_frac = s["null_frac"] or 0.0
                        col.most_common_vals = _parse_pg_array(s["most_common_vals"])
                        col.most_common_freqs = _parse_pg_float_array(s["most_common_freqs"])
                        col.histogram_bounds = _parse_pg_array(s["histogram_bounds"])

                    info.columns.append(col)

            return info
        finally:
            conn.close()


def _parse_pg_array(val: Optional[str]) -> Optional[list]:
    """Parse a PostgreSQL array literal like {a,b,c} into a Python list."""
    if not val:
        return None
    val = val.strip()
    if val.startswith("{") and val.endswith("}"):
        inner = val[1:-1]
        if not inner:
            return []
        # Handle quoted values
        items = []
        in_quote = False
        current = []
        for ch in inner:
            if ch == '"':
                in_quote = not in_quote
            elif ch == ',' and not in_quote:
                items.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            items.append("".join(current).strip())
        return items
    return None


def _parse_pg_float_array(val: Optional[str]) -> Optional[list[float]]:
    """Parse a PostgreSQL float array literal."""
    items = _parse_pg_array(val)
    if items is None:
        return None
    try:
        return [float(x) for x in items]
    except (ValueError, TypeError):
        return None
