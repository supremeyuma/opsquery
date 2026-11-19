"""Injection screening for retrieved content.

Retrieved documents are data. A document that contains instruction-like text is
quarantined: it is excluded from the model context entirely, so it cannot cause
a tool call. The screening result is reported, never silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from opsquery.llm.prompts import rag_prompt
from opsquery.rag.index import RetrievedChunk, load_documents

INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore-instructions", re.compile(r"ignore (all )?(previous|prior|above) instructions", re.I)),
    ("disregard-instructions", re.compile(r"disregard .{0,30}instructions", re.I)),
    ("system-prompt", re.compile(r"system prompt|you are now|as an ai", re.I)),
    ("execute-sql", re.compile(r"\b(run|execute) sql\b", re.I)),
    ("destructive-sql", re.compile(r"\b(drop table|delete from|truncate table|grant all)\b", re.I)),
    ("exfiltration", re.compile(r"exfiltrat|send .{0,30}(customer|email|password)", re.I)),
]


@dataclass(frozen=True)
class ContextBundle:
    prompt: str
    allowed_ids: tuple[str, ...]
    quarantined: tuple[str, ...]
    matched: dict[str, tuple[str, ...]] = field(default_factory=dict)
    tool_calls: tuple[()] = ()


def scan(text: str) -> list[str]:
    return [label for label, pattern in INJECTION_PATTERNS if pattern.search(text)]


def is_poisoned(text: str) -> bool:
    return bool(scan(text))


def build_context(question: str, chunks: list[RetrievedChunk]) -> ContextBundle:
    """Delimit clean chunks as untrusted data and quarantine poisoned documents.

    Quarantine is document-level: if any chunk of a document contains an
    instruction, every chunk of that document is excluded. A partly poisoned
    document is not trustworthy.
    """

    doc_labels: dict[str, set[str]] = {}
    known_poisoned = {doc_id for doc_id, text in load_documents() if scan(text)}
    for chunk in chunks:
        if chunk.doc_id in known_poisoned:
            doc_labels.setdefault(chunk.doc_id, set()).add("poisoned-document")
            continue
        labels = scan(chunk.content)
        if labels:
            doc_labels.setdefault(chunk.doc_id, set()).update(labels)

    allowed: list[RetrievedChunk] = []
    quarantined: list[str] = []
    matched: dict[str, tuple[str, ...]] = {}
    for chunk in chunks:
        if chunk.doc_id in doc_labels:
            quarantined.append(chunk.chunk_id)
            matched[chunk.chunk_id] = tuple(sorted(doc_labels[chunk.doc_id]))
        else:
            allowed.append(chunk)
    prompt = rag_prompt(question, [(chunk.chunk_id, chunk.content) for chunk in allowed])
    return ContextBundle(
        prompt=prompt,
        allowed_ids=tuple(chunk.chunk_id for chunk in allowed),
        quarantined=tuple(quarantined),
        matched=matched,
    )
