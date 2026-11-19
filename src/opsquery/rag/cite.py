"""Citation assembly and groundedness checks.

Every factual claim must carry a citation that resolves to a retrieved chunk and
be supported by that chunk's text. Unsupported or dangling claims fail the
groundedness check; the answer is never silently accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from opsquery.rag.index import RetrievedChunk, tokens

CITATION = re.compile(r"\[([a-z0-9_-]+#\d{3})\]")
SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Claim:
    text: str
    citation_ids: tuple[str, ...]


@dataclass(frozen=True)
class GroundednessReport:
    score: float
    total_claims: int
    supported: int
    unsupported: tuple[str, ...]
    unresolved: tuple[str, ...]


@dataclass(frozen=True)
class GroundedAnswer:
    text: str
    citations: tuple[str, ...]


def extract_claims(text: str) -> list[Claim]:
    claims: list[Claim] = []
    for sentence in SENTENCE.split(text.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        citations = tuple(CITATION.findall(sentence))
        claims.append(Claim(text=sentence, citation_ids=citations))
    return claims


def _support_ratio(claim_text: str, chunk_text: str) -> float:
    claim_tokens = set(tokens(CITATION.sub(" ", claim_text)))
    if not claim_tokens:
        return 1.0
    chunk_tokens = set(tokens(chunk_text))
    return len(claim_tokens & chunk_tokens) / len(claim_tokens)


def evaluate_groundedness(
    answer_text: str, chunks: list[RetrievedChunk], *, support_threshold: float = 0.5
) -> GroundednessReport:
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    claims = extract_claims(answer_text)
    supported = 0
    unsupported: list[str] = []
    unresolved: list[str] = []
    for claim in claims:
        if not claim.citation_ids:
            unsupported.append(claim.text)
            continue
        missing = [cid for cid in claim.citation_ids if cid not in chunks_by_id]
        if missing:
            unresolved.extend(missing)
            continue
        ratios = [
            _support_ratio(claim.text, chunks_by_id[cid].content) for cid in claim.citation_ids
        ]
        if max(ratios) >= support_threshold:
            supported += 1
        else:
            unsupported.append(claim.text)
    total = len(claims)
    score = supported / total if total else 0.0
    return GroundednessReport(
        score=score,
        total_claims=total,
        supported=supported,
        unsupported=tuple(unsupported),
        unresolved=tuple(unresolved),
    )


def _first_sentence(text: str) -> str:
    sentence = SENTENCE.split(text.strip())[0].strip()
    return sentence


def answer_from_chunks(
    question: str, chunks: list[RetrievedChunk], max_claims: int = 3
) -> GroundedAnswer:
    """Build a deterministic extractive answer with one citation per claim."""

    if not chunks:
        return GroundedAnswer(
            text="I could not find supporting documentation for that question.", citations=()
        )
    claims: list[str] = []
    citations: list[str] = []
    for chunk in chunks[:max_claims]:
        sentence = _first_sentence(chunk.content)
        if not sentence:
            continue
        sentence = sentence.rstrip(".!?")
        claims.append(f"{sentence} [{chunk.chunk_id}].")
        citations.append(chunk.chunk_id)
    if not claims:
        return GroundedAnswer(
            text="I could not find supporting documentation for that question.", citations=()
        )
    return GroundedAnswer(text=" ".join(claims), citations=tuple(citations))
