"""Deterministic compact schema catalog for prompting.

Output is byte-identical for the same database contents. ``--check`` compares
against the committed golden file used by ``make introspect-check``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from opsquery.config import settings
from opsquery.db.migrate import repo_root

CATALOG_TABLES = (
    "customers",
    "inventory",
    "invoices",
    "order_items",
    "orders",
    "payments",
    "products",
    "support_tickets",
    "suppliers",
)

HEADER = "OpsQuery catalog v1"


def _format_sample(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).replace("\n", " ").strip()
    return text[:40]


def _columns(conn: psycopg.Connection[Any]) -> list[tuple[str, str, str, bool, bool]]:
    rows = conn.execute(
        """
        SELECT c.relname,
               a.attname,
               format_type(a.atttypid, a.atttypmod),
               a.attnotnull,
               EXISTS (
                   SELECT 1 FROM pg_index i
                   WHERE i.indrelid = c.oid AND i.indisprimary
                     AND a.attnum = ANY (i.indkey)
               ) AS is_pk
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND a.attnum > 0
          AND NOT a.attisdropped
          AND c.relname = ANY (%s)
        ORDER BY c.relname, a.attnum
        """,
        (list(CATALOG_TABLES),),
    ).fetchall()
    return [(str(r[0]), str(r[1]), str(r[2]), bool(r[3]), bool(r[4])) for r in rows]


def _foreign_keys(conn: psycopg.Connection[Any]) -> list[tuple[str, str, str, str]]:
    rows = conn.execute(
        """
        SELECT con.conrelid::regclass::text,
               a.attname,
               con.confrelid::regclass::text,
               af.attname
        FROM pg_constraint con
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = con.conkey[1]
        JOIN pg_attribute af ON af.attrelid = con.confrelid AND af.attnum = con.confkey[1]
        WHERE con.contype = 'f'
          AND con.connamespace = 'public'::regnamespace
        ORDER BY 1, 2
        """
    ).fetchall()
    return [(str(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows]


def render_catalog(conn: psycopg.Connection[Any]) -> str:
    """Render the catalog text. Deterministic ordering, no environment data."""

    columns = _columns(conn)
    by_table: dict[str, list[tuple[str, str, bool, bool]]] = {t: [] for t in CATALOG_TABLES}
    for table, column, type_name, not_null, is_pk in columns:
        by_table.setdefault(table, []).append((column, type_name, not_null, is_pk))

    fks = _foreign_keys(conn)
    fk_by_table: dict[str, list[str]] = {t: [] for t in CATALOG_TABLES}
    for table, column, ref_table, ref_column in fks:
        simple_table = table.split(".")[-1]
        simple_ref = ref_table.split(".")[-1]
        if simple_table in fk_by_table:
            fk_by_table[simple_table].append(f"  fk {column} -> {simple_ref}.{ref_column}")

    lines = [HEADER]
    for table in sorted(CATALOG_TABLES):
        count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
        assert count is not None
        lines.append(f"table {table} rows={count[0]}")
        for column, type_name, not_null, is_pk in by_table.get(table, []):
            flags = []
            if is_pk:
                flags.append("pk")
            if not_null:
                flags.append("not null")
            suffix = f" [{' '.join(flags)}]" if flags else ""
            samples = conn.execute(
                f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL ORDER BY id LIMIT 3"
            ).fetchall()
            sample_text = " | ".join(_format_sample(r[0]) for r in samples) or "-"
            lines.append(f"  {column} {type_name}{suffix} samples: {sample_text}")
        lines.extend(sorted(fk_by_table.get(table, [])))
    return "\n".join(lines) + "\n"


def golden_path() -> Path:
    return repo_root() / "db" / "catalog.golden.txt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opsquery.db.introspect")
    parser.add_argument("--check", action="store_true", help="compare with the golden file")
    parser.add_argument("--write", action="store_true", help="rewrite the golden file")
    args = parser.parse_args(argv)

    with psycopg.connect(settings().readonly_url) as conn:
        catalog = render_catalog(conn)
    target = golden_path()
    if args.write:
        target.write_text(catalog, encoding="utf-8", newline="\n")
        print(f"introspect: wrote {target}")
        return 0
    if args.check:
        expected = target.read_text(encoding="utf-8") if target.exists() else ""
        if catalog != expected:
            print("introspect: catalog differs from db/catalog.golden.txt", file=sys.stderr)
            import difflib

            diff = difflib.unified_diff(
                expected.splitlines(), catalog.splitlines(), "golden", "live", lineterm=""
            )
            for line in list(diff)[:80]:
                print(line, file=sys.stderr)
            return 1
        print(f"introspect-check: ok ({len(catalog.splitlines())} lines, byte-identical)")
        return 0
    sys.stdout.write(catalog)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
