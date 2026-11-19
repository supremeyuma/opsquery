from __future__ import annotations

import psycopg
import pytest

from opsquery.db.migrate import apply_migrations
from opsquery.seed.generator import build_dataset, load

EXPECTED_TABLES = {
    "customers",
    "products",
    "suppliers",
    "inventory",
    "orders",
    "order_items",
    "invoices",
    "payments",
    "support_tickets",
}


def _tables(conn: psycopg.Connection[object]) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    }


def test_migrations_apply_from_scratch(test_db) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(test_db.admin_url) as conn:
        assert _tables(conn) >= EXPECTED_TABLES
        ext = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        assert ext.fetchone() is not None
        fks = conn.execute(
            "SELECT count(*) FROM pg_constraint WHERE contype = 'f' "
            "AND connamespace = 'public'::regnamespace"
        ).fetchone()
        assert fks is not None and fks[0] >= 7


def test_migrations_are_idempotent(test_db) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(test_db.admin_url) as conn:
        assert apply_migrations(conn) == []


WRITE_STATEMENTS = [
    "INSERT INTO customers (name, email, segment, city, created_at) "
    "VALUES ('x', 'x@example.com', 'retail', 'Austin', now())",
    "UPDATE customers SET city = 'Boston'",
    "DELETE FROM orders",
    "TRUNCATE orders",
    "CREATE TABLE hacked (id integer)",
    "DROP TABLE orders",
    "ALTER TABLE orders ADD COLUMN hacked integer",
    "GRANT SELECT ON orders TO ops_readonly",
    "CREATE INDEX hacked_idx ON orders (status)",
]


@pytest.mark.parametrize("statement", WRITE_STATEMENTS)
def test_readonly_role_cannot_write(ro_conn, statement: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.Error):
        ro_conn.execute(statement)


def test_readonly_role_cannot_copy_in(ro_conn) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.Error), ro_conn.cursor().copy("COPY orders FROM STDIN"):
        pass


def test_readonly_role_can_select(ro_conn) -> None:  # type: ignore[no-untyped-def]
    row = ro_conn.execute("SELECT count(*) FROM orders").fetchone()
    assert row is not None and row[0] == 50_000


def test_readonly_role_has_safe_defaults(ro_conn) -> None:  # type: ignore[no-untyped-def]
    timeout = ro_conn.execute("SHOW statement_timeout").fetchone()
    read_only = ro_conn.execute("SHOW default_transaction_read_only").fetchone()
    assert timeout is not None and timeout[0] == "5s"
    assert read_only is not None and read_only[0] == "on"


def test_statement_timeout_is_enforced(ro_conn) -> None:  # type: ignore[no-untyped-def]
    ro_conn.execute("SET statement_timeout = '100ms'")
    with pytest.raises(psycopg.errors.QueryCanceled):
        ro_conn.execute("SELECT pg_sleep(1)")


def test_seed_loads_expected_counts(seeded_db) -> None:  # type: ignore[no-untyped-def]
    data = build_dataset()
    with psycopg.connect(seeded_db.readonly_url) as conn:
        for table, rows in data.items():
            got = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert got is not None and got[0] == len(rows), table


def test_seed_contains_awkward_rows(seeded_db) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(seeded_db.readonly_url) as conn:
        dupes = conn.execute("SELECT count(*) FROM customers WHERE name = 'Acme Corp'").fetchone()
        nulls = conn.execute("SELECT count(*) FROM customers WHERE phone IS NULL").fetchone()
        negatives = conn.execute(
            "SELECT count(*) FROM inventory WHERE quantity_on_hand < 0"
        ).fetchone()
        refunds = conn.execute("SELECT count(*) FROM payments WHERE amount < 0").fetchone()
        assert dupes is not None and dupes[0] >= 15
        assert nulls is not None and nulls[0] > 0
        assert negatives is not None and negatives[0] > 0
        assert refunds is not None and refunds[0] > 0


def test_load_is_idempotent(seeded_db) -> None:  # type: ignore[no-untyped-def]
    data = build_dataset()
    with psycopg.connect(seeded_db.admin_url) as conn:
        before = conn.execute("SELECT count(*) FROM orders").fetchone()
        load(conn, data)
        after = conn.execute("SELECT count(*) FROM orders").fetchone()
    assert before == after == (50_000,)
