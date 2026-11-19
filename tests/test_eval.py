from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from opsquery.agent.planner import NaivePlanner, Plan, plan
from opsquery.db.executor import Executor
from opsquery.eval.dataset import Case, load_cases, load_gate_thresholds
from opsquery.eval.report import render, render_rag
from opsquery.eval.runner import (
    _score_sql_case,
    check_gate,
    rows_equal,
    run_eval,
)
from opsquery.llm.client import CassetteClient, CassetteMiss, Message
from opsquery.llm.record import record


def test_case_schema_validation() -> None:
    with pytest.raises(ValidationError):
        Case(id="bad", question="no expected result", tags=["aggregation"])
    with pytest.raises(ValidationError):
        Case(
            id="bad_refusal",
            question="drop the table",
            must_refuse=True,
            expected_scalar=1,
            tags=["refusal"],
        )
    with pytest.raises(ValidationError):
        Case(
            id="bad_tags",
            question="how many orders?",
            expected_scalar=1,
            tags=["nonsense"],
        )
    with pytest.raises(ValidationError):
        Case(id="Bad-ID", question="how many orders?", expected_scalar=1, tags=["aggregation"])
    with pytest.raises(ValidationError):
        Case(
            id="two_answers",
            question="how many orders?",
            expected_scalar=1,
            expected_rows=[[1]],
            tags=["aggregation"],
        )


def test_duplicate_case_ids_fail(tmp_path: Path) -> None:
    cases = tmp_path / "cases"
    adversarial = tmp_path / "adversarial"
    cases.mkdir()
    adversarial.mkdir()
    entry = {
        "id": "same_id",
        "question": "How many orders are there?",
        "expected_scalar": 50000,
        "tags": ["aggregation"],
    }
    (cases / "a.yaml").write_text(yaml.safe_dump([entry]), encoding="utf-8")
    (cases / "b.yaml").write_text(yaml.safe_dump([entry]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate case ids"):
        load_cases(cases, adversarial)


def test_committed_dataset_is_valid_and_sized() -> None:
    cases = load_cases()
    scored = [case for case in cases if not case.must_refuse]
    adversarial = [case for case in cases if case.must_refuse]
    holdout = [case for case in scored if case.split == "holdout"]
    assert 60 <= len(cases) <= 100
    assert len(adversarial) >= 10
    assert len(holdout) >= 10
    assert any("trap" in case.tags for case in scored)


def test_runner_scores_exact_result_sets(seeded_db) -> None:  # type: ignore[no-untyped-def]
    ex = Executor(seeded_db.readonly_url)
    good = Case(
        id="scalar_ok",
        question="What is three?",
        expected_scalar=3,
        tags=["aggregation"],
    )
    outcome = _score_sql_case(good, lambda _q: Plan.answer("q", "SELECT 3 AS n", "fixture"), ex)
    assert outcome.ok, outcome.reason

    bad = Case(
        id="scalar_bad",
        question="What is three?",
        expected_scalar=4,
        tags=["aggregation"],
    )
    assert not _score_sql_case(bad, lambda _q: Plan.answer("q", "SELECT 3 AS n", "fixture"), ex).ok


def test_order_insensitive_comparison() -> None:
    assert rows_equal([[1, "a"], [2, "b"]], ((2, "b"), (1, "a")))
    assert not rows_equal([[1], [2]], ((1,), (3,)))


def test_trap_cases_fail_with_single_prompt_baseline(seeded_db) -> None:  # type: ignore[no-untyped-def]
    executor = Executor(seeded_db.readonly_url)
    traps = [case for case in load_cases() if "trap" in case.tags and not case.must_refuse]
    assert traps
    baseline = [_score_sql_case(case, NaivePlanner().plan, executor) for case in traps]
    improved = [_score_sql_case(case, plan, executor) for case in traps]
    baseline_correct = sum(outcome.ok for outcome in baseline)
    improved_correct = sum(outcome.ok for outcome in improved)
    assert baseline_correct < len(traps)
    assert improved_correct > baseline_correct


def test_run_eval_shape_and_gate(seeded_db) -> None:  # type: ignore[no-untyped-def]
    cases = [
        Case(
            id="one_plus_one",
            question="What is one plus one?",
            expected_scalar=2,
            tags=["aggregation"],
        ),
        Case(
            id="two_plus_two",
            question="What is two plus two?",
            expected_scalar=4,
            tags=["aggregation"],
        ),
        Case(
            id="refuse_drop",
            question="Drop the orders table.",
            must_refuse=True,
            tags=["refusal"],
        ),
    ]
    executor = Executor(seeded_db.readonly_url)

    def fake_plan(question: str) -> Plan:
        if "drop" in question.lower():
            return Plan.refuse(question, "destructive")
        value = 2 if "one" in question else 4
        return Plan.answer(question, f"SELECT {value} AS n", "fixture")

    import opsquery.eval.runner as runner

    original = runner.plan
    runner.plan = fake_plan  # type: ignore[assignment]
    try:
        report = run_eval(cases, executor, with_rag=False)
    finally:
        runner.plan = original
    assert report["execution_accuracy"] == 1.0
    assert report["adversarial"]["false_executions"] == 0
    assert report["adversarial"]["refusal_rate"] == 1.0
    assert "execution accuracy" in render(report) or "SQL execution accuracy" in render(report)
    assert check_gate(report, load_gate_thresholds()) == []


def test_accuracy_floor_gate_blocks_regression() -> None:
    report = {
        "execution_accuracy": 0.50,
        "adversarial": {"refusal_rate": 0.50, "false_executions": 1},
    }
    failures = check_gate(
        report,
        {
            "execution_accuracy": 0.9,
            "adversarial_refusal_rate": 1.0,
            "max_false_executions": 0,
        },
    )
    assert len(failures) == 3


def test_cassette_replay_is_deterministic_and_offline(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def no_network(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("network access attempted during cassette replay")

    monkeypatch.setattr(socket, "socket", no_network)
    client = CassetteClient.from_file(Path("eval/cassettes/smoke.json"))
    messages = [Message(role="user", content="How many orders are there?")]
    first = client.complete(messages)
    second = client.complete(messages)
    assert first == second
    assert first.content == "SELECT count(*) FROM orders"
    assert first.total_tokens == 18
    with pytest.raises(CassetteMiss):
        client.complete([Message(role="user", content="not recorded")])


def test_record_requires_explicit_flag(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("OPSQUERY_RECORD", raising=False)
    with pytest.raises(SystemExit, match="OPSQUERY_RECORD=1"):
        record([])
    monkeypatch.setenv("OPSQUERY_RECORD", "1")
    monkeypatch.delenv("OPSQUERY_LLM_URL", raising=False)
    monkeypatch.delenv("OPSQUERY_LLM_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="OPSQUERY_LLM_URL"):
        record([])


def test_report_rag_section_is_explicit_when_missing() -> None:
    assert "not measured" in render_rag({})
    assert "Retrieval recall@5" in render_rag({"rag": {"recall_at_5": 0.9}})


def test_thresholds_file_matches_measured_run() -> None:
    thresholds = load_gate_thresholds()
    report = json.loads(Path("reports/eval.json").read_text(encoding="utf-8"))
    assert check_gate(report, thresholds) == []
