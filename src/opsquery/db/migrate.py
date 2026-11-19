"""Apply SQL migrations and the read-only role.

Migrations live in ``db/migrations`` and are applied in filename order, once,
inside a transaction. The roles file is applied on every run and is safe to
re-run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import psycopg

from opsquery.config import settings

MIGRATION_SCHEMA = "ops_migrations"


def repo_root() -> Path:
    """Locate the repository root, allowing an explicit override for tests/packaging."""

    env = os.environ.get("OPSQUERY_DB_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3]


def migrations_dir() -> Path:
    return repo_root() / "db" / "migrations"


def roles_path() -> Path:
    return repo_root() / "db" / "roles.sql"


def apply_migrations(conn: psycopg.Connection[Any]) -> list[str]:
    """Apply pending migration files. Returns the versions applied this run."""

    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {MIGRATION_SCHEMA}")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MIGRATION_SCHEMA}.schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()

    applied = {
        row[0]
        for row in conn.execute(
            f"SELECT version FROM {MIGRATION_SCHEMA}.schema_migrations"
        ).fetchall()
    }

    newly_applied: list[str] = []
    for path in sorted(migrations_dir().glob("*.sql")):
        version = path.stem
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        with conn.transaction():
            conn.execute(sql)
            conn.execute(
                f"INSERT INTO {MIGRATION_SCHEMA}.schema_migrations (version) VALUES (%s)",
                (version,),
            )
        newly_applied.append(version)
    return newly_applied


def apply_roles(conn: psycopg.Connection[Any]) -> None:
    """Apply the read-only role grants for the connected database."""

    database = conn.info.dbname
    sql = roles_path().read_text(encoding="utf-8").replace("__DATABASE__", database)
    with conn.transaction():
        conn.execute(sql)


def migrate(url: str | None = None) -> list[str]:
    """Run migrations and roles against ``url`` (defaults to the admin URL)."""

    target = url or settings().database_url
    with psycopg.connect(target) as conn:
        applied = apply_migrations(conn)
        apply_roles(conn)
    return applied


def main() -> int:
    applied = migrate()
    if applied:
        print("migrate: applied " + ", ".join(applied))
    else:
        print("migrate: up to date")
    print("migrate: role ops_readonly ensured")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
