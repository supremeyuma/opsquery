"""Explicit cassette recording. Never runs by default and never in tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from opsquery.db.migrate import repo_root
from opsquery.llm.client import Message, cassette_key, provider_from_env


def default_requests() -> list[list[Message]]:
    questions = [
        "How many orders were placed in 2025?",
        "What is the total value of refunds?",
        "Which customer names appear more than once?",
    ]
    return [[Message(role="user", content=question)] for question in questions]


def record(requests: list[list[Message]] | None = None) -> Path:
    if os.environ.get("OPSQUERY_RECORD") != "1":
        raise SystemExit(
            "record: refusing to record without OPSQUERY_RECORD=1 "
            "(recording is deliberate and reviewed)"
        )
    provider = provider_from_env()
    if provider is None:
        raise SystemExit("record: OPSQUERY_LLM_URL and OPSQUERY_LLM_API_KEY are required")
    entries: list[dict[str, Any]] = []
    for messages in requests or default_requests():
        completion = provider.complete(messages)
        entries.append(
            {
                "key": cassette_key(messages),
                "request": {"messages": [m.as_dict() for m in messages], "tools": []},
                "response": {
                    "content": completion.content,
                    "tool_calls": [
                        {"name": call.name, "arguments": call.arguments}
                        for call in completion.tool_calls
                    ],
                    "prompt_tokens": completion.prompt_tokens,
                    "completion_tokens": completion.completion_tokens,
                },
            }
        )
    target = repo_root() / "eval" / "cassettes" / "recorded.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"version": 1, "model": provider.model, "entries": entries}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def main() -> int:
    print(f"record: wrote {record()}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
