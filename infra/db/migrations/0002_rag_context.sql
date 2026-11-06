-- Generated context (RAG-02). Both columns are NULL on a plain chunk, so a plain index is unchanged.
-- `context` is what was embedded in front of `text`, and retrieval never returns it to a model.
ALTER TABLE rag_chunks
    ADD COLUMN context    text CHECK (context <> ''),
    ADD COLUMN embed_hash text,
    ADD CONSTRAINT rag_chunks_context_has_hash CHECK ((context IS NULL) = (embed_hash IS NULL));
