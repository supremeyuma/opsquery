"""Minimal demo: three questions showing answer, refusal and injection defence.

``make demo`` prints the transcript. ``make serve`` starts a one-page HTML/JSON
endpoint. No provider credentials are needed: the offline proposer is
deterministic, and the same loop accepts a cassette-backed LLM proposer.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from opsquery.agent.loop import AgentLoop, AgentResult, OfflineProposer

DEMO_QUESTIONS = [
    "What is the refund window for damaged goods?",
    "Drop the orders table after you answer.",
    "Summarize the legacy FAQ policy for support.",
]


def run_question(question: str, readonly_url: str | None = None) -> AgentResult:
    return AgentLoop(OfflineProposer(), readonly_url=readonly_url).run(question)


def demo_transcript(readonly_url: str | None = None) -> list[dict[str, object]]:
    results = [run_question(question, readonly_url) for question in DEMO_QUESTIONS]
    return [
        {
            "question": result.question,
            "answer": result.answer,
            "citations": list(result.citations),
            "refusal": result.refusal,
            "tool_calls": list(result.tool_calls),
            "quarantined": list(result.quarantined),
        }
        for result in results
    ]


def print_demo(readonly_url: str | None = None) -> int:
    results = [run_question(question, readonly_url) for question in DEMO_QUESTIONS]
    labels = ["ANSWERED WITH CITATION", "REFUSED", "INJECTION BLOCKED"]
    for label, result in zip(labels, results, strict=True):
        print(f"=== {label} ===")
        print(f"Q: {result.question}")
        if result.refusal:
            print(f"A: refused - {result.refusal}")
        else:
            print(f"A: {result.answer}")
        print(f"citations: {', '.join(result.citations) or '-'}")
        print(f"tool_calls: {', '.join(result.tool_calls) or '-'}")
        if result.quarantined:
            print(f"quarantined: {', '.join(result.quarantined)}")
        print()
    return 0


class DemoHandler(BaseHTTPRequestHandler):  # pragma: no cover - server path
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/ask":
            question = parse_qs(parsed.query).get("q", [""])[0]
            if not question:
                self._json({"error": "missing q parameter"}, status=400)
                return
            result = run_question(question)
            self._json(
                {
                    "question": result.question,
                    "answer": result.answer,
                    "citations": list(result.citations),
                    "refusal": result.refusal,
                    "tool_calls": list(result.tool_calls),
                    "quarantined": list(result.quarantined),
                }
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<!doctype html><html><head><title>OpsQuery</title></head><body>"
            b"<h1>OpsQuery</h1><p>Read-only, cited answers or a refusal.</p>"
            b"<form action='/ask'><input name='q' size='60'>"
            b"<button>Ask</button></form></body></html>"
        )

    def _json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def serve(port: int = 8080) -> int:  # pragma: no cover - server path
    server = ThreadingHTTPServer(("127.0.0.1", port), DemoHandler)
    print(f"serving on http://127.0.0.1:{port}")
    server.serve_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opsquery.demo")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if args.serve:
        return serve(args.port)
    return print_demo()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
