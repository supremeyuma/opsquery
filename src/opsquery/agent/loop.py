"""Bounded tool loop.

Tools are exactly ``run_sql``, ``retrieve_docs`` and ``finish``. There is no
write tool: the capability does not exist in the registry. Every SQL call passes
the guard and the read-only executor, results are redacted, and retrieved text is
quarantined when it contains instructions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

import psycopg

from opsquery.agent.planner import Plan, plan
from opsquery.agent.refuse import RefusalDecision, check_request
from opsquery.config import settings
from opsquery.db.executor import ExecResult, Executor
from opsquery.llm.client import Completion, Message
from opsquery.llm.prompts import SYSTEM_PROMPT
from opsquery.observability.trace import RecordedSpan, TraceRecorder
from opsquery.rag.cite import answer_from_chunks
from opsquery.rag.index import RetrievedChunk, retrieve
from opsquery.rag.injection import ContextBundle, build_context
from opsquery.security.redact import redact_result, redact_text

MAX_TOOL_CALLS = 6
TOOL_NAMES = ("run_sql", "retrieve_docs", "finish")

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Run one guarded SELECT against the read-only database.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_docs",
            "description": "Retrieve policy documents for a question.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Finish with a cited answer or an explicit refusal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "string"}},
                    "refusal": {"type": "string"},
                },
            },
        },
    },
]


@dataclass(frozen=True)
class Action:
    kind: Literal["tool", "finish"]
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    citations: tuple[str, ...] = ()
    refusal: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @classmethod
    def run_sql(cls, sql: str) -> Action:
        return cls(kind="tool", tool="run_sql", arguments={"sql": sql})

    @classmethod
    def retrieve(cls, query: str) -> Action:
        return cls(kind="tool", tool="retrieve_docs", arguments={"query": query})

    @classmethod
    def finish(
        cls, answer: str = "", citations: tuple[str, ...] = (), refusal: str | None = None
    ) -> Action:
        return cls(kind="finish", answer=answer, citations=citations, refusal=refusal)


@dataclass(frozen=True)
class Observation:
    tool: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    message: str = "ok"


@dataclass
class LoopState:
    question: str
    observations: list[Observation] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return 0


class Proposer(Protocol):
    def next_action(self, state: LoopState) -> Action: ...


@dataclass(frozen=True)
class AgentResult:
    question: str
    answer: str
    citations: tuple[str, ...]
    refusal: str | None
    tool_calls: tuple[str, ...]
    observations: tuple[Observation, ...]
    tokens: int
    cost_usd: float
    trace: tuple[RecordedSpan, ...]
    quarantined: tuple[str, ...]


class ToolRegistry:
    def __init__(self, readonly_url: str | None = None, trace: TraceRecorder | None = None):
        self.executor = Executor(readonly_url or settings().readonly_url)
        self.readonly_url = readonly_url or settings().readonly_url
        self.trace = trace or TraceRecorder()

    @property
    def names(self) -> tuple[str, ...]:
        return TOOL_NAMES

    def invoke(self, action: Action) -> Observation:
        if action.tool == "run_sql":
            return self._run_sql(str(action.arguments.get("sql", "")))
        if action.tool == "retrieve_docs":
            return self._retrieve(str(action.arguments.get("query", "")))
        return Observation(tool=str(action.tool), ok=False, message="unknown tool")

    def _run_sql(self, sql: str) -> Observation:
        result: ExecResult = self.executor.execute(sql)
        redacted, report = redact_result(result)
        sql_hash = hashlib.sha256(result.sql.encode()).hexdigest()
        with self.trace.span(
            "tool.run_sql",
            {
                "tool": "run_sql",
                "sql_hash": sql_hash,
                "rows": redacted.row_count,
                "tokens": 0,
                "cost": 0.0,
                "redactions": report.redactions,
                "truncated": redacted.truncated,
            },
        ):
            pass
        return Observation(
            tool="run_sql",
            ok=redacted.ok,
            data={
                "columns": [column.name for column in redacted.columns],
                "rows": redacted.rows,
                "row_count": redacted.row_count,
                "truncated": redacted.truncated,
                "sql_hash": sql_hash,
                "redactions": report.redactions,
                "result_hash": redacted.result_hash,
            },
            message=redacted.message,
        )

    def _retrieve(self, query: str) -> Observation:
        with psycopg.connect(self.readonly_url) as conn:
            chunks: list[RetrievedChunk] = retrieve(conn, query, k=5)
        redactions = 0
        cleaned: list[RetrievedChunk] = []
        for chunk in chunks:
            text, count = redact_text(chunk.content)
            redactions += count
            cleaned.append(replace(chunk, content=text))
        bundle: ContextBundle = build_context(query, cleaned)
        allowed = [chunk for chunk in cleaned if chunk.chunk_id in bundle.allowed_ids]
        with self.trace.span(
            "tool.retrieve_docs",
            {
                "tool": "retrieve_docs",
                "chunks": len(cleaned),
                "quarantined": len(bundle.quarantined),
                "redactions": redactions,
                "tokens": 0,
                "cost": 0.0,
            },
        ):
            pass
        return Observation(
            tool="retrieve_docs",
            ok=True,
            data={
                "chunks": allowed,
                "bundle": bundle,
                "quarantined": bundle.quarantined,
                "top_score": chunks[0].score if chunks else 0.0,
            },
        )


class AgentLoop:
    def __init__(
        self,
        proposer: Proposer,
        *,
        readonly_url: str | None = None,
        max_tool_calls: int = MAX_TOOL_CALLS,
    ) -> None:
        self.proposer = proposer
        self.registry = ToolRegistry(readonly_url)
        self.max_tool_calls = max_tool_calls

    def run(self, question: str) -> AgentResult:
        recorder = self.registry.trace
        decision: RefusalDecision = check_request(question)
        trace: tuple[RecordedSpan, ...] = ()
        with recorder.span("agent.request", {"question_chars": len(question)}):
            if decision.refuse:
                return AgentResult(
                    question=question,
                    answer="",
                    citations=(),
                    refusal=decision.reason,
                    tool_calls=(),
                    observations=(),
                    tokens=0,
                    cost_usd=0.0,
                    trace=(),
                    quarantined=(),
                )
            state = LoopState(question=question)
            tokens = 0
            answer = ""
            citations: tuple[str, ...] = ()
            refusal: str | None = None
            while len(state.tool_calls) < self.max_tool_calls:
                action = self.proposer.next_action(state)
                tokens += action.prompt_tokens + action.completion_tokens
                if action.kind == "finish":
                    answer = action.answer
                    citations = action.citations
                    refusal = action.refusal
                    break
                assert action.tool is not None
                state.tool_calls.append(action.tool)
                with recorder.span(
                    "agent.step",
                    {"tool": action.tool, "step": len(state.tool_calls)},
                ):
                    observation = self.registry.invoke(action)
                state.observations.append(observation)
            else:
                refusal = "tool budget exhausted before a supported answer"
            quarantined = tuple(
                chunk_id
                for observation in state.observations
                for chunk_id in observation.data.get("quarantined", ())
            )
            answer = answer or ("" if refusal else "No answer produced.")
        trace = tuple(recorder.recorded())
        return AgentResult(
            question=question,
            answer=answer,
            citations=citations,
            refusal=refusal,
            tool_calls=tuple(state.tool_calls),
            observations=tuple(state.observations),
            tokens=tokens,
            cost_usd=0.0,
            trace=trace,
            quarantined=quarantined,
        )


DOC_KEYWORDS = (
    "policy",
    "refund",
    "warranty",
    "shipping",
    "invoice terms",
    "return",
    "privacy",
    "retention",
    "supplier",
    "inventory",
    "segment",
    "support",
    "pricing",
    "discount",
    "payment",
    "chargeback",
    "security",
    "tax",
    "faq",
    "marketing notes",
    "vendor update",
    "document",
)


def _is_document_question(question: str) -> bool:
    lower = question.lower()
    return any(keyword in lower for keyword in DOC_KEYWORDS)


class OfflineProposer:
    """Deterministic proposer used by ``make demo`` and offline tests."""

    def next_action(self, state: LoopState) -> Action:
        if not state.observations:
            if _is_document_question(state.question):
                return Action.retrieve(state.question)
            chosen: Plan = plan(state.question)
            if chosen.refused:
                return Action.finish(refusal=chosen.refusal)
            if chosen.sql is not None:
                return Action.run_sql(chosen.sql)
            return Action.finish(refusal="no supported plan for the question")
        last = state.observations[-1]
        if last.tool == "retrieve_docs":
            chunks: list[RetrievedChunk] = last.data.get("chunks", [])
            quarantined: tuple[str, ...] = last.data.get("quarantined", ())
            top_score = float(last.data.get("top_score", 0.0))
            allowed_top = max((chunk.score for chunk in chunks), default=0.0)
            if quarantined and allowed_top < 0.5 * top_score:
                return Action.finish(
                    answer=(
                        "The retrieved document contained instructions and was quarantined; "
                        "no trustworthy source supported an answer."
                    )
                )
            if not chunks:
                suffix = (
                    f" ({len(quarantined)} instruction-bearing document(s) quarantined)"
                    if quarantined
                    else ""
                )
                return Action.finish(
                    answer=f"No supporting document was found for that question.{suffix}"
                )
            grounded = answer_from_chunks(state.question, chunks, top_document_only=True)
            return Action.finish(answer=grounded.text, citations=grounded.citations)
        if last.tool == "run_sql":
            if not last.ok:
                return Action.finish(refusal=f"query rejected: {last.message}")
            rows = last.data.get("rows", ())
            return Action.finish(
                answer=f"Result ({last.data.get('row_count', 0)} rows): "
                + json.dumps(rows, default=str)
            )
        return Action.finish(refusal="no supported plan for the question")


class ScriptedProposer:
    """Replay a fixed list of actions; used by budget and loop tests."""

    def __init__(self, actions: list[Action]) -> None:
        self.actions = actions
        self.index = 0

    def next_action(self, state: LoopState) -> Action:
        if self.index >= len(self.actions):
            return Action.finish(refusal="script exhausted")
        action = self.actions[self.index]
        self.index += 1
        return action


class LLMProposer:
    """Drive the loop from a cassette-replayed (or recorded) completion client."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def next_action(self, state: LoopState) -> Action:
        messages = [Message(role="system", content=SYSTEM_PROMPT)]
        messages.append(Message(role="user", content=state.question))
        for observation in state.observations:
            messages.append(
                Message(
                    role="user",
                    content=f'<tool_result tool="{observation.tool}">'
                    + json.dumps(observation.data, default=str)
                    + "</tool_result>",
                )
            )
        completion: Completion = self.client.complete(messages, TOOLS_SPEC)
        if completion.tool_calls:
            call = completion.tool_calls[0]
            if call.name == "finish":
                return Action(
                    kind="finish",
                    answer=str(call.arguments.get("answer", "")),
                    citations=tuple(call.arguments.get("citations", ())),
                    refusal=call.arguments.get("refusal"),
                    prompt_tokens=completion.prompt_tokens,
                    completion_tokens=completion.completion_tokens,
                )
            return Action(
                kind="tool",
                tool=call.name,
                arguments=dict(call.arguments),
                prompt_tokens=completion.prompt_tokens,
                completion_tokens=completion.completion_tokens,
            )
        return Action(
            kind="finish",
            answer=completion.content,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
        )
