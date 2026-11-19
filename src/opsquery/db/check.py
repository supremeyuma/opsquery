"""Connectivity check used by ``make db-check``."""

from __future__ import annotations

import sys

import psycopg

from opsquery.config import settings


def check() -> str:
    cfg = settings()
    with psycopg.connect(cfg.database_url, connect_timeout=10) as conn:
        row = conn.execute("select version(), current_database()").fetchone()
        assert row is not None
        version, database = row
        ext = conn.execute(
            "select default_version from pg_available_extensions where name = 'vector'"
        ).fetchone()
    vector = ext[0] if ext else "MISSING"
    return f"db-check: ok database={database} pgvector={vector} server={str(version).split(',')[0]}"


def main() -> int:
    try:
        print(check())
    except Exception as exc:  # pragma: no cover - CLI failure path
        print(f"db-check: FAILED {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
