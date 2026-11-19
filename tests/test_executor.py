from __future__ import annotations

import psycopg

from opsquery.db.executor import Executor, result_hash
from opsquery.sql.guard import ErrorCode


def executor(seeded_db) -> Executor:  # type: ignore[no-untyped-def]
    return Executor(seeded_db.readonly_url, max_rows=100, timeout_ms=2000)


def test_executor_returns_typed_rows_and_hash(seeded_db) -> None:  # type: ignore[no-untyped-def]
    result = executor(seeded_db).execute(
        "SELECT 1::bigint AS n, 2.5::numeric AS amount, 'x'::text AS label, true AS flag"
    )
    assert result.ok, result.message
    assert [c.name for c in result.columns] == ["n", "amount", "label", "flag"]
    assert [c.type_name for c in result.columns] == ["int8", "numeric", "text", "bool"]
    assert result.rows == ((1, 2.5, "x", True),)
    assert result.result_hash == result_hash(result.columns, result.rows)


def test_executor_hash_is_order_insensitive(seeded_db) -> None:  # type: ignore[no-untyped-def]
    ex = executor(seeded_db)
    ascending = ex.execute("SELECT id FROM orders WHERE id <= 5 ORDER BY id")
    descending = ex.execute("SELECT id FROM orders WHERE id <= 5 ORDER BY id DESC")
    assert ascending.ok and descending.ok
    assert ascending.result_hash == descending.result_hash


def test_executor_enforces_row_cap(seeded_db) -> None:  # type: ignore[no-untyped-def]
    result = executor(seeded_db).execute("SELECT n FROM generate_series(1, 500) AS n")
    assert result.ok
    assert result.row_count == 100
    assert result.truncated is True


def test_executor_enforces_timeout(seeded_db) -> None:  # type: ignore[no-untyped-def]
    ex = Executor(seeded_db.readonly_url, max_rows=10, timeout_ms=150)
    result = ex.execute("SELECT count(*) FROM orders a CROSS JOIN orders b CROSS JOIN orders c")
    assert not result.ok
    assert result.code is ErrorCode.TIMEOUT


def test_executor_rejects_unguarded_writes_before_touching_db(seeded_db) -> None:  # type: ignore[no-untyped-def]
    result = executor(seeded_db).execute("DROP TABLE orders")
    assert not result.ok
    assert result.code is ErrorCode.UNSAFE
    with psycopg.connect(seeded_db.readonly_url) as conn:
        assert conn.execute("SELECT count(*) FROM orders").fetchone() == (50_000,)


def test_executor_surfaces_database_errors(seeded_db) -> None:  # type: ignore[no-untyped-def]
    ex = executor(seeded_db)
    result = ex.execute("SELECT 1/0 AS boom")
    assert not result.ok
    assert result.code is ErrorCode.DB_ERROR
    assert "division by zero" in result.message


def test_executor_guard_bypass_is_still_read_only(seeded_db) -> None:  # type: ignore[no-untyped-def]
    result = executor(seeded_db).execute(
        "INSERT INTO customers (name, email, segment, city, created_at) "
        "VALUES ('x', 'x@example.com', 'retail', 'Austin', now())",
        enforce_guard=False,
    )
    assert not result.ok
    assert "read-only" in result.message.lower()
