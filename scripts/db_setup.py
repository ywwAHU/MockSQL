"""Database setup: create TPC-H tables and load data for benchmarking."""

import io
import logging
import os
import subprocess

import psycopg2

logger = logging.getLogger(__name__)

# TPC-H schema (SF=10 compatible)
TPCH_SCHEMA = """
-- TPC-H Schema for MockSQL benchmarking

DROP TABLE IF EXISTS lineitem CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS partsupp CASCADE;
DROP TABLE IF EXISTS customer CASCADE;
DROP TABLE IF EXISTS part CASCADE;
DROP TABLE IF EXISTS supplier CASCADE;
DROP TABLE IF EXISTS nation CASCADE;
DROP TABLE IF EXISTS region CASCADE;

CREATE TABLE region (
    r_regionkey INTEGER PRIMARY KEY,
    r_name CHAR(25) NOT NULL,
    r_comment VARCHAR(152)
);

CREATE TABLE nation (
    n_nationkey INTEGER PRIMARY KEY,
    n_name CHAR(25) NOT NULL,
    n_regionkey INTEGER NOT NULL REFERENCES region(r_regionkey),
    n_comment VARCHAR(152)
);

CREATE TABLE supplier (
    s_suppkey INTEGER PRIMARY KEY,
    s_name CHAR(25) NOT NULL,
    s_address VARCHAR(40) NOT NULL,
    s_nationkey INTEGER NOT NULL REFERENCES nation(n_nationkey),
    s_phone CHAR(15) NOT NULL,
    s_acctbal DECIMAL(15,2) NOT NULL,
    s_comment VARCHAR(101)
);

CREATE TABLE part (
    p_partkey INTEGER PRIMARY KEY,
    p_name VARCHAR(55) NOT NULL,
    p_mfgr CHAR(25) NOT NULL,
    p_brand CHAR(10) NOT NULL,
    p_type VARCHAR(25) NOT NULL,
    p_size INTEGER NOT NULL,
    p_container CHAR(10) NOT NULL,
    p_retailprice DECIMAL(15,2) NOT NULL,
    p_comment VARCHAR(23)
);

CREATE TABLE partsupp (
    ps_partkey INTEGER NOT NULL REFERENCES part(p_partkey),
    ps_suppkey INTEGER NOT NULL REFERENCES supplier(s_suppkey),
    ps_availqty INTEGER NOT NULL,
    ps_supplycost DECIMAL(15,2) NOT NULL,
    ps_comment VARCHAR(199),
    PRIMARY KEY (ps_partkey, ps_suppkey)
);

CREATE TABLE customer (
    c_custkey INTEGER PRIMARY KEY,
    c_name VARCHAR(25) NOT NULL,
    c_address VARCHAR(40) NOT NULL,
    c_nationkey INTEGER NOT NULL REFERENCES nation(n_nationkey),
    c_phone CHAR(15) NOT NULL,
    c_acctbal DECIMAL(15,2) NOT NULL,
    c_mktsegment CHAR(10) NOT NULL,
    c_comment VARCHAR(117)
);

CREATE TABLE orders (
    o_orderkey INTEGER PRIMARY KEY,
    o_custkey INTEGER NOT NULL REFERENCES customer(c_custkey),
    o_orderstatus CHAR(1) NOT NULL,
    o_totalprice DECIMAL(15,2) NOT NULL,
    o_orderdate DATE NOT NULL,
    o_orderpriority CHAR(15) NOT NULL,
    o_clerk CHAR(15) NOT NULL,
    o_shippriority INTEGER NOT NULL,
    o_comment VARCHAR(79)
);

CREATE TABLE lineitem (
    l_orderkey INTEGER NOT NULL REFERENCES orders(o_orderkey),
    l_partkey INTEGER NOT NULL REFERENCES part(p_partkey),
    l_suppkey INTEGER NOT NULL REFERENCES supplier(s_suppkey),
    l_linenumber INTEGER NOT NULL,
    l_quantity DECIMAL(15,2) NOT NULL,
    l_extendedprice DECIMAL(15,2) NOT NULL,
    l_discount DECIMAL(15,2) NOT NULL,
    l_tax DECIMAL(15,2) NOT NULL,
    l_returnflag CHAR(1) NOT NULL,
    l_linestatus CHAR(1) NOT NULL,
    l_shipdate DATE NOT NULL,
    l_commitdate DATE NOT NULL,
    l_receiptdate DATE NOT NULL,
    l_shipinstruct CHAR(25) NOT NULL,
    l_shipmode CHAR(10) NOT NULL,
    l_comment VARCHAR(44) NOT NULL,
    PRIMARY KEY (l_orderkey, l_linenumber)
);

-- Create indexes for realistic query plans
CREATE INDEX idx_orders_custkey ON orders(o_custkey);
CREATE INDEX idx_orders_orderdate ON orders(o_orderdate);
CREATE INDEX idx_lineitem_orderkey ON lineitem(l_orderkey);
CREATE INDEX idx_lineitem_partkey ON lineitem(l_partkey);
CREATE INDEX idx_lineitem_suppkey ON lineitem(l_suppkey);
CREATE INDEX idx_lineitem_shipdate ON lineitem(l_shipdate);
CREATE INDEX idx_customer_nationkey ON customer(c_nationkey);
CREATE INDEX idx_supplier_nationkey ON supplier(s_nationkey);
CREATE INDEX idx_nation_regionkey ON nation(n_regionkey);
"""


