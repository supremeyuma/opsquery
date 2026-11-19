from __future__ import annotations

from opsquery.rag.cite import (
    answer_from_chunks,
    evaluate_groundedness,
    extract_claims,
)
from opsquery.rag.index import RetrievedChunk


def chunk(chunk_id: str, content: str, doc_id: str = "policy") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        section="section",
        content=content,
        vector_score=1.0,
        lexical_score=1.0,
        score=1.0,
    )


def test_citation_ids_all_resolve_to_retrieved_chunks() -> None:
    chunks = [
        chunk("policy#000", "Refunds are issued within 30 days of delivery."),
        chunk("policy#001", "Shipping takes three to five business days."),
    ]
    answer = answer_from_chunks("refunds and shipping", chunks)
    report = evaluate_groundedness(answer.text, chunks)
    assert report.score == 1.0
    assert not report.unresolved
    assert set(answer.citations) <= {c.chunk_id for c in chunks}
    for citation in answer.citations:
        assert f"[{citation}]" in answer.text


def test_groundedness_flags_unsupported_answer() -> None:
    chunks = [chunk("policy#000", "Refunds are issued within 30 days of delivery.")]
    hallucination = "Refunds are paid in 48 hours with no questions asked [policy#000]."
    report = evaluate_groundedness(hallucination, chunks)
    assert report.score < 1.0
    assert report.unsupported


def test_groundedness_flags_unresolved_citation() -> None:
    chunks = [chunk("policy#000", "Refunds are issued within 30 days.")]
    report = evaluate_groundedness("Refunds take 30 days [made-up#999].", chunks)
    assert report.unresolved == ("made-up#999",)
    assert report.score == 0.0


def test_claim_without_citation_is_unsupported() -> None:
    chunks = [chunk("policy#000", "Refunds are issued within 30 days.")]
    report = evaluate_groundedness("Refunds are issued within 30 days.", chunks)
    assert report.unsupported
    assert report.score == 0.0


def test_answer_without_chunks_is_explicit() -> None:
    answer = answer_from_chunks("anything", [])
    assert "could not find" in answer.text
    assert answer.citations == ()


def test_extract_claims_reads_citations() -> None:
    claims = extract_claims("One fact [a#000]. Another fact [b#001].")
    assert len(claims) == 2
    assert claims[0].citation_ids == ("a#000",)
    assert claims[1].citation_ids == ("b#001",)
