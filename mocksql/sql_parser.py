"""SQL parser: extract table names, query type, and structural information."""

import re
import sqlparse
from sqlparse.sql import IdentifierList, Identifier, Where, Parenthesis
from sqlparse.tokens import Keyword, DML


def extract_tables(sql: str) -> list[str]:
    """Extract all table names referenced in a SQL query.

    Handles FROM, JOIN, subqueries, and CTEs.
    """
    tables = set()
    parsed = sqlparse.parse(sql)
    if not parsed:
        return []

    for statement in parsed:
        _extract_tables_from_statement(statement, tables)

    return sorted(tables)


def _extract_tables_from_statement(statement, tables: set):
    """Recursively extract table names from a parsed SQL statement."""
    from_seen = False
    join_seen = False

    for token in statement.tokens:
        if token.ttype is DML and token.value.upper() == "SELECT":
            from_seen = False
            join_seen = False
        elif token.ttype is Keyword:
            upper = token.value.upper()
            if upper in ("FROM", "INTO", "UPDATE"):
                from_seen = True
                join_seen = False
            elif "JOIN" in upper:
                join_seen = True
                from_seen = False
            elif upper in (
                "ON", "SET", "WHERE", "GROUP", "ORDER", "LIMIT",
                "HAVING", "UNION", "INTERSECT", "EXCEPT",
            ):
                from_seen = False
                join_seen = False
        elif from_seen or join_seen:
            if isinstance(token, IdentifierList):
                for identifier in token.get_identifiers():
                    _extract_table_name(identifier, tables)
                from_seen = False
                join_seen = False
            elif isinstance(token, Identifier):
                _extract_table_name(token, tables)
                from_seen = False
                join_seen = False
            elif isinstance(token, Parenthesis):
                # Subquery — recurse
                _extract_tables_from_statement(token, tables)
                from_seen = False
                join_seen = False

        # Recurse into subqueries in WHERE, etc.
        if isinstance(token, (Where, Parenthesis)):
            _extract_tables_from_statement(token, tables)

    # Handle CTEs (WITH ... AS (...))
    sql_upper = str(statement).upper()
    if sql_upper.strip().startswith("WITH"):
        cte_pattern = re.compile(
            r"WITH\s+(\w+)\s+AS\s*\(", re.IGNORECASE
        )
        cte_names = set(m.group(1).lower() for m in cte_pattern.finditer(str(statement)))
        tables -= cte_names  # CTEs are not real tables


def _extract_table_name(token, tables: set):
    """Extract a real table name from an Identifier token."""
    if isinstance(token, Identifier):
        # Check for subquery
        if token.tokens and isinstance(token.tokens[0], Parenthesis):
            _extract_tables_from_statement(token.tokens[0], tables)
            return
        name = token.get_real_name()
        if name:
            # Remove schema prefix if present (e.g., public.orders -> orders)
            name = name.split(".")[-1].strip('"').strip("`").strip("'")
            if name.upper() not in _SQL_KEYWORDS:
                tables.add(name.lower())


_SQL_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "JOIN", "ON", "AND", "OR", "NOT",
    "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER",
    "TABLE", "INDEX", "VIEW", "AS", "IN", "EXISTS", "BETWEEN",
    "LIKE", "IS", "NULL", "TRUE", "FALSE", "CASE", "WHEN",
    "THEN", "ELSE", "END", "GROUP", "ORDER", "BY", "HAVING",
    "LIMIT", "OFFSET", "UNION", "ALL", "DISTINCT", "COUNT",
    "SUM", "AVG", "MIN", "MAX", "INNER", "LEFT", "RIGHT",
    "OUTER", "FULL", "CROSS", "NATURAL", "WITH", "RECURSIVE",
}


def get_query_type(sql: str) -> str:
    """Return the type of SQL statement: SELECT, INSERT, UPDATE, DELETE, etc."""
    parsed = sqlparse.parse(sql.strip())
    if not parsed:
        return "UNKNOWN"
    first = parsed[0]
    return first.get_type() or "UNKNOWN"


def has_limit(sql: str) -> bool:
    """Check if the query contains a LIMIT clause."""
    return bool(re.search(r"\bLIMIT\b", sql, re.IGNORECASE))


def has_where(sql: str) -> bool:
    """Check if the query contains a WHERE clause."""
    return bool(re.search(r"\bWHERE\b", sql, re.IGNORECASE))
