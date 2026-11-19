"""Execution-accuracy runner.

Every case runs the planner, the guard and the read-only executor against the
seeded database. Expected results come from the case's golden SQL, never from
the planner's output. The report is written to ``reports/eval.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from opsquery.agent.planner import NaivePlanner, Plan, plan
from opsquery.config import settings
from opsquery.db.executor import ExecResult, Executor
from opsquery.db.migrate import repo_root
from opsquery.eval.dataset import Case, load_cases, load_gate_thresholds


def _normalise(value: Any) -> Any:
    if isinstance(value, Decimal):
        return ("num", str(value.normalize()))
    if isinstance(value, datetime):
        return ("ts", value.isoformat())
    if isinstance(value, date):
        return ("date", value.isoformat())
    if isinstance(value, float):
        return ("num", repr(round(value, 6)))
    if isinstance(value, bool):
        return ("bool", value)
    return value


def rows_equal(
    expected: list[list[Any]] | tuple[tuple[Any, ...], ...],
    actual: tuple[tuple[Any, ...], ...],
) -> bool:
    """Order-insensitive, typed comparison of result sets."""

    expected_counter: Counter[tuple[Any, ...]] = Counter(
        tuple(_normalise(v) for v in row) for row in expected
    )
    actual_counter: Counter[tuple[Any, ...]] = Counter(
        tuple(_normalise(v) for v in row) for row in actual
    )
    return expected_counter == actual_counter


def _expected_rows(case: Case, executor: Executor) -> tuple[tuple[Any, ...], ...]:
    if case.expected_rows is not None:
        return tuple(tuple(row) for row in case.expected_rows)
    if case.expected_scalar is not None:
        return ((case.expected_scalar,),)
    assert case.expected_sql is not None
    result = executor.execute(case.expected_sql)
    if not result.ok:
        raise RuntimeError(f"golden SQL for {case.id} failed: {result.code} {result.message}")
    return result.rows


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    ok: bool
    reason: str
    false_execution: bool
    latency_ms: int
    tags: tuple[str, ...]


def _score_sql_case(case: Case, plan_fn: Any, executor: Executor) -> CaseOutcome:
    started = time.perf_counter()
    chosen: Plan = plan_fn(case.question)
    false_execution = False
    if chosen.refused:
        ok = False
        reason = f"refused: {chosen.refusal}"
    else:
        assert chosen.sql is not None
        actual = executor.execute(chosen.sql)
        if not actual.ok:
            ok = False
            reason = f"execution failed: {actual.code} {actual.message}"
        else:
            expected = _expected_rows(case, executor)
            ok = rows_equal(expected, actual.rows)
            reason = "ok" if ok else "result-set mismatch"
        if chosen.sql.strip().lower().startswith(("insert", "update", "delete", "drop")):
            false_execution = True
    latency = int((time.perf_counter() - started) * 1000)
    return CaseOutcome(case.id, ok, reason, false_execution, latency, tuple(case.tags))


def _score_refusal_case(case: Case, plan_fn: Any, executor: Executor) -> CaseOutcome:
    started = time.perf_counter()
    chosen: Plan = plan_fn(case.question)
    false_execution = False
    if chosen.refused:
        ok, reason = True, f"refused: {chosen.refusal}"
    else:
        assert chosen.sql is not None
        executed: ExecResult = executor.execute(chosen.sql)
        false_execution = executed.ok
        ok = False
        reason = "unsafe request was not refused"
    latency = int((time.perf_counter() - started) * 1000)
    return CaseOutcome(case.id, ok, reason, false_execution, latency, tuple(case.tags))


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def run_eval(
    cases: list[Case] | None = None,
    executor: Executor | None = None,
) -> dict[str, Any]:
    loaded = cases if cases is not None else load_cases()
    ex = executor or Executor(settings().readonly_url)

    scored = [case for case in loaded if not case.must_refuse]
    adversarial = [case for case in loaded if case.must_refuse]

    outcomes = [_score_sql_case(case, plan, ex) for case in scored]
    adversarial_outcomes = [_score_refusal_case(case, plan, ex) for case in adversarial]
    baseline_outcomes = [_score_sql_case(case, NaivePlanner().plan, ex) for case in scored]

    correct = sum(1 for outcome in outcomes if outcome.ok)
    baseline_correct = sum(1 for outcome in baseline_outcomes if outcome.ok)
    refused = sum(1 for outcome in adversarial_outcomes if outcome.ok)
    false_executions = sum(1 for outcome in adversarial_outcomes if outcome.false_execution)

    per_tag: dict[str, dict[str, Any]] = {}
    for case, outcome in zip(scored, outcomes, strict=True):
        for tag in case.tags:
            bucket = per_tag.setdefault(tag, {"total": 0, "correct": 0, "accuracy": 0.0})
            bucket["total"] += 1
            bucket["correct"] += int(outcome.ok)
    for bucket in per_tag.values():
        bucket["accuracy"] = round(bucket["correct"] / bucket["total"], 4)

    holdout = [
        outcome for case, outcome in zip(scored, outcomes, strict=True) if case.split == "holdout"
    ]
    holdout_correct = sum(1 for outcome in holdout if outcome.ok)

    latencies = [outcome.latency_ms for outcome in outcomes + adversarial_outcomes]
    failures = [
        {"id": outcome.case_id, "reason": outcome.reason}
        for outcome in outcomes + adversarial_outcomes
        if not outcome.ok
    ]

    return {
        "cases": len(loaded),
        "scored_cases": len(scored),
        "correct": correct,
        "execution_accuracy": round(correct / len(scored), 4) if scored else 0.0,
        "baseline": {
            "correct": baseline_correct,
            "accuracy": round(baseline_correct / len(scored), 4) if scored else 0.0,
        },
        "holdout": {
            "correct": holdout_correct,
            "total": len(holdout),
            "accuracy": round(holdout_correct / len(holdout), 4) if holdout else 0.0,
        },
        "per_tag": dict(sorted(per_tag.items())),
        "adversarial": {
            "total": len(adversarial),
            "refused": refused,
            "refusal_rate": round(refused / len(adversarial), 4) if adversarial else 0.0,
            "false_executions": false_executions,
        },
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "cost": {
            "tokens": 0,
            "usd": 0.0,
            "note": "deterministic offline planner; no LLM calls during eval",
        },
        "failures": failures,
    }


def check_gate(report: dict[str, Any], thresholds: dict[str, float]) -> list[str]:
    failures: list[str] = []
    accuracy = float(report["execution_accuracy"])
    floor = thresholds.get("execution_accuracy", 0.0)
    if accuracy < floor:
        failures.append(f"execution accuracy {accuracy:.4f} below floor {floor:.4f}")
    refusal_floor = thresholds.get("adversarial_refusal_rate", 1.0)
    refusal_rate = float(report["adversarial"]["refusal_rate"])
    if refusal_rate < refusal_floor:
        failures.append(f"adversarial refusal rate {refusal_rate:.4f} below {refusal_floor:.4f}")
    max_false = thresholds.get("max_false_executions", 0.0)
    if float(report["adversarial"]["false_executions"]) > max_false:
        failures.append(
            f"{report['adversarial']['false_executions']} unsafe queries reached the database"
        )
    return failures


def write_report(report: dict[str, Any]) -> Path:
    target = repo_root() / "reports" / "eval.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opsquery.eval.runner")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args(argv)

    report = run_eval()
    if args.write_report:
        target = write_report(report)
        print(f"eval: wrote {target}")
    print(
        "eval: accuracy={:.4f} ({}/{}) baseline={:.4f} holdout={:.4f} "
        "refusal={:.4f} false_executions={}".format(
            report["execution_accuracy"],
            report["correct"],
            report["scored_cases"],
            report["baseline"]["accuracy"],
            report["holdout"]["accuracy"],
            report["adversarial"]["refusal_rate"],
            report["adversarial"]["false_executions"],
        )
    )
    if args.gate:
        failures = check_gate(report, load_gate_thresholds())
        if failures:
            for failure in failures:
                print(f"eval-gate: FAIL {failure}", file=sys.stderr)
            return 1
        print("eval-gate: ok")
    elif report["failures"]:
        for failure in report["failures"]:
            print(f"eval: failure {failure['id']}: {failure['reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
