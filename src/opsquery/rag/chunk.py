"""Heading-aware, lossless document chunking."""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    section: str
    ordinal: int
    start: int
    end: int
    text: str


def _sections(text: str) -> list[tuple[int, str]]:
    sections: list[tuple[int, str]] = []
    for match in HEADING.finditer(text):
        if not sections or match.start() != 0:
            sections.append((match.start(), match.group(2).strip()))
    if not sections or sections[0][0] != 0:
        sections.insert(0, (0, "preamble"))
    return sections


def _split_range(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    pieces: list[tuple[int, int]] = []
    while end - start > max_chars:
        limit = start + max_chars
        window = text[start:limit]
        cut = window.rfind("\n\n")
        if cut <= 0:
            cut = window.rfind("\n")
        if cut <= 0:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = max_chars
        pieces.append((start, start + cut))
        start += cut
    if start < end:
        pieces.append((start, end))
    return pieces


def chunk_document(doc_id: str, text: str, max_chars: int = 800) -> list[Chunk]:
    """Split a markdown document into contiguous chunks.

    Concatenating ``chunk.text`` in order reproduces the input exactly, and
    ``text[chunk.start:chunk.end] == chunk.text``. Section headings are preserved
    in the chunk that starts the section.
    """

    if not text:
        return []
    sections = _sections(text)
    chunks: list[Chunk] = []
    ordinal = 0
    for index, (start, title) in enumerate(sections):
        end = sections[index + 1][0] if index + 1 < len(sections) else len(text)
        if end <= start:
            continue
        for piece_start, piece_end in _split_range(text, start, end, max_chars):
            chunk_text = text[piece_start:piece_end]
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}#{ordinal:03d}",
                    doc_id=doc_id,
                    section=title,
                    ordinal=ordinal,
                    start=piece_start,
                    end=piece_end,
                    text=chunk_text,
                )
            )
            ordinal += 1
    return chunks


def chunk_file(path_name: str, text: str, max_chars: int = 800) -> list[Chunk]:
    doc_id = path_name.rsplit(".", 1)[0]
    return chunk_document(doc_id, text, max_chars=max_chars)
