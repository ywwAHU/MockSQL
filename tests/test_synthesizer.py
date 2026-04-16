"""Tests for synthesizer and exec channel using DuckDB only (no PostgreSQL required)."""

import pytest
import duckdb

from mocksql.config import MockSQLConfig
from mocksql.metadata import TableInfo, ColumnInfo
from mocksql.synthesizer import DataSynthesizer


@pytest.fixture
def config():
    return MockSQLConfig(default_rows_per_table=50)


@pytest.fixture
def synthesizer(config):
    return DataSynthesizer(config)


class TestDataSynthesizer:
    def test_single_table_synthesis(self, synthesizer):
        tables_info = {
            "orders": TableInfo(
                name="orders",
                row_count=1000,
                primary_keys=["id"],
                columns=[
                    ColumnInfo(name="id", data_type="int4", is_primary_key=True),
                    ColumnInfo(name="total", data_type="numeric"),
                    ColumnInfo(name="status", data_type="varchar"),
                    ColumnInfo(name="created", data_type="date"),
                ],
            ),
        }

        data = synthesizer.synthesize(tables_info)
        assert "orders" in data
        assert len(data["orders"]) >= 50
        # PKs should be unique
        ids = [r["id"] for r in data["orders"]]
        assert len(ids) == len(set(ids))

    def test_fk_relationship(self, synthesizer):
        tables_info = {
            "customer": TableInfo(
                name="customer",
                row_count=100,
                primary_keys=["c_id"],
                columns=[
                    ColumnInfo(name="c_id", data_type="int4", is_primary_key=True),
                    ColumnInfo(name="c_name", data_type="varchar"),
                ],
            ),
            "orders": TableInfo(
                name="orders",
                row_count=1000,
                primary_keys=["o_id"],
                columns=[
                    ColumnInfo(name="o_id", data_type="int4", is_primary_key=True),
                    ColumnInfo(
                        name="o_custkey", data_type="int4",
                        foreign_key=("customer", "c_id"),
                    ),
                    ColumnInfo(name="o_total", data_type="numeric"),
                ],
            ),
        }

        data = synthesizer.synthesize(tables_info)
        assert "customer" in data
        assert "orders" in data

        # FK values should be subset of PK values
        customer_ids = {r["c_id"] for r in data["customer"]}
        order_fks = {r["o_custkey"] for r in data["orders"] if r["o_custkey"] is not None}
        assert order_fks.issubset(customer_ids), "FK values must reference valid PKs"

    def test_join_produces_results(self, synthesizer):
        """The key property: equi-join on PK-FK should produce non-empty results."""
        tables_info = {
            "customer": TableInfo(
                name="customer",
                row_count=100,
                primary_keys=["c_id"],
                columns=[
                    ColumnInfo(name="c_id", data_type="int4", is_primary_key=True),
                    ColumnInfo(name="c_name", data_type="varchar"),
                ],
            ),
            "orders": TableInfo(
                name="orders",
                row_count=1000,
                primary_keys=["o_id"],
                columns=[
                    ColumnInfo(name="o_id", data_type="int4", is_primary_key=True),
                    ColumnInfo(
                        name="o_custkey", data_type="int4",
                        foreign_key=("customer", "c_id"),
                    ),
                    ColumnInfo(name="o_total", data_type="numeric"),
                ],
            ),
        }

        data = synthesizer.synthesize(tables_info)

        # Load into DuckDB and verify JOIN
        conn = duckdb.connect(":memory:")

        conn.execute("""
            CREATE TABLE customer (c_id INTEGER, c_name VARCHAR)
        """)
        conn.execute("""
            CREATE TABLE orders (o_id INTEGER, o_custkey INTEGER, o_total DECIMAL)
        """)

        for row in data["customer"]:
            conn.execute(
                "INSERT INTO customer VALUES (?, ?)",
                [row["c_id"], row["c_name"]],
            )
        for row in data["orders"]:
            conn.execute(
                "INSERT INTO orders VALUES (?, ?, ?)",
                [row["o_id"], row["o_custkey"], row["o_total"]],
            )

        # Equi-join should produce results
        result = conn.execute("""
            SELECT c.c_name, o.o_total
            FROM customer c JOIN orders o ON c.c_id = o.o_custkey
        """).fetchall()

        assert len(result) > 0, "PK-FK join must produce non-empty results"
        conn.close()

    def test_null_injection(self, synthesizer):
        """Test that null_frac is respected."""
        tables_info = {
            "test": TableInfo(
                name="test",
                row_count=100,
                primary_keys=["id"],
                columns=[
                    ColumnInfo(name="id", data_type="int4", is_primary_key=True),
                    ColumnInfo(
                        name="nullable_col", data_type="varchar",
                        is_nullable=True, null_frac=0.5,
                    ),
                ],
            ),
        }

        data = synthesizer.synthesize(tables_info)
        null_count = sum(1 for r in data["test"] if r["nullable_col"] is None)
        total = len(data["test"])
        # Should have some nulls (with 0.5 probability, expect ~25%)
        assert null_count > 0, "Should have some null values"
        assert null_count < total, "Should not be all nulls"

    def test_histogram_sampling(self, synthesizer):
        """Test that histogram bounds are used for value generation."""
        tables_info = {
            "test": TableInfo(
                name="test",
                row_count=1000,
                primary_keys=["id"],
                columns=[
                    ColumnInfo(
                        name="id", data_type="int4", is_primary_key=True,
                    ),
                    ColumnInfo(
                        name="price", data_type="numeric",
                        histogram_bounds=["10.0", "50.0", "100.0", "500.0", "1000.0"],
                    ),
                ],
            ),
        }

        data = synthesizer.synthesize(tables_info)
        prices = [r["price"] for r in data["test"] if r["price"] is not None]
        assert len(prices) > 0
        # Values should be within histogram range
        assert min(prices) >= 10.0
        assert max(prices) <= 1000.0
