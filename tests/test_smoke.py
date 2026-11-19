from __future__ import annotations

from opsquery import __version__
from opsquery.cli import main
from opsquery.config import Settings


def test_version_string() -> None:
    assert __version__ == "0.1.0"


def test_settings_defaults(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for key in (
        "OPSQUERY_DATABASE_URL",
        "OPSQUERY_READONLY_URL",
        "OPSQUERY_STATEMENT_TIMEOUT_MS",
        "OPSQUERY_MAX_ROWS",
        "OPSQUERY_DEFAULT_LIMIT",
    ):
        monkeypatch.delenv(key, raising=False)
    s = Settings.from_env()
    assert s.statement_timeout_ms == 5000
    assert s.max_rows == 10_000
    assert "ops_readonly" in s.readonly_url


def test_settings_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPSQUERY_MAX_ROWS", "7")
    monkeypatch.setenv("OPSQUERY_DEFAULT_LIMIT", "3")
    s = Settings.from_env()
    assert (s.max_rows, s.default_limit) == (7, 3)


def test_cli_version(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_cli_help(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main([]) == 0
    assert "usage:" in capsys.readouterr().out
