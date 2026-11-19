"""Golden eval case schema and loader."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from opsquery.db.migrate import repo_root

ALLOWED_TAGS = frozenset(
    {
        "aggregation",
        "filter",
        "join",
        "date",
        "null",
        "trap",
        "group",
        "topn",
        "refusal",
        "injection",
        "pii",
        "retrieval",
        "groundedness",
    }
)


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    question: str = Field(min_length=5)
    expected_sql: str | None = None
    expected_rows: list[list[Any]] | None = None
    expected_scalar: Any | None = None
    must_refuse: bool = False
    tags: list[str] = Field(min_length=1)
    split: Literal["train", "holdout"] = "train"

    @model_validator(mode="after")
    def _check_shape(self) -> Case:
        unknown = set(self.tags) - ALLOWED_TAGS
        if unknown:
            raise ValueError(f"unknown tags: {sorted(unknown)}")
        if self.must_refuse:
            if (
                self.expected_sql
                or self.expected_rows is not None
                or self.expected_scalar is not None
            ):
                raise ValueError("refusal cases cannot define expected results")
            return self
        defined = [
            self.expected_sql is not None,
            self.expected_rows is not None,
            self.expected_scalar is not None,
        ]
        if sum(defined) != 1:
            raise ValueError(
                "exactly one of expected_sql, expected_rows or expected_scalar is required"
            )
        return self


def cases_dir() -> Path:
    return repo_root() / "eval" / "cases"


def adversarial_dir() -> Path:
    return repo_root() / "eval" / "adversarial"


def _load_files(directory: Path) -> Iterable[Case]:
    if not directory.exists():
        return []
    loaded: list[Case] = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            continue
        if not isinstance(raw, list):
            raise ValueError(f"{path}: expected a YAML list of cases")
        for entry in raw:
            loaded.append(Case.model_validate(entry))
    return loaded


def load_cases(directory: Path | None = None, adversarial: Path | None = None) -> list[Case]:
    """Load and validate every case; duplicate ids anywhere are fatal."""

    cases = list(_load_files(directory or cases_dir()))
    cases.extend(_load_files(adversarial or adversarial_dir()))
    seen: set[str] = set()
    duplicates: set[str] = set()
    for case in cases:
        if case.id in seen:
            duplicates.add(case.id)
        seen.add(case.id)
    if duplicates:
        raise ValueError(f"duplicate case ids: {sorted(duplicates)}")
    if not cases:
        raise ValueError("no cases found")
    return sorted(cases, key=lambda case: case.id)


def load_gate_thresholds(path: Path | None = None) -> dict[str, float]:
    target = path or (repo_root() / "eval" / "thresholds.json")
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{target}: expected an object of thresholds")
    return {str(k): float(v) for k, v in raw.items()}
