"""LLM client with cassette replay for offline, deterministic tests."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Completion:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class CassetteMiss(RuntimeError):
    """The request is not in the cassette. Never fall back to the network."""


class LLMClient(Protocol):
    def complete(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> Completion: ...


def cassette_key(messages: list[Message], tools: list[dict[str, Any]] | None = None) -> str:
    payload = {
        "messages": [message.as_dict() for message in messages],
        "tools": tools or [],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _completion_from_dict(raw: dict[str, Any]) -> Completion:
    tool_calls = tuple(
        ToolCall(name=str(call["name"]), arguments=dict(call.get("arguments", {})))
        for call in raw.get("tool_calls", [])
    )
    return Completion(
        content=str(raw.get("content", "")),
        tool_calls=tool_calls,
        prompt_tokens=int(raw.get("prompt_tokens", 0)),
        completion_tokens=int(raw.get("completion_tokens", 0)),
    )


class CassetteClient:
    """Replay recorded completions. Deterministic; never touches the network."""

    def __init__(self, entries: dict[str, Completion], *, model: str = "recorded") -> None:
        self._entries = dict(entries)
        self.model = model
        self.calls = 0

    @classmethod
    def from_file(cls, path: str | Path) -> CassetteClient:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = {
            str(entry["key"]): _completion_from_dict(entry["response"])
            for entry in raw.get("entries", [])
        }
        return cls(entries, model=str(raw.get("model", "recorded")))

    def complete(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> Completion:
        key = cassette_key(messages, tools)
        self.calls += 1
        try:
            return self._entries[key]
        except KeyError as exc:
            raise CassetteMiss(
                f"no cassette entry for request {key[:12]} (model={self.model})"
            ) from exc


class ProviderClient:  # pragma: no cover - requires network credentials
    """OpenAI-compatible chat completion client, used only by ``make record``."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def complete(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> Completion:
        import httpx

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.as_dict() for message in messages],
            "temperature": 0,
        }
        if tools:
            payload["tools"] = tools
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]["message"]
        tool_calls = tuple(
            ToolCall(
                name=str(call["function"]["name"]),
                arguments=json.loads(call["function"].get("arguments") or "{}"),
            )
            for call in choice.get("tool_calls", [])
        )
        usage = body.get("usage", {})
        return Completion(
            content=str(choice.get("content") or ""),
            tool_calls=tool_calls,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
        )


def provider_from_env() -> ProviderClient | None:
    base_url = os.environ.get("OPSQUERY_LLM_URL")
    api_key = os.environ.get("OPSQUERY_LLM_API_KEY")
    model = os.environ.get("OPSQUERY_LLM_MODEL", "gpt-4o-mini")
    if not base_url or not api_key:
        return None
    return ProviderClient(base_url, api_key, model)
