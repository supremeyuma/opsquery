"""Deterministic hybrid retrieval over the document corpus.

Embeddings are feature hashes (no model download, no network). Vector similarity
is combined with a lexical overlap score and reranked. The retrieval API returns
chunk ids, document ids and text; callers must delimit text as untrusted data.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg

from opsquery.db.migrate import repo_root
from opsquery.rag.chunk import Chunk, chunk_file

DIMENSIONS = 256
TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "there",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "you",
        "your",
    ]
)


def docs_dir() -> Path:
    return repo_root() / "seed" / "docs"


def tokens(text: str, *, bigrams: bool = True) -> list[str]:
    words = [word for word in TOKEN.findall(text.lower()) if word not in STOPWORDS]
    if not bigrams or len(words) < 2:
        return words
    return words + [f"{words[i]}_{words[i + 1]}" for i in range(len(words) - 1)]


def embed(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for token in tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % DIMENSIONS
        sign = 1.0 if digest[2] & 1 else -1.0
        weight = 1.0 if "_" not in token else 0.5
        vector[index] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [round(value / norm, 8) for value in vector]


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    section: str
    content: str
    vector_score: float
    lexical_score: float
    score: float


def load_documents(directory: Path | None = None) -> list[tuple[str, str]]:
    target = directory or docs_dir()
    if not target.exists():
        return []
    return [(path.stem, path.read_text(encoding="utf-8")) for path in sorted(target.glob("*.md"))]


def _updated_at(doc_id: str) -> date:
    return date(2025, 1, 1) + timedelta(days=sum(doc_id.encode("utf-8")) % 300)


def ingest(conn: psycopg.Connection[Any], directory: Path | None = None) -> int:
    """Replace the retrieval index with chunks of every markdown document."""

    documents = load_documents(directory)
    rows: list[tuple[Any, ...]] = []
    for doc_id, text in documents:
        for chunk in chunk_file(doc_id, text):
            rows.append(
                (
                    chunk.chunk_id,
                    chunk.doc_id,
                    chunk.section,
                    chunk.ordinal,
                    chunk.start,
                    chunk.end,
                    chunk.text,
                    _updated_at(chunk.doc_id),
                    vector_literal(embed(chunk.text)),
                )
            )
    conn.execute("DELETE FROM doc_chunks")
    if rows:
        conn.cursor().executemany(
            "INSERT INTO doc_chunks (chunk_id, doc_id, section, ordinal, start_offset, "
            "end_offset, content, updated_at, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)",
            rows,
        )
    conn.commit()
    return len(rows)


def lexical_score(query: str, content: str) -> float:
    query_tokens = set(tokens(query))
    if not query_tokens:
        return 0.0
    content_tokens = set(tokens(content))
    overlap = len(query_tokens & content_tokens) / len(query_tokens)
    phrase = 1.0 if query.lower().strip() in content.lower() else 0.0
    return min(1.0, 0.8 * overlap + 0.2 * phrase)


def retrieve(conn: psycopg.Connection[Any], query: str, k: int = 5) -> list[RetrievedChunk]:
    """Hybrid vector + lexical retrieval, returning chunk ids with text."""

    rows = conn.execute(
        "SELECT chunk_id, doc_id, section, content, "
        "1 - (embedding <=> %s::vector) AS vector_score FROM doc_chunks",
        (vector_literal(embed(query)),),
    ).fetchall()
    scored: list[RetrievedChunk] = []
    for chunk_id, doc_id, section, content, vector_score in rows:
        lexical = lexical_score(query, str(content))
        combined = 0.6 * float(vector_score) + 0.4 * lexical
        scored.append(
            RetrievedChunk(
                chunk_id=str(chunk_id),
                doc_id=str(doc_id),
                section=str(section),
                content=str(content),
                vector_score=round(float(vector_score), 6),
                lexical_score=round(lexical, 6),
                score=round(combined, 6),
            )
        )
    scored.sort(key=lambda chunk: (-chunk.score, chunk.chunk_id))
    return scored[:k]


def recall_at_k(
    conn: psycopg.Connection[Any], pairs: Iterable[dict[str, str]], k: int = 5
) -> tuple[float, list[str]]:
    total = 0
    hits = 0
    misses: list[str] = []
    for pair in pairs:
        total += 1
        expected = pair["expected_doc_id"]
        retrieved = retrieve(conn, pair["question"], k=k)
        if any(chunk.doc_id == expected for chunk in retrieved):
            hits += 1
        else:
            misses.append(pair["question"])
    return (hits / total if total else 0.0), misses


__all__ = [
    "Chunk",
    "RetrievedChunk",
    "docs_dir",
    "embed",
    "ingest",
    "load_documents",
    "recall_at_k",
    "retrieve",
]
