from __future__ import annotations

import pytest

from opsquery.sql.guard import (
    ErrorCode,
    _scan,
    guard,
    main,
)

REJECT_CASES: list[tuple[str, ErrorCode]] = [
    # DML / DDL
    ("INSERT INTO customers (name) VALUES ('x')", ErrorCode.UNSAFE),
    ("UPDATE orders SET status = 'paid'", ErrorCode.UNSAFE),
    ("DELETE FROM orders", ErrorCode.UNSAFE),
    ("DROP TABLE orders", ErrorCode.UNSAFE),
    ("TRUNCATE TABLE orders", ErrorCode.UNSAFE),
    ("ALTER TABLE orders ADD COLUMN x integer", ErrorCode.UNSAFE),
    ("GRANT SELECT ON orders TO ops_readonly", ErrorCode.UNSAFE),
    ("CREATE TABLE x (id integer)", ErrorCode.UNSAFE),
    ("CREATE INDEX x ON orders (status)", ErrorCode.UNSAFE),
    ("COPY orders TO STDOUT", ErrorCode.UNSAFE),
    ("VACUUM orders", ErrorCode.UNSAFE),
    ("SET statement_timeout = 0", ErrorCode.UNSAFE),
    ("CALL something()", ErrorCode.UNSAFE),
    # Stacking
    ("SELECT 1; DROP TABLE orders", ErrorCode.UNSAFE),
    ("SELECT 1; SELECT 2", ErrorCode.UNSAFE),
    ("SELECT 1;;", ErrorCode.UNSAFE),
    ("SELECT 1; -- trailing comment", ErrorCode.UNSAFE),
    ("SELECT 'a'; SELECT 'b'", ErrorCode.UNSAFE),
    # Comments
    ("SELECT 1 -- ; DROP TABLE orders", ErrorCode.UNSAFE),
    ("SELECT /* hidden */ 1", ErrorCode.UNSAFE),
    ("SELECT 1 /* multi\nline */", ErrorCode.UNSAFE),
    ("-- leading\nSELECT 1", ErrorCode.UNSAFE),
    ("SELECT 1 # not postgres", ErrorCode.UNSAFE),
    # System catalogs and dangerous functions
    ("SELECT * FROM pg_catalog.pg_tables", ErrorCode.UNSAFE),
    ("SELECT * FROM information_schema.tables", ErrorCode.UNSAFE),
    ("SELECT * FROM pg_user", ErrorCode.UNSAFE),
    ("SELECT * FROM pg_toast.pg_toast_123", ErrorCode.UNSAFE),
    ("SELECT pg_sleep(10)", ErrorCode.UNSAFE),
    ("SELECT pg_read_file('/etc/passwd')", ErrorCode.UNSAFE),
    ("SELECT pg_read_binary_file('/etc/passwd')", ErrorCode.UNSAFE),
    ("SELECT pg_ls_dir('.')", ErrorCode.UNSAFE),
    ("SELECT lo_import('/etc/passwd')", ErrorCode.UNSAFE),
    ("SELECT lo_export(1, '/tmp/x')", ErrorCode.UNSAFE),
    ("SELECT dblink('dbname=x', 'SELECT 1')", ErrorCode.UNSAFE),
    ("SELECT pg_terminate_backend(123)", ErrorCode.UNSAFE),
    ("SELECT current_setting('data_directory')", ErrorCode.UNSAFE),
    ("SELECT set_config('x', 'y', false)", ErrorCode.UNSAFE),
    # Tables and schemas
    ("SELECT * FROM users", ErrorCode.UNKNOWN_TABLE),
    ("SELECT * FROM secret.orders", ErrorCode.UNSAFE),
    ("SELECT * FROM auth.users", ErrorCode.UNSAFE),
    ("SELECT * FROM orders, users", ErrorCode.UNKNOWN_TABLE),
    # Columns and aliases
    ("SELECT password FROM customers", ErrorCode.UNSAFE),
    ("SELECT o.password FROM orders o", ErrorCode.UNSAFE),
    ("SELECT z.id FROM orders", ErrorCode.UNSAFE),
    ("SELECT id FROM orders JOIN customers ON orders.customer_id = customers.id", ErrorCode.UNSAFE),
    (
        "SELECT name FROM orders JOIN customers ON orders.customer_id = customers.id",
        ErrorCode.UNSAFE,
    ),
    # Query shapes that are read-only but not acceptable
    ("SELECT * INTO stolen FROM orders", ErrorCode.UNSAFE),
    ("SELECT * FROM orders FOR UPDATE", ErrorCode.UNSAFE),
    ("SELECT 1 LIMIT ALL", ErrorCode.UNSAFE),
    ("SELECT 1 LIMIT '5'", ErrorCode.UNSAFE),
    ("SELECT 1 LIMIT -1", ErrorCode.UNSAFE),
    # Parse failures and empties
    ("SELECT FROM", ErrorCode.PARSE_ERROR),
    ("SELEC 1", ErrorCode.PARSE_ERROR),
    ("WITH", ErrorCode.PARSE_ERROR),
    ("SELECT 1 +", ErrorCode.PARSE_ERROR),
    ("", ErrorCode.EMPTY),
    ("   \n\t ", ErrorCode.EMPTY),
    ("\x00SELECT 1", ErrorCode.UNSAFE),
]


@pytest.mark.parametrize("sql,code", REJECT_CASES)
def test_guard_rejections(sql: str, code: ErrorCode) -> None:
    result = guard(sql)
    assert not result.ok
    assert result.code == code, (sql, result.code, result.message)
    assert result.sql is None


