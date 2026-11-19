from __future__ import annotations

from pathlib import Path

import pytest

from opsquery import db as _db  # noqa: F401
from opsquery.config import Settings
from opsquery.db import check as db_check
from opsquery.db import introspect
from opsquery.db import migrate as migrate_module
from opsquery.db.executor import ExecResult
from opsquery.demo import demo_transcript, print_demo
from opsquery.eval import report as report_module
from opsquery.eval import runner as runner_module
from opsquery.eval.dataset import load_cases, load_gate_thresholds
from opsquery.llm import client as client_module
from opsquery.llm.client import Completion, Message, ProviderClient
from opsquery.llm.prompts import SYSTEM_PROMPT, rag_prompt, sql_prompt, untrusted_block
from opsquery.llm.record import record
from opsquery.observability.trace import TraceRecorder
from opsquery.rag.cite import answer_from_chunks
from opsquery.rag.index import RetrievedChunk
from opsquery.security.redact import redact_result


def _settings_for(url: str) -> Settings:
    return Settings(database_url=url, readonly_url=url)


def test_demo_runs_three_scenarios(seeded_db, capsys) -> None:  # type: ignore[no-untyped-def]
    transcript = demo_transcript(seeded_db.readonly_url)
    assert len(transcript) == 3
    assert transcript[0]["citations"]
    assert transcript[1]["refusal"]
    assert transcript[2]["quarantined"]
    assert print_demo(seeded_db.readonly_url) == 0
    output = capsys.readouterr().out
    assert "ANSWERED WITH CITATION" in output
    assert "REFUSED" in output
    assert "INJECTION BLOCKED" in output


def test_report_cli_sections(capsys) -> None:  # type: ignore[no-untyped-def]
    assert report_module.main(["--section", "eval"]) == 0
    assert "SQL execution accuracy" in capsys.readouterr().out
    assert report_module.main(["--section", "rag"]) == 0
    assert "Retrieval recall@5" in capsys.readouterr().out


