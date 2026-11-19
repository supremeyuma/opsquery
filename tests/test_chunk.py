from __future__ import annotations

from opsquery.rag.chunk import chunk_document, chunk_file

SAMPLE = """# Policy

Intro text before the first section.

## Refunds

Customers may request a refund within 30 days.

## Shipping

Orders ship within two business days.

"""


def test_chunking_preserves_headings_and_offsets() -> None:
    chunks = chunk_document("policy", SAMPLE)
    assert chunks
    assert "".join(chunk.text for chunk in chunks) == SAMPLE
    for chunk in chunks:
        assert SAMPLE[chunk.start : chunk.end] == chunk.text
        assert chunk.doc_id == "policy"
        assert chunk.chunk_id == f"policy#{chunk.ordinal:03d}"
    headings = [chunk.section for chunk in chunks]
    assert "Policy" in headings
    assert "Refunds" in headings
    assert "Shipping" in headings


def test_long_section_splits_without_losing_characters() -> None:
    body = "\n\n".join(f"Paragraph {index} " + "word " * 40 for index in range(40))
    text = "# Long\n\n" + body
    chunks = chunk_document("long", text, max_chars=200)
    assert len(chunks) > 1
    assert "".join(chunk.text for chunk in chunks) == text
    assert all(chunk.end - chunk.start <= 200 for chunk in chunks)


def test_empty_document_has_no_chunks() -> None:
    assert chunk_document("empty", "") == []


def test_chunk_file_derives_doc_id() -> None:
    chunks = chunk_file("refund-policy.md", "# Refunds\n\nText.\n")
    assert chunks and chunks[0].doc_id == "refund-policy"