@pytest.mark.parametrize("sql", [sql for sql, _ in REJECT_CASES])
def test_guard_never_returns_sql_for_rejections(sql: str) -> None:
    assert guard(sql).sql is None


ACCEPT_CASES = [
    "SELECT 1",
    "SELECT count(*) FROM orders",
    "SELECT status, count(*) AS n FROM orders GROUP BY status ORDER BY n DESC",
    "SELECT c.name, o.total_amount FROM customers c JOIN orders o ON o.customer_id = c.id",
    "SELECT * FROM orders",
    "SELECT o.*, c.name FROM orders o JOIN customers c ON o.customer_id = c.id",
    "WITH recent AS (SELECT id, total_amount FROM orders WHERE ordered_at >= '2025-01-01') "
    "SELECT count(*) FROM recent",
    "SELECT id FROM orders UNION SELECT id FROM invoices",
    "SELECT (SELECT max(total_amount) FROM orders) AS biggest",
    "SELECT date_trunc('month', ordered_at) AS month, sum(total_amount) FROM orders "
    "GROUP BY 1 ORDER BY 1",
    "SELECT name FROM customers WHERE email LIKE '%@example.com'",
    "SELECT lower(status) FROM orders",
    "SELECT COALESCE(phone, 'none') FROM customers",
    "SELECT n FROM generate_series(1, 10) AS n",
    "SELECT 'a--b' AS literal",
    "SELECT 'it''s fine' AS literal",
    "SELECT $$a;b$$ AS literal",
    "SELECT 1;",
]


@pytest.mark.parametrize("sql", ACCEPT_CASES)
def test_guard_accepts_read_only_queries(sql: str) -> None:
    result = guard(sql)
    assert result.ok, result.message
    assert result.sql is not None


def test_guard_rejects_dml_ddl() -> None:
    statements = {
        "INSERT": "INSERT INTO orders (id) VALUES (1)",
        "UPDATE": "UPDATE orders SET status = 'paid'",
        "DELETE": "DELETE FROM orders",
        "DROP": "DROP TABLE orders",
        "TRUNCATE": "TRUNCATE TABLE orders",
        "ALTER": "ALTER TABLE orders ADD COLUMN x integer",
        "GRANT": "GRANT SELECT ON orders TO ops_readonly",
    }
    for result in (guard(sql) for sql in statements.values()):
        assert not result.ok
        assert result.code is ErrorCode.UNSAFE


def test_guard_rejects_statement_stacking() -> None:
    result = guard("SELECT 1; DROP TABLE orders")
    assert result.code is ErrorCode.UNSAFE
    assert "stacking" in result.message


def test_guard_rejects_comment_smuggling() -> None:
    result = guard("SELECT 1 -- ; DROP TABLE x")
    assert result.code is ErrorCode.UNSAFE
    assert "comment" in result.message.lower()


def test_guard_rejects_system_catalog_and_file_functions() -> None:
    for sql in (
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_sleep(1)",
        "SELECT dblink('x', 'y')",
    ):
        assert guard(sql).code is ErrorCode.UNSAFE


def test_guard_rejects_non_allowlisted_table() -> None:
    result = guard("SELECT * FROM users")
    assert result.code is ErrorCode.UNKNOWN_TABLE
    assert "users" in result.message


def test_guard_adds_limit_and_preserves_existing_limit() -> None:
    added = guard("SELECT id FROM orders")
    assert added.ok and added.sql is not None
    assert added.sql.endswith("LIMIT 500")

    preserved = guard("SELECT id FROM orders LIMIT 10")
    assert preserved.ok and preserved.sql is not None
    assert preserved.sql.endswith("LIMIT 10")

    clamped = guard("SELECT id FROM orders LIMIT 5000")
    assert clamped.ok and clamped.sql is not None
    assert clamped.sql.endswith("LIMIT 1000")


def test_guard_accepts_cte_and_join_select() -> None:
    assert guard(
        "WITH c AS (SELECT id FROM customers) "
        "SELECT o.id FROM orders o JOIN c ON c.id = o.customer_id"
    ).ok
    assert guard(
        "SELECT c.name, count(*) FROM customers c "
        "JOIN orders o ON o.customer_id = c.id GROUP BY c.name"
    ).ok


def test_guard_is_idempotent() -> None:
    for sql in ACCEPT_CASES:
        first = guard(sql)
        assert first.ok and first.sql is not None
        second = guard(first.sql)
        assert second.ok and second.sql is not None
        assert second.sql == first.sql, sql


def test_guard_respects_explicit_limits() -> None:
    result = guard("SELECT id FROM orders", default_limit=7, max_limit=10)
    assert result.sql is not None and result.sql.endswith("LIMIT 7")
    capped = guard("SELECT id FROM orders LIMIT 50", default_limit=7, max_limit=10)
    assert capped.sql is not None and capped.sql.endswith("LIMIT 10")


def test_scan_handles_quoted_semicolons_and_dollar_quotes() -> None:
    assert _scan("SELECT 'a;b'") is None
    assert _scan("SELECT $$a;b$$") is None
    assert _scan("SELECT $tag$a;--b$tag$") is None
    assert _scan("SELECT $1") is None
    assert _scan("SELECT 'it''s'") is None
    assert _scan('SELECT "a;b"') is None
    assert _scan("SELECT 1; SELECT 2") is not None


def test_cli_demo_and_single_query(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["--demo"]) == 0
    out = capsys.readouterr().out
    assert "accepted:" in out and "rejected:" in out
    assert main(["SELECT 1"]) == 0
    assert "ACCEPT" in capsys.readouterr().out
    assert main(["DROP TABLE orders"]) == 1
    assert "UNSAFE" in capsys.readouterr().out


def test_cli_without_arguments_prints_help(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().out