def test_report_missing_file(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(report_module, "report_path", lambda: Path("does-not-exist.json"))
    with pytest.raises(SystemExit):
        report_module.load_report()


def _minimal_report() -> dict[str, object]:
    return {
        "cases": 3,
        "scored_cases": 2,
        "correct": 2,
        "execution_accuracy": 1.0,
        "baseline": {"correct": 0, "accuracy": 0.0},
        "holdout": {"correct": 1, "total": 1, "accuracy": 1.0},
        "per_tag": {"aggregation": {"total": 2, "correct": 2, "accuracy": 1.0}},
        "adversarial": {"total": 1, "refused": 1, "refusal_rate": 1.0, "false_executions": 0},
        "latency_ms": {"p50": 1, "p95": 2},
        "cost": {"tokens": 0, "usd": 0.0},
        "failures": [],
        "rag": {"recall_at_5": 1.0, "groundedness": 1.0, "poisoned_tool_calls": 0},
    }


def test_runner_cli_gate_and_write(monkeypatch, capsys, tmp_path) -> None:  # type: ignore[no-untyped-def]
    report = _minimal_report()
    monkeypatch.setattr(runner_module, "run_eval", lambda: report)
    monkeypatch.setattr(
        runner_module,
        "load_gate_thresholds",
        lambda: {"execution_accuracy": 1.0, "adversarial_refusal_rate": 1.0},
    )
    assert runner_module.main(["--gate"]) == 0
    assert "eval-gate: ok" in capsys.readouterr().out

    failing = dict(report)
    failing["execution_accuracy"] = 0.0
    monkeypatch.setattr(runner_module, "run_eval", lambda: failing)
    assert runner_module.main(["--gate"]) == 1
    assert "FAIL" in capsys.readouterr().err

    target = tmp_path / "eval.json"
    monkeypatch.setattr(runner_module, "write_report", lambda _report: target)
    assert runner_module.main(["--write-report"]) == 0
    assert "wrote" in capsys.readouterr().out


def test_runner_error_branches(seeded_db) -> None:  # type: ignore[no-untyped-def]
    from opsquery.agent.planner import Plan
    from opsquery.eval.dataset import Case
    from opsquery.eval.runner import _score_refusal_case, _score_sql_case

    executor = runner_module.Executor(seeded_db.readonly_url)
    refused_case = Case(
        id="unexpected_refusal", question="count orders", expected_scalar=1, tags=["aggregation"]
    )
    outcome = _score_sql_case(refused_case, lambda _q: Plan.refuse("q", "no plan"), executor)
    assert not outcome.ok and "refused" in outcome.reason

    failing_case = Case(
        id="bad_sql", question="count orders", expected_scalar=1, tags=["aggregation"]
    )
    outcome = _score_sql_case(
        failing_case, lambda _q: Plan.answer("q", "SELECT 1/0 AS n", "fixture"), executor
    )
    assert not outcome.ok and "execution failed" in outcome.reason

    refusal_case = Case(
        id="should_refuse", question="drop orders", must_refuse=True, tags=["refusal"]
    )
    outcome = _score_refusal_case(
        refusal_case, lambda _q: Plan.answer("q", "SELECT 1 AS n", "fixture"), executor
    )
    assert outcome.false_execution
    assert not outcome.ok


def test_dataset_loader_edges(tmp_path: Path) -> None:
    cases = tmp_path / "cases"
    adversarial = tmp_path / "adversarial"
    cases.mkdir()
    adversarial.mkdir()
    with pytest.raises(ValueError, match="no cases found"):
        load_cases(cases, adversarial)

    (cases / "empty.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="no cases found"):
        load_cases(cases, adversarial)

    (cases / "bad.yaml").write_text("not-a-list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a YAML list"):
        load_cases(cases, adversarial)

    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected an object"):
        load_gate_thresholds(thresholds)


def test_db_check_and_migrate_main(seeded_db, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(db_check, "settings", lambda: _settings_for(seeded_db.admin_url))
    output = db_check.check()
    assert "db-check: ok" in output
    assert db_check.main() == 0
    assert "db-check: ok" in capsys.readouterr().out

    assert migrate_module.migrate(seeded_db.admin_url) == []
    monkeypatch.setattr(migrate_module, "settings", lambda: _settings_for(seeded_db.admin_url))
    assert migrate_module.main() == 0
    assert "up to date" in capsys.readouterr().out


def test_introspect_cli(seeded_db, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPSQUERY_READONLY_URL", seeded_db.readonly_url)
    assert introspect.main(["--check"]) == 0
    assert "byte-identical" in capsys.readouterr().out
    assert introspect.main([]) == 0
    assert "OpsQuery catalog v1" in capsys.readouterr().out


def test_provider_from_env(monkeypatch) -> None:
    monkeypatch.delenv("OPSQUERY_LLM_URL", raising=False)
    monkeypatch.delenv("OPSQUERY_LLM_API_KEY", raising=False)
    assert client_module.provider_from_env() is None
    monkeypatch.setenv("OPSQUERY_LLM_URL", "http://localhost:9")
    monkeypatch.setenv("OPSQUERY_LLM_API_KEY", "test")
    provider = client_module.provider_from_env()
    assert isinstance(provider, ProviderClient)


def test_record_success(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class FakeProvider:
        model = "fake"

        def complete(self, messages, tools=None):  # type: ignore[no-untyped-def]
            return Completion(content="SELECT 1", prompt_tokens=3, completion_tokens=2)

    monkeypatch.setenv("OPSQUERY_RECORD", "1")
    monkeypatch.setattr("opsquery.llm.record.provider_from_env", lambda: FakeProvider())
    monkeypatch.setattr("opsquery.llm.record.repo_root", lambda: tmp_path)
    target = record([[Message(role="user", content="one")]])
    assert target.exists()
    assert "SELECT 1" in target.read_text(encoding="utf-8")


def test_prompt_templates() -> None:
    assert "OpsQuery" in SYSTEM_PROMPT
    assert "catalog" in sql_prompt("catalog", "question")
    assert "Question: q" in sql_prompt("catalog", "q")
    assert untrusted_block("doc#000", "text").startswith("<untrusted_document")
    prompt = rag_prompt("q", [("doc#000", "text")])
    assert "<untrusted_document" in prompt and "untrusted data" in prompt


def test_trace_shutdown() -> None:
    recorder = TraceRecorder()
    with recorder.span("test", {"a": 1}):
        pass
    assert recorder.recorded()[0].attributes["a"] == 1
    recorder.shutdown()


def test_redact_failure_result_is_unchanged() -> None:
    result = ExecResult(ok=False, sql="bad", code=None, message="failed")
    redacted, report = redact_result(result)
    assert redacted is result
    assert report.redactions == 0


def test_answer_top_document_only() -> None:
    def chunk(chunk_id: str, doc_id: str, content: str) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            section="s",
            content=content,
            vector_score=1.0,
            lexical_score=1.0,
            score=1.0,
        )

    chunks = [
        chunk("a#000", "a", "Alpha fact."),
        chunk("b#000", "b", "Beta fact."),
    ]
    answer = answer_from_chunks("q", chunks, top_document_only=True)
    assert answer.citations == ("a#000",)
    assert "Beta" not in answer.text
