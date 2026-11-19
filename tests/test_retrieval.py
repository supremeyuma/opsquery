from __future__ import annotations

import psycopg

from opsquery.eval.rag_eval import evaluate_rag, load_labelled_pairs
from opsquery.rag.index import embed, ingest, load_documents, recall_at_k, retrieve
from opsquery.rag.injection import is_poisoned


def test_retrieval_recall_at_5_on_labelled_pairs(seeded_db) -> None:  # type: ignore[no-untyped-def]
    pairs = load_labelled_pairs()
    with psycopg.connect(seeded_db.readonly_url) as conn:
        recall, misses = recall_at_k(conn, pairs, k=5)
    assert recall >= 0.85, misses


def test_retrieval_returns_ranked_chunks(seeded_db) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(seeded_db.readonly_url) as conn:
        chunks = retrieve(conn, "refund window for damaged goods", k=5)
    assert 0 < len(chunks) <= 5
    assert chunks[0].doc_id == "refund-policy"
    assert all(chunks[i].score >= chunks[i + 1].score for i in range(len(chunks) - 1))


def test_embeddings_are_deterministic_and_normalised() -> None:
    first = embed("refund policy window")
    second = embed("refund policy window")
    assert first == second
    assert len(first) == 256
    norm = sum(value * value for value in first) ** 0.5
    assert abs(norm - 1.0) < 1e-5
    assert embed("") == [0.0] * 256


def test_poisoned_documents_are_present_and_identified() -> None:
    poisoned = [doc_id for doc_id, text in load_documents() if is_poisoned(text)]
    assert len(poisoned) == 3
    assert set(poisoned) == {"marketing-notes", "vendor-update", "legacy-faq"}


def test_ingest_is_idempotent(seeded_db) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(seeded_db.admin_url) as conn:
        first = conn.execute("SELECT count(*) FROM doc_chunks").fetchone()
        ingest(conn)
        second = conn.execute("SELECT count(*) FROM doc_chunks").fetchone()
    assert first == second


def test_rag_report_shape(seeded_db) -> None:  # type: ignore[no-untyped-def]
    with psycopg.connect(seeded_db.readonly_url) as conn:
        report = evaluate_rag(conn)
    assert report["recall_at_5"] >= 0.85
    assert report["groundedness"] >= 0.95
    assert report["poisoned_tool_calls"] == 0
    assert report["poisoned_documents"] == 3
    assert report["poisoned_chunks_quarantined"] >= 3
