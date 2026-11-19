"""Parse-based SQL guard.

Policy, in order:

1. exactly one statement, no statement stacking, no comments (comments are how
   statements get smuggled past naive checkers);
2. the statement must be a query (``SELECT``/``UNION``/...); DDL and DML are
   rejected outright, not "disabled";
3. only allowlisted tables and columns;
4. no system catalogs, no file/network/sleep functions;
5. a bounded ``LIMIT`` is always present.

The guard returns a structured result. It never executes anything.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from opsquery.config import settings

DEFAULT_LIMIT = 500
MAX_LIMIT = 1_000

ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "customers",
        "inventory",
        "invoices",
        "order_items",
        "orders",
        "payments",
        "products",
        "support_tickets",
        "suppliers",
    }
)

ALLOWED_COLUMNS: dict[str, frozenset[str]] = {
    "customers": frozenset(
        {"id", "name", "email", "phone", "segment", "city", "country", "created_at"}
    ),
    "products": frozenset({"id", "sku", "name", "category", "unit_price", "active", "created_at"}),
    "suppliers": frozenset({"id", "name", "country", "lead_time_days", "rating"}),
    "inventory": frozenset(
        {"id", "product_id", "warehouse", "quantity_on_hand", "reorder_point", "updated_at"}
    ),
    "orders": frozenset(
        {
            "id",
            "order_number",
            "customer_id",
            "status",
            "channel",
            "ordered_at",
            "shipped_at",
            "currency",
            "total_amount",
        }
    ),
    "order_items": frozenset(
        {"id", "order_id", "product_id", "quantity", "unit_price", "discount_pct", "line_total"}
    ),
    "invoices": frozenset(
        {"id", "order_id", "invoice_number", "issued_at", "due_at", "status", "amount", "paid_at"}
    ),
    "payments": frozenset({"id", "invoice_id", "paid_at", "amount", "method", "reference"}),
    "support_tickets": frozenset(
        {
            "id",
            "customer_id",
            "opened_at",
            "closed_at",
            "category",
            "priority",
            "subject",
            "satisfaction",
        }
    ),
}

_ALL_COLUMNS: frozenset[str] = frozenset().union(*ALLOWED_COLUMNS.values())
_AMBIGUOUS_COLUMNS = frozenset(
    column
    for column in _ALL_COLUMNS
    if sum(1 for cols in ALLOWED_COLUMNS.values() if column in cols) > 1
)

FORBIDDEN_SCHEMAS = frozenset({"pg_catalog", "information_schema", "pg_toast"})
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "PG_SLEEP",
        "DBLINK",
        "DBLINK_CONNECT",
        "DBLINK_EXEC",
        "PG_READ_FILE",
        "PG_READ_BINARY_FILE",
        "PG_READ_SERVER_FILE",
        "PG_LS_DIR",
        "PG_STAT_FILE",
        "LO_IMPORT",
        "LO_EXPORT",
        "LO_UNLINK",
        "PG_TERMINATE_BACKEND",
        "PG_CANCEL_BACKEND",
        "PG_RELOAD_CONF",
        "SET_CONFIG",
        "CURRENT_SETTING",
        "COPY_TO",
        "COPY_FROM",
        "PG_ADVISORY_LOCK",
        "PG_TRY_ADVISORY_LOCK",
        "QUERY_TO_XML",
        "CURSOR_TO_XML",
        "DATABASE_TO_XML",
    }
)

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_0-9]*\$")


class ErrorCode(str, Enum):
    PARSE_ERROR = "PARSE_ERROR"
    UNSAFE = "UNSAFE"
    UNKNOWN_TABLE = "UNKNOWN_TABLE"
    TIMEOUT = "TIMEOUT"
    EMPTY = "EMPTY"
    DB_ERROR = "DB_ERROR"


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    sql: str | None
    code: ErrorCode | None
    message: str

    @classmethod
    def accept(cls, sql: str) -> GuardResult:
        return cls(ok=True, sql=sql, code=None, message="ok")

    @classmethod
    def reject(cls, code: ErrorCode, message: str) -> GuardResult:
        return cls(ok=False, sql=None, code=code, message=message)


def _scan(sql: str) -> GuardResult | None:
    """Reject comments, stacked statements and NUL bytes outside string literals."""

    quote: str | None = None
    dollar: str | None = None
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if dollar is not None:
            if sql.startswith(dollar, index):
                index += len(dollar)
                dollar = None
                continue
            index += 1
            continue
        if quote is not None:
            if char == quote:
                if index + 1 < length and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "$":
            match = _DOLLAR_TAG.match(sql, index)
            if match is not None:
                dollar = match.group(0)
                index = match.end()
                continue
        if char == "-" and sql[index : index + 2] == "--":
            return GuardResult.reject(ErrorCode.UNSAFE, "SQL comments are not permitted")
        if char == "/" and sql[index : index + 2] == "/*":
            return GuardResult.reject(ErrorCode.UNSAFE, "SQL comments are not permitted")
        if char == ";":
            return GuardResult.reject(ErrorCode.UNSAFE, "statement stacking is not permitted")
        if char == "\x00":
            return GuardResult.reject(ErrorCode.UNSAFE, "NUL byte in SQL")
        index += 1
    return None


def _table_name(node: exp.Table) -> str:
    return node.name.lower()


def _is_cte_reference(node: exp.Table, cte_names: set[str]) -> bool:
    return not node.db and _table_name(node) in cte_names


def _check_tables(ast: exp.Expression, cte_names: set[str]) -> GuardResult | None:
    for table in ast.find_all(exp.Table):
        if _is_cte_reference(table, cte_names):
            continue
        name = _table_name(table)
        if not name:
            # Table function such as generate_series(...); the function itself is
            # checked against the function blocklist.
            continue
        schema = (table.db or "").lower()
        if schema and schema not in {"public"}:
            return GuardResult.reject(ErrorCode.UNSAFE, f"schema '{schema}' is not allowlisted")
        if name.startswith("pg_") or schema in FORBIDDEN_SCHEMAS:
            return GuardResult.reject(ErrorCode.UNSAFE, "system catalog access is not permitted")
        if name not in ALLOWED_TABLES:
            return GuardResult.reject(
                ErrorCode.UNKNOWN_TABLE, f"table '{name}' is not in the allowlist"
            )
    return None


def _check_columns(
    ast: exp.Expression, cte_names: set[str], alias_names: set[str]
) -> GuardResult | None:
    all_tables = list(ast.find_all(exp.Table))
    global_aliases = {_table_name(t) for t in all_tables if _table_name(t)}
    global_aliases |= {t.alias.lower() for t in all_tables if t.alias}
    global_aliases |= {name.lower() for name in cte_names}

    selects = list(ast.find_all(exp.Select))
    for select in selects:
        scope_nodes = [
            table
            for table in select.find_all(exp.Table)
            if table.find_ancestor(exp.Select) is select and not _is_cte_reference(table, cte_names)
        ]
        scope_table_count = len(
            {(_table_name(t), (t.db or "").lower()) for t in scope_nodes if _table_name(t)}
        )
        scope_aliases = global_aliases | {t.alias.lower() for t in scope_nodes if t.alias}
        scope_alias_columns = alias_names | {t.alias.lower() for t in scope_nodes if t.alias}
        for column in select.find_all(exp.Column):
            if column.find_ancestor(exp.Select) is not select:
                continue
            name = column.name.lower()
            if not name or name == "*":
                continue
            qualifier = column.table.lower()
            if qualifier:
                if qualifier not in scope_aliases:
                    return GuardResult.reject(
                        ErrorCode.UNSAFE, f"unknown table alias '{qualifier}'"
                    )
                if name not in _ALL_COLUMNS and name not in scope_alias_columns:
                    return GuardResult.reject(
                        ErrorCode.UNSAFE, f"column '{qualifier}.{name}' is not allowlisted"
                    )
                continue
            if name in scope_alias_columns:
                continue
            if name not in _ALL_COLUMNS:
                return GuardResult.reject(ErrorCode.UNSAFE, f"column '{name}' is not allowlisted")
            if scope_table_count > 1 and name in _AMBIGUOUS_COLUMNS:
                return GuardResult.reject(
                    ErrorCode.UNSAFE,
                    f"column '{name}' is ambiguous across joined tables; qualify it",
                )
    return None


def _check_functions(ast: exp.Expression) -> GuardResult | None:
    for node in ast.find_all(exp.Func):
        name = str(node.this).upper() if isinstance(node, exp.Anonymous) else str(node.key).upper()
        if name in FORBIDDEN_FUNCTIONS:
            return GuardResult.reject(
                ErrorCode.UNSAFE, f"function '{name.lower()}' is not permitted"
            )
    return None


def _apply_limit(
    ast: exp.Expression, default_limit: int, max_limit: int
) -> GuardResult | exp.Expression:
    limit = ast.args.get("limit")
    if limit is None:
        ast.set("limit", exp.Limit(expression=exp.Literal.number(default_limit)))
        return ast
    expression = limit.expression
    if not isinstance(expression, exp.Literal) or expression.is_string:
        return GuardResult.reject(ErrorCode.UNSAFE, "LIMIT must be an integer literal")
    value = int(expression.this)
    if value > max_limit:
        ast.set("limit", exp.Limit(expression=exp.Literal.number(max_limit)))
    return ast


_QUERY_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)


def guard(
    sql: str,
    *,
    default_limit: int | None = None,
    max_limit: int | None = None,
) -> GuardResult:
    """Validate ``sql`` and return the normalised, bounded statement."""

    cfg = settings()
    limit_default = default_limit if default_limit is not None else cfg.default_limit
    limit_max = max_limit if max_limit is not None else cfg.max_rows
    limit_max = min(limit_max, MAX_LIMIT)

    if not sql or not sql.strip():
        return GuardResult.reject(ErrorCode.EMPTY, "empty SQL")

    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1]
    scan_error = _scan(stripped)
    if scan_error is not None:
        return scan_error

    try:
        statements = sqlglot.parse(stripped, read="postgres")
    except ParseError as exc:
        return GuardResult.reject(ErrorCode.PARSE_ERROR, f"parse error: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        return GuardResult.reject(ErrorCode.PARSE_ERROR, f"parse error: {exc}")
    if not statements:  # pragma: no cover - parse always returns a statement here
        return GuardResult.reject(ErrorCode.EMPTY, "empty SQL")
    if len(statements) > 1:  # pragma: no cover - semicolons are rejected by _scan
        return GuardResult.reject(ErrorCode.UNSAFE, "exactly one statement is allowed")
    ast = cast(exp.Expression, statements[0])
    if not isinstance(ast, _QUERY_TYPES):
        return GuardResult.reject(
            ErrorCode.UNSAFE, f"only SELECT queries are allowed, got {ast.key.upper()}"
        )
    if ast.find(exp.Into) is not None:
        return GuardResult.reject(ErrorCode.UNSAFE, "SELECT INTO is not permitted")
    if ast.find(exp.Lock) is not None:
        return GuardResult.reject(ErrorCode.UNSAFE, "row locking is not permitted")

    cte_names = {cte.alias_or_name.lower() for cte in ast.find_all(exp.CTE)}
    alias_names = {alias.alias.lower() for alias in ast.find_all(exp.Alias)}
    for cte in ast.find_all(exp.CTE):
        alias_names |= {name.lower() for name in cte.alias_column_names}

    for check in (
        _check_tables(ast, cte_names),
        _check_columns(ast, cte_names, alias_names),
        _check_functions(ast),
    ):
        if check is not None:
            return check

    limited = _apply_limit(ast, limit_default, limit_max)
    if isinstance(limited, GuardResult):
        return limited
    return GuardResult.accept(limited.sql(dialect="postgres"))


DEMO_ACCEPTED = [
    "SELECT status, count(*) AS n FROM orders GROUP BY status ORDER BY n DESC",
    "SELECT c.name, sum(p.amount) AS revenue FROM customers c "
    "JOIN orders o ON o.customer_id = c.id "
    "JOIN invoices i ON i.order_id = o.id "
    "JOIN payments p ON p.invoice_id = i.id GROUP BY c.name LIMIT 5",
    "WITH recent AS (SELECT id, total_amount FROM orders WHERE ordered_at >= '2025-01-01') "
    "SELECT count(*) FROM recent",
]

DEMO_REJECTED = [
    "DROP TABLE orders",
    "SELECT 1; DROP TABLE orders",
    "SELECT 1 -- ; DROP TABLE orders",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT pg_sleep(10)",
    "INSERT INTO customers (name) VALUES ('x')",
    "SELECT * FROM secret_schema.orders",
]


def demo() -> int:
    print("accepted:")
    for example in DEMO_ACCEPTED:
        result = guard(example)
        print(f"  [{result.code.value if result.code else 'OK'}] {example}")
        if not result.ok:  # pragma: no cover - demo invariant
            return 1
        print(f"    -> {result.sql}")
    print("rejected:")
    for example in DEMO_REJECTED:
        result = guard(example)
        print(f"  [{result.code}] {example} :: {result.message}")
        if result.ok:  # pragma: no cover - demo invariant
            return 1
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opsquery.sql.guard")
    parser.add_argument("--demo", action="store_true", help="print accepted/rejected examples")
    parser.add_argument("sql", nargs="?", help="SQL to check")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.demo:
        return demo()
    if not args.sql:
        parser.print_help()
        return 2
    result = guard(args.sql)
    print(f"{'ACCEPT' if result.ok else result.code}: {result.sql or result.message}")
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
