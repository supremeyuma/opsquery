"""PII detection and masking for results and retrieved context."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from opsquery.db.executor import ExecResult, result_hash

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?:\+1[- ]?)?\(?\d{3}\)?[- ]\d{3}[- ]\d{4}\b")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
ACCOUNT = re.compile(r"\bACCT[- ]?\d{6,}\b")

SENSITIVE_COLUMNS = frozenset({"email", "phone"})
COLUMN_MASKS = {"email": "[redacted-email]", "phone": "[redacted-phone]"}


@dataclass(frozen=True)
class RedactionReport:
    redactions: int
    columns: tuple[str, ...]


def redact_text(text: str) -> tuple[str, int]:
    """Redact email, phone, card-like and account-like values from free text."""

    total = 0
    redacted = text
    for pattern, label in (
        (EMAIL, "[redacted-email]"),
        (PHONE, "[redacted-phone]"),
        (CARD, "[redacted-card]"),
        (ACCOUNT, "[redacted-account]"),
    ):
        redacted, count = pattern.subn(label, redacted)
        total += count
    return redacted, total


def redact_result(result: ExecResult) -> tuple[ExecResult, RedactionReport]:
    """Mask sensitive columns and free-text values, preserving the row shape."""

    if not result.ok:
        return result, RedactionReport(0, ())
    sensitive = tuple(
        column.name for column in result.columns if column.name.lower() in SENSITIVE_COLUMNS
    )
    rows: list[tuple[Any, ...]] = []
    redactions = 0
    for row in result.rows:
        masked: list[Any] = []
        for column, value in zip(result.columns, row, strict=True):
            name = column.name.lower()
            if name in SENSITIVE_COLUMNS and value is not None:
                masked.append(COLUMN_MASKS[name])
                redactions += 1
            elif isinstance(value, str):
                cleaned, count = redact_text(value)
                masked.append(cleaned)
                redactions += count
            else:
                masked.append(value)
        rows.append(tuple(masked))
    new_rows = tuple(rows)
    updated = replace(
        result,
        rows=new_rows,
        row_count=len(new_rows),
        result_hash=result_hash(result.columns, new_rows),
    )
    return updated, RedactionReport(redactions, sensitive)
