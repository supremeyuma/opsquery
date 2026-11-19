"""Runtime configuration for OpsQuery.

All values are environment-overridable so the same code runs against the local
compose database and against a CI service container.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = "postgresql://ops_admin:ops_admin_dev@localhost:55432/opsquery"
DEFAULT_READONLY_URL = "postgresql://ops_readonly:ops_readonly_dev@localhost:55432/opsquery"


@dataclass(frozen=True)
class Settings:
    """Resolved settings for one process."""

    database_url: str
    readonly_url: str
    statement_timeout_ms: int = 5000
    max_rows: int = 10_000
    default_limit: int = 500

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ.get("OPSQUERY_DATABASE_URL", DEFAULT_DATABASE_URL),
            readonly_url=os.environ.get("OPSQUERY_READONLY_URL", DEFAULT_READONLY_URL),
            statement_timeout_ms=int(os.environ.get("OPSQUERY_STATEMENT_TIMEOUT_MS", "5000")),
            max_rows=int(os.environ.get("OPSQUERY_MAX_ROWS", "10000")),
            default_limit=int(os.environ.get("OPSQUERY_DEFAULT_LIMIT", "500")),
        )


def settings() -> Settings:
    """Return settings derived from the current environment."""

    return Settings.from_env()
