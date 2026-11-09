-- Keyword search for hybrid retrieval (RAG-03). Generated from `text` only, never from `context`:
-- context stays ranking-only through the embedding, its one channel (decisions 0010 and 0012).
ALTER TABLE rag_chunks
    ADD COLUMN fts tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;

CREATE INDEX rag_chunks_fts ON rag_chunks USING gin (fts);
