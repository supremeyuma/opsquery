"""Read-only query executor.

Connections use the ``ops_readonly`` role: writes fail at the database even if
the guard is bypassed. The executor adds its own 5s timeout, a row cap and a
stable result hash used by the eval harness.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import psycopg

from opsquery.config import settings
from opsquery.sql.guard import ErrorCode, GuardResult, guard

TYPE_NAMES: dict[int, str] = {
    16: "bool",
    18: "char",
    19: "name",
    20: "int8",
    21: "int2",
    23: "int4",
    25: "text",
    114: "json",
    700: "float4",
    701: "float8",
    1042: "bpchar",
    1043: "varchar",
    1082: "date",
    1083: "time",
    1114: "timestamp",
    1184: "timestamptz",
    1186: "interval",
    1700: "numeric",
    2950: "uuid",
}


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type_name: str


@dataclass(frozen=True)
class ExecResult:
    ok: bool
    sql: str
    columns: tuple[ColumnInfo, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    row_count: int = 0
    truncated: bool = False
    result_hash: str = ""
    duration_ms: int = 0
    code: ErrorCode | None = None
    message: str = "ok"
    metadata: dict[str, Any] = field(default_factory=dict)


def result_hash(columns: tuple[ColumnInfo, ...], rows: tuple[tuple[Any, ...], ...]) -> str:
    """Order-insensitive hash of a typed result set."""

    canonical_rows = sorted(
        json.dumps(list(row), default=str, sort_keys=True, ensure_ascii=True) for row in rows
    )
    payload = json.dumps(
        {"columns": [[c.name, c.type_name] for c in columns], "rows": canonical_rows},
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class Executor:
    """Execute guarded SQL against the read-only role."""

    def __init__(
        self,
        readonly_url: str | None = None,
        *,
        max_rows: int | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        cfg = settings()
        self.readonly_url = readonly_url or cfg.readonly_url
        self.max_rows = max_rows if max_rows is not None else cfg.max_rows
        self.timeout_ms = timeout_ms if timeout_ms is not None else cfg.statement_timeout_ms

    def execute(self, sql: str, *, enforce_guard: bool = True) -> ExecResult:
        checked: GuardResult = (
            guard(sql, max_limit=self.max_rows) if enforce_guard else (GuardResult.accept(sql))
        )
        if not checked.ok or checked.sql is None:
            return ExecResult(
                ok=False,
                sql=sql,
                code=checked.code,
                message=checked.message,
            )

        started = time.perf_counter()
        try:
            with psycopg.connect(self.readonly_url, connect_timeout=10) as conn:
                conn.execute(f"SET statement_timeout = {int(self.timeout_ms)}")
                with conn.cursor() as cur:
                    cur.execute(checked.sql)
                    columns = tuple(
                        ColumnInfo(
                            name=str(desc.name),
                            type_name=TYPE_NAMES.get(desc.type_code, f"oid:{desc.type_code}"),
                        )
                        for desc in (cur.description or ())
                    )
                    fetched = cur.fetchmany(self.max_rows + 1) if columns else []
                duration = int((time.perf_counter() - started) * 1000)
                truncated = len(fetched) > self.max_rows
                rows = tuple(tuple(row) for row in fetched[: self.max_rows])
                conn.rollback()
        except psycopg.errors.QueryCanceled:
            duration = int((time.perf_counter() - started) * 1000)
            return ExecResult(
                ok=False,
                sql=checked.sql,
                duration_ms=duration,
                code=ErrorCode.TIMEOUT,
                message=f"query exceeded {self.timeout_ms}ms",
            )
        except psycopg.Error as exc:
            duration = int((time.perf_counter() - started) * 1000)
            return ExecResult(
                ok=False,
                sql=checked.sql,
                duration_ms=duration,
                code=ErrorCode.DB_ERROR,
                message=f"database rejected query: {exc}",
            )

        return ExecResult(
            ok=True,
            sql=checked.sql,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            result_hash=result_hash(columns, rows),
            duration_ms=duration,
        )
