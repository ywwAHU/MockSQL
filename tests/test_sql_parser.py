"""Tests for SQL parser."""

import pytest
from mocksql.sql_parser import extract_tables, get_query_type, has_limit, has_where


class TestExtractTables:
    def test_single_table(self):
        sql = "SELECT * FROM orders"
        assert extract_tables(sql) == ["orders"]

    def test_two_tables_join(self):
        sql = "SELECT o.id, c.name FROM orders o JOIN customer c ON o.custkey = c.custkey"
        tables = extract_tables(sql)
        assert "orders" in tables
        assert "customer" in tables

    def test_implicit_join(self):
        sql = "SELECT * FROM orders, customer WHERE orders.custkey = customer.custkey"
        tables = extract_tables(sql)
        assert "orders" in tables
        assert "customer" in tables

    def test_subquery(self):
        sql = "SELECT * FROM orders WHERE custkey IN (SELECT custkey FROM customer)"
        tables = extract_tables(sql)
        assert "orders" in tables
        assert "customer" in tables

    def test_multiple_joins(self):
        sql = """
        SELECT c.name, o.total, l.price
        FROM customer c
        JOIN orders o ON c.custkey = o.custkey
        JOIN lineitem l ON o.orderkey = l.orderkey
        """
        tables = extract_tables(sql)
        assert len(tables) == 3
        assert "customer" in tables
        assert "orders" in tables
        assert "lineitem" in tables

    def test_left_join(self):
        sql = "SELECT * FROM orders LEFT JOIN customer ON orders.custkey = customer.custkey"
        tables = extract_tables(sql)
        assert "orders" in tables
        assert "customer" in tables


class TestQueryType:
    def test_select(self):
        assert get_query_type("SELECT * FROM orders") == "SELECT"

    def test_insert(self):
        assert get_query_type("INSERT INTO orders VALUES (1, 2)") == "INSERT"

    def test_update(self):
        assert get_query_type("UPDATE orders SET total = 100") == "UPDATE"

    def test_delete(self):
        assert get_query_type("DELETE FROM orders WHERE id = 1") == "DELETE"


class TestHasLimit:
    def test_with_limit(self):
        assert has_limit("SELECT * FROM orders LIMIT 10") is True

    def test_without_limit(self):
        assert has_limit("SELECT * FROM orders") is False


class TestHasWhere:
    def test_with_where(self):
        assert has_where("SELECT * FROM orders WHERE id > 5") is True

    def test_without_where(self):
        assert has_where("SELECT * FROM orders") is False
