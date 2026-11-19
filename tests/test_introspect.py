from __future__ import annotations

import psycopg

from opsquery.db.introspect import CATALOG_TABLES, golden_path, render_catalog


def test_introspect_is_stable_and_compact(seeded_db) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(seeded_db.readonly_url) as conn:
        first = render_catalog(conn)
    with psycopg.connect(seeded_db.readonly_url) as conn:
        second = render_catalog(conn)
    assert first == second
    assert first.startswith("OpsQuery catalog v1\n")
    assert len(first.splitlines()) < 300
    for table in CATALOG_TABLES:
        assert f"table {table} rows=" in first


def test_introspect_matches_committed_golden(seeded_db) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(seeded_db.readonly_url) as conn:
        live = render_catalog(conn)
    golden = golden_path()
    assert golden.exists(), "db/catalog.golden.txt must be committed"
    assert live == golden.read_text(encoding="utf-8")


def test_introspect_includes_samples_and_foreign_keys(seeded_db) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(seeded_db.readonly_url) as conn:
        catalog = render_catalog(conn)
    assert "samples: " in catalog
    assert "fk customer_id -> customers.id" in catalog
    assert "orders rows=50000" in catalog
