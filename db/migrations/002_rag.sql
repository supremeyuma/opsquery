-- Retrieval index. Small corpus: chunk text plus a 256-dimension hashed embedding.
-- The embeddings are deterministic feature hashes, so retrieval evaluation runs offline.

CREATE TABLE doc_chunks (
    chunk_id     TEXT PRIMARY KEY,
    doc_id       TEXT        NOT NULL,
    section      TEXT        NOT NULL,
    ordinal      INTEGER     NOT NULL,
    start_offset INTEGER     NOT NULL,
    end_offset   INTEGER     NOT NULL,
    content      TEXT        NOT NULL,
    updated_at   DATE        NOT NULL,
    embedding    vector(256) NOT NULL
);

CREATE INDEX doc_chunks_doc_idx ON doc_chunks (doc_id);
CREATE INDEX doc_chunks_ordinal_idx ON doc_chunks (doc_id, ordinal);
