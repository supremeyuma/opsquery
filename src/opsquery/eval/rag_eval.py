"""Retrieval and groundedness metrics folded into the main eval report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import yaml

from opsquery.db.migrate import repo_root
from opsquery.rag.cite import answer_from_chunks, evaluate_groundedness
from opsquery.rag.index import (
    RetrievedChunk,
    ingest,
    load_documents,
    recall_at_k,
    retrieve,
)
from opsquery.rag.injection import build_context, is_poisoned


def labelled_pairs_path() -> Path:
    return repo_root() / "eval" / "rag" / "labelled_pairs.yaml"


def load_labelled_pairs() -> list[dict[str, str]]:
    raw = yaml.safe_load(labelled_pairs_path().read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("labelled pairs must be a YAML list")
    return [
        {"question": str(row["question"]), "expected_doc_id": str(row["expected_doc_id"])}
        for row in raw
    ]


def evaluate_rag(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """Recall@5, citation groundedness and poisoned-document quarantine."""

    pairs = load_labelled_pairs()
    recall, misses = recall_at_k(conn, pairs, k=5)

    groundedness_scores = []
    for pair in pairs:
        chunks = retrieve(conn, pair["question"], k=5)
        answer = answer_from_chunks(pair["question"], chunks)
        report = evaluate_groundedness(answer.text, chunks)
        groundedness_scores.append(report.score)

    poisoned_doc_ids = [doc_id for doc_id, text in load_documents() if is_poisoned(text)]
    quarantined_chunks = 0
    for doc_id in poisoned_doc_ids:
        rows = conn.execute(
            "SELECT chunk_id, doc_id, section, content FROM doc_chunks WHERE doc_id = %s",
            (doc_id,),
        ).fetchall()
        chunks = [
            RetrievedChunk(
                chunk_id=str(row[0]),
                doc_id=str(row[1]),
                section=str(row[2]),
                content=str(row[3]),
                vector_score=0.0,
                lexical_score=0.0,
                score=0.0,
            )
            for row in rows
        ]
        bundle = build_context("poisoned document test", chunks)
        quarantined_chunks += len(bundle.quarantined)
        if bundle.tool_calls:  # pragma: no cover - defensive invariant
            raise AssertionError("poisoned document produced a tool call")

    return {
        "recall_at_5": round(recall, 4),
        "groundedness": (
            round(sum(groundedness_scores) / len(groundedness_scores), 4)
            if groundedness_scores
            else 0.0
        ),
        "labelled_queries": len(pairs),
        "poisoned_documents": len(poisoned_doc_ids),
        "poisoned_chunks_quarantined": quarantined_chunks,
        "poisoned_tool_calls": 0,
        "recall_misses": misses,
    }


def reindex(conn: psycopg.Connection[Any]) -> int:
    return ingest(conn)
