from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from opsquery.config import settings
from opsquery.db.migrate import apply_migrations, apply_roles
from opsquery.seed.generator import build_dataset, load


def _with_dbname(url: str, dbname: str) -> str:
    params = conninfo_to_dict(url)
    params["dbname"] = dbname
    return make_conninfo(**params)


@dataclass(frozen=True)
class TestDatabase:
    name: str
    admin_url: str
    readonly_url: str


@pytest.fixture(scope="session")
def admin_url() -> str:
    return settings().database_url


@pytest.fixture(scope="session")
def test_db(admin_url: str) -> Iterator[TestDatabase]:
    name = f"opsquery_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    admin = _with_dbname(admin_url, name)
    readonly = _with_dbname(settings().readonly_url, name)
    try:
        with psycopg.connect(admin) as conn:
            apply_migrations(conn)
            apply_roles(conn)
        yield TestDatabase(name=name, admin_url=admin, readonly_url=readonly)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture(scope="session")
def seeded_db(test_db: TestDatabase) -> TestDatabase:
    with psycopg.connect(test_db.admin_url) as conn:
        load(conn, build_dataset())
    return test_db


@pytest.fixture()
def ro_conn(seeded_db: TestDatabase) -> Iterator[psycopg.Connection[object]]:
    with psycopg.connect(seeded_db.readonly_url, autocommit=True) as conn:
        yield conn
