"""Hazardous SQL generator: create benchmark queries for detection evaluation (Section 5.2)."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Optional


@dataclass
class HazardousQuery:
    """A hazardous query with its metadata."""
    sql: str
    category: str  # CARTESIAN, FULL_SCAN, EMPTY_JOIN, AGG_ERROR
    description: str
    expected_detection_channel: str  # "plan", "exec", "both"


def generate_cartesian_products(n: int = 50) -> list[HazardousQuery]:
    """Generate queries with missing/incorrect JOIN conditions."""
    queries = []
    templates = [
        # Missing ON clause entirely
        (
            "SELECT o.o_orderkey, c.c_name FROM orders o, customer c",
            "Cross join between orders and customer (implicit Cartesian)",
        ),
        (
            "SELECT l.l_orderkey, p.p_name FROM lineitem l, part p",
            "Cross join between lineitem and part (implicit Cartesian)",
        ),
        (
            "SELECT o.o_orderkey, l.l_extendedprice, s.s_name "
            "FROM orders o, lineitem l, supplier s",
            "Three-way Cartesian product",
        ),
        # JOIN with wrong condition
        (
            "SELECT o.o_orderkey, c.c_name FROM orders o "
            "JOIN customer c ON o.o_orderkey = c.c_custkey",
            "Join on wrong columns (orderkey vs custkey)",
        ),
        # CROSS JOIN explicit
        (
            "SELECT n.n_name, r.r_name FROM nation n CROSS JOIN region r "
            "CROSS JOIN supplier s",
            "Explicit three-way CROSS JOIN",
        ),
    ]

    for i in range(n):
        t = templates[i % len(templates)]
        # Add variation with different columns in SELECT
        select_suffix = f" -- variation_{i}"
        queries.append(HazardousQuery(
            sql=t[0] + select_suffix,
            category="CARTESIAN",
            description=t[1],
            expected_detection_channel="plan",
        ))

    return queries[:n]


def generate_full_scans(n: int = 50) -> list[HazardousQuery]:
    """Generate queries with unrestricted full table scans."""
    queries = []
    templates = [
        (
            "SELECT * FROM lineitem",
            "Full scan on lineitem (60M rows at SF=10) without WHERE or LIMIT",
        ),
        (
            "SELECT * FROM orders ORDER BY o_totalprice DESC",
            "Full scan + sort on orders without LIMIT",
        ),
        (
            "SELECT l_extendedprice, l_quantity FROM lineitem "
            "ORDER BY l_extendedprice",
            "Full scan + sort on lineitem without WHERE",
        ),
        (
            "SELECT o_orderkey, o_totalprice FROM orders "
            "WHERE o_totalprice > 0",
            "Trivially true WHERE (almost all rows match)",
        ),
        (
            "SELECT c_name, c_acctbal FROM customer "
            "ORDER BY c_acctbal DESC",
            "Full scan on customer without LIMIT",
        ),
    ]

    for i in range(n):
        t = templates[i % len(templates)]
        queries.append(HazardousQuery(
            sql=t[0] + f" -- scan_var_{i}",
            category="FULL_SCAN",
            description=t[1],
            expected_detection_channel="plan",
        ))

    return queries[:n]


def generate_empty_joins(n: int = 50) -> list[HazardousQuery]:
    """Generate queries with join conditions that produce empty results."""
    queries = []
    templates = [
        (
            "SELECT o.o_orderkey, c.c_name FROM orders o "
            "JOIN customer c ON o.o_orderkey = c.c_nationkey",
            "Join on semantically unrelated columns (orderkey vs nationkey)",
        ),
        (
            "SELECT l.l_orderkey, p.p_name FROM lineitem l "
            "JOIN part p ON l.l_linenumber = p.p_partkey",
            "Join on mismatched columns (linenumber vs partkey)",
        ),
        (
            "SELECT o.o_orderkey, s.s_name FROM orders o "
            "JOIN supplier s ON o.o_shippriority = s.s_suppkey",
            "Join on shippriority = suppkey (type compatible but semantically wrong)",
        ),
        (
            "SELECT c.c_name, n.n_name FROM customer c "
            "JOIN nation n ON c.c_custkey = n.n_nationkey",
            "Join on custkey = nationkey (different domains)",
        ),
        (
            "SELECT o.o_orderkey FROM orders o "
            "JOIN lineitem l ON o.o_orderkey = l.l_suppkey",
            "Join on orderkey = suppkey (wrong reference)",
        ),
    ]

    for i in range(n):
        t = templates[i % len(templates)]
        queries.append(HazardousQuery(
            sql=t[0] + f" -- empty_var_{i}",
            category="EMPTY_JOIN",
            description=t[1],
            expected_detection_channel="exec",
        ))

    return queries[:n]


def generate_aggregation_errors(n: int = 50) -> list[HazardousQuery]:
    """Generate queries with aggregation logic errors."""
    queries = []
    templates = [
        (
            "SELECT c_custkey, SUM(c_acctbal - 999999) as total_balance "
            "FROM customer GROUP BY c_custkey",
            "Subtraction creates negative balances (business logic violation)",
        ),
        (
            "SELECT o_custkey, AVG(o_totalprice * -1) as avg_price "
            "FROM orders GROUP BY o_custkey",
            "Negated price produces negative average (sign error)",
        ),
        (
            "SELECT l_orderkey, SUM(l_quantity - l_extendedprice) as metric "
            "FROM lineitem GROUP BY l_orderkey",
            "Subtracting price from quantity (unit mismatch)",
        ),
        (
            "SELECT n_name, COUNT(DISTINCT s_suppkey) "
            "FROM nation JOIN supplier ON n_nationkey = s_nationkey "
            "GROUP BY n_name HAVING COUNT(DISTINCT s_suppkey) < 0",
            "HAVING COUNT < 0 is always false (logical contradiction)",
        ),
        (
            "SELECT p_brand, SUM(ps_supplycost * -ps_availqty) as inventory_value "
            "FROM part JOIN partsupp ON p_partkey = ps_partkey "
            "GROUP BY p_brand",
            "Negative inventory value (sign error in multiplication)",
        ),
    ]

    for i in range(n):
        t = templates[i % len(templates)]
        queries.append(HazardousQuery(
            sql=t[0] + f" -- agg_var_{i}",
            category="AGG_ERROR",
            description=t[1],
            expected_detection_channel="exec",
        ))

    return queries[:n]


def generate_all_hazardous_queries() -> list[HazardousQuery]:
    """Generate the full 200-query hazardous benchmark."""
    all_queries = []
    all_queries.extend(generate_cartesian_products(50))
    all_queries.extend(generate_full_scans(50))
    all_queries.extend(generate_empty_joins(50))
    all_queries.extend(generate_aggregation_errors(50))
    return all_queries


def generate_correct_queries() -> list[dict]:
    """Generate 800 known-correct queries for false positive analysis (Section 5.4)."""
    queries = []

    # --- Single-table queries (200) ---
    single_table = [
        "SELECT c_name, c_acctbal FROM customer WHERE c_acctbal > 1000 LIMIT 50",
        "SELECT o_orderkey, o_totalprice FROM orders WHERE o_orderdate >= '1995-01-01' LIMIT 100",
        "SELECT p_name, p_retailprice FROM part WHERE p_size BETWEEN 10 AND 20",
        "SELECT s_name, s_acctbal FROM supplier WHERE s_nationkey = 1",
        "SELECT n_name FROM nation WHERE n_regionkey = 2",
        "SELECT COUNT(*) FROM orders WHERE o_orderstatus = 'F'",
        "SELECT l_shipmode, COUNT(*) FROM lineitem GROUP BY l_shipmode LIMIT 10",
        "SELECT r_name FROM region ORDER BY r_name",
    ]

    for i in range(200):
        q = single_table[i % len(single_table)]
        queries.append({"sql": q, "category": "single_table", "is_correct": True})

    # --- Equi-join 2 tables (200) ---
    two_table_join = [
        "SELECT c.c_name, o.o_totalprice FROM customer c "
        "JOIN orders o ON c.c_custkey = o.o_custkey LIMIT 100",
        "SELECT o.o_orderkey, l.l_extendedprice FROM orders o "
        "JOIN lineitem l ON o.o_orderkey = l.l_orderkey LIMIT 100",
        "SELECT s.s_name, n.n_name FROM supplier s "
        "JOIN nation n ON s.s_nationkey = n.n_nationkey",
        "SELECT p.p_name, ps.ps_supplycost FROM part p "
        "JOIN partsupp ps ON p.p_partkey = ps.ps_partkey LIMIT 50",
        "SELECT c.c_name, n.n_name FROM customer c "
        "JOIN nation n ON c.c_nationkey = n.n_nationkey",
    ]

    for i in range(200):
        q = two_table_join[i % len(two_table_join)]
        queries.append({"sql": q, "category": "equi_join_2", "is_correct": True})

    # --- Equi-join 3+ tables (150) ---
    multi_join = [
        "SELECT c.c_name, o.o_totalprice, l.l_extendedprice FROM customer c "
        "JOIN orders o ON c.c_custkey = o.o_custkey "
        "JOIN lineitem l ON o.o_orderkey = l.l_orderkey LIMIT 50",
        "SELECT s.s_name, n.n_name, r.r_name FROM supplier s "
        "JOIN nation n ON s.s_nationkey = n.n_nationkey "
        "JOIN region r ON n.n_regionkey = r.r_regionkey",
        "SELECT c.c_name, o.o_orderdate, l.l_shipdate, p.p_name FROM customer c "
        "JOIN orders o ON c.c_custkey = o.o_custkey "
        "JOIN lineitem l ON o.o_orderkey = l.l_orderkey "
        "JOIN part p ON l.l_partkey = p.p_partkey LIMIT 20",
    ]

    for i in range(150):
        q = multi_join[i % len(multi_join)]
        queries.append({"sql": q, "category": "equi_join_3plus", "is_correct": True})

    # --- Non-equi join (100) ---
    non_equi = [
        "SELECT o.o_orderkey, l.l_orderkey FROM orders o "
        "JOIN lineitem l ON o.o_orderdate BETWEEN l.l_shipdate AND l.l_receiptdate LIMIT 50",
        "SELECT p1.p_name, p2.p_name FROM part p1 "
        "JOIN part p2 ON p1.p_retailprice > p2.p_retailprice AND p1.p_partkey != p2.p_partkey LIMIT 50",
        "SELECT s.s_name, c.c_name FROM supplier s "
        "JOIN customer c ON s.s_acctbal > c.c_acctbal LIMIT 20",
    ]

    for i in range(100):
        q = non_equi[i % len(non_equi)]
        queries.append({"sql": q, "category": "non_equi_join", "is_correct": True})

    # --- Subquery (150) ---
    subquery = [
        "SELECT c_name FROM customer WHERE c_custkey IN "
        "(SELECT o_custkey FROM orders WHERE o_totalprice > 10000) LIMIT 50",
        "SELECT o_orderkey FROM orders WHERE o_custkey IN "
        "(SELECT c_custkey FROM customer WHERE c_nationkey = 1) LIMIT 50",
        "SELECT p_name FROM part WHERE p_partkey IN "
        "(SELECT ps_partkey FROM partsupp WHERE ps_supplycost < 100) LIMIT 50",
        "SELECT s_name FROM supplier WHERE EXISTS "
        "(SELECT 1 FROM partsupp WHERE ps_suppkey = s_suppkey AND ps_availqty > 1000)",
        "SELECT c_name, (SELECT COUNT(*) FROM orders WHERE o_custkey = c_custkey) as order_count "
        "FROM customer LIMIT 50",
    ]

    for i in range(150):
        q = subquery[i % len(subquery)]
        queries.append({"sql": q, "category": "subquery", "is_correct": True})

    return queries


def save_benchmark(output_path: str = "benchmarks/"):
    """Save all benchmark queries to JSON files."""
    import os
    os.makedirs(output_path, exist_ok=True)

    # Hazardous queries
    hazardous = generate_all_hazardous_queries()
    with open(os.path.join(output_path, "hazardous_queries.json"), "w") as f:
        json.dump(
            [{"sql": q.sql, "category": q.category, "description": q.description,
              "channel": q.expected_detection_channel} for q in hazardous],
            f, indent=2,
        )
    print(f"Generated {len(hazardous)} hazardous queries")

    # Correct queries
    correct = generate_correct_queries()
    with open(os.path.join(output_path, "correct_queries.json"), "w") as f:
        json.dump(correct, f, indent=2)
    print(f"Generated {len(correct)} correct queries")


if __name__ == "__main__":
    save_benchmark()
