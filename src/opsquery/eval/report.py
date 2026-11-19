"""Render the eval report as markdown. Numbers come only from reports/eval.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from opsquery.db.migrate import repo_root


def report_path() -> Path:
    return repo_root() / "reports" / "eval.json"


def load_report() -> dict[str, Any]:
    target = report_path()
    if not target.exists():
        raise SystemExit("reports/eval.json missing; run `make eval` first")
    data = json.loads(target.read_text(encoding="utf-8"))
    return dict(data)


def render(report: dict[str, Any]) -> str:
    adversarial = report["adversarial"]
    baseline = report["baseline"]
    holdout = report["holdout"]
    lines = [
        "| Metric | Baseline | OpsQuery |",
        "|---|---|---|",
        f"| SQL execution accuracy (golden set) | {baseline['accuracy']:.3f} | "
        f"{report['execution_accuracy']:.3f} |",
        f"| Held-out execution accuracy | - | {holdout['accuracy']:.3f} "
        f"({holdout['correct']}/{holdout['total']}) |",
        f"| Groundedness | - | {report.get('groundedness', {}).get('score', 0.0):.3f} |",
        f"| Unsafe-query refusal rate | 0.000 | {adversarial['refusal_rate']:.3f} |",
        f"| False executions in adversarial set | - | {adversarial['false_executions']} |",
        f"| Cost per query / p95 latency | - | "
        f"${report['cost']['usd']:.4f} / {report['latency_ms']['p95']} ms |",
        "",
        f"Scored cases: {report['scored_cases']} · adversarial: {adversarial['total']} · "
        f"failures: {len(report['failures'])}",
        "",
        "| Tag | Correct / total | Accuracy |",
        "|---|---|---|",
    ]
    for tag, bucket in report["per_tag"].items():
        lines.append(
            f"| {tag} | {bucket['correct']} / {bucket['total']} | {bucket['accuracy']:.3f} |"
        )
    return "\n".join(lines)


def render_rag(report: dict[str, Any]) -> str:
    rag = report.get("rag")
    if not rag:
        return "rag: not measured (run `make eval` after the retrieval milestone)"
    lines = [
        "| RAG metric | Value |",
        "|---|---|",
        f"| Retrieval recall@5 | {rag.get('recall_at_5', 0.0):.3f} |",
        f"| Citation groundedness | {rag.get('groundedness', 0.0):.3f} |",
        f"| Poisoned-document tool calls | {rag.get('poisoned_tool_calls', 0)} |",
        f"| Labelled queries | {rag.get('labelled_queries', 0)} |",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opsquery.eval.report")
    parser.add_argument("--section", choices=["eval", "rag"], default="eval")
    args = parser.parse_args(argv)
    report = load_report()
    print(render(report) if args.section == "eval" else render_rag(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