def setup_tpch_database(dsn: str, scale_factor: int = 10, dbgen_path: str = "./dbgen"):
    """Set up TPC-H database with given scale factor.

    Prerequisites:
        - TPC-H dbgen tool compiled at dbgen_path
        - PostgreSQL database accessible via dsn

    Steps:
        1. Generate TPC-H data using dbgen
        2. Create schema
        3. Load data using COPY
        4. Run ANALYZE for statistics
    """
    conn = psycopg2.connect(dsn)
    conn.autocommit = True

    try:
        logger.info("Creating TPC-H schema...")
        with conn.cursor() as cur:
            cur.execute(TPCH_SCHEMA)

        # Generate data if dbgen is available
        if os.path.exists(dbgen_path):
            logger.info("Generating TPC-H data (SF=%d)...", scale_factor)
            subprocess.run(
                [os.path.join(dbgen_path, "dbgen"), "-s", str(scale_factor), "-f"],
                cwd=dbgen_path,
                check=True,
            )

            # Load data
            tables_files = [
                ("region", "region.tbl"),
                ("nation", "nation.tbl"),
                ("supplier", "supplier.tbl"),
                ("part", "part.tbl"),
                ("partsupp", "partsupp.tbl"),
                ("customer", "customer.tbl"),
                ("orders", "orders.tbl"),
                ("lineitem", "lineitem.tbl"),
            ]

            with conn.cursor() as cur:
                for table, filename in tables_files:
                    filepath = os.path.join(dbgen_path, filename)
                    if os.path.exists(filepath):
                        logger.info("Loading %s...", table)
                        with open(filepath) as f:
                            # dbgen produces a trailing '|' on every line;
                            # strip it so PostgreSQL doesn't expect an extra column
                            cleaned = io.StringIO()
                            for line in f:
                                cleaned.write(line.rstrip().rstrip("|") + "\n")
                            cleaned.seek(0)
                            cur.copy_expert(
                                f"COPY {table} FROM STDIN WITH DELIMITER '|'",
                                cleaned,
                            )
        else:
            logger.warning(
                "dbgen not found at %s. Schema created but no data loaded. "
                "Please load TPC-H data manually.",
                dbgen_path,
            )

        # Analyze for statistics
        logger.info("Running ANALYZE...")
        with conn.cursor() as cur:
            cur.execute("ANALYZE")

        logger.info("TPC-H setup complete.")

    finally:
        conn.close()


def setup_spider_database(dsn: str, spider_path: str = "./spider"):
    """Set up Spider benchmark databases.

    Prerequisites:
        - Spider dataset downloaded at spider_path
        - Spider provides SQLite databases; we convert them to PostgreSQL
    """
    import sqlite3
    import json

    tables_json = os.path.join(spider_path, "tables.json")
    if not os.path.exists(tables_json):
        logger.error("Spider tables.json not found at %s", tables_json)
        return

    with open(tables_json) as f:
        databases = json.load(f)

    conn = psycopg2.connect(dsn)
    conn.autocommit = True

    try:
        for db_info in databases:
            db_id = db_info["db_id"]
            sqlite_path = os.path.join(spider_path, "database", db_id, f"{db_id}.sqlite")

            if not os.path.exists(sqlite_path):
                continue

            logger.info("Importing Spider database: %s", db_id)

            # Create schema in PostgreSQL
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS spider_{db_id}")

            # Read SQLite and convert
            sqlite_conn = sqlite3.connect(sqlite_path)
            sqlite_cur = sqlite_conn.cursor()

            # Get table creation SQL
            sqlite_cur.execute(
                "SELECT sql FROM sqlite_master WHERE type='table'"
            )
            for (create_sql,) in sqlite_cur.fetchall():
                if create_sql:
                    # Convert SQLite syntax to PostgreSQL
                    pg_sql = _sqlite_to_pg(create_sql, f"spider_{db_id}")
                    try:
                        with conn.cursor() as cur:
                            cur.execute(pg_sql)
                    except Exception as e:
                        logger.warning("Failed to create table in %s: %s", db_id, e)

            sqlite_conn.close()

        # Analyze
        with conn.cursor() as cur:
            cur.execute("ANALYZE")

    finally:
        conn.close()


def _sqlite_to_pg(create_sql: str, schema: str) -> str:
    """Basic SQLite to PostgreSQL CREATE TABLE conversion."""
    import re
    sql = create_sql
    # Replace AUTOINCREMENT with SERIAL
    sql = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY", sql, flags=re.IGNORECASE)
    # Replace TEXT with VARCHAR
    sql = sql.replace("TEXT", "VARCHAR")
    # Add schema prefix
    sql = re.sub(r"CREATE\s+TABLE\s+(\w+)", f"CREATE TABLE {schema}.\\1", sql, flags=re.IGNORECASE)
    return sql


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python db_setup.py <dsn> [--sf=10] [--dbgen-path=./dbgen]")
        sys.exit(1)

    dsn = sys.argv[1]
    sf = 10
    dbgen = "./dbgen"

    for arg in sys.argv[2:]:
        if arg.startswith("--sf="):
            sf = int(arg.split("=")[1])
        elif arg.startswith("--dbgen-path="):
            dbgen = arg.split("=")[1]

    setup_tpch_database(dsn, scale_factor=sf, dbgen_path=dbgen)
