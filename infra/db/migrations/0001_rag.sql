-- RAG index (RAG-01). Applied by northwind_rag.store.apply_migrations, not by infra/db/init,
-- which only runs when the volume is first created. The vector extension comes from infra/db/init.

CREATE TABLE rag_index_manifest (
    index_version   text PRIMARY KEY,
    data_version    integer NOT NULL,
    dataset_sha256  text NOT NULL,
    corpus_sha256   text NOT NULL,
    chunker_version integer NOT NULL,
    embedder_id     text NOT NULL,
    chunk_count     integer NOT NULL,
    is_current      boolean NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX rag_index_manifest_one_current ON rag_index_manifest (is_current)
    WHERE is_current;

-- "Doesn't apply" is NULL. The CHECKs refuse the other spellings of nothing, '' and empty arrays.
-- visibility has no DEFAULT on purpose: a row that doesn't state it is refused (hard rule 7).
CREATE TABLE rag_chunks (
    chunk_id      text PRIMARY KEY,
    source_type   text NOT NULL CHECK (source_type IN ('product', 'variant', 'review', 'document')),
    source_id     text NOT NULL,
    seq           integer NOT NULL,
    parent_id     text,
    doc_type      text,
    text          text NOT NULL CHECK (text <> ''),
    category      text CHECK (category <> ''),
    brand         text CHECK (brand <> ''),
    size          text[] CHECK (cardinality(size) > 0),
    colour        text[] CHECK (cardinality(colour) > 0),
    material      text CHECK (material <> ''),
    -- region -> band, because one product sits in different bands per region. Never an amount.
    price_band    jsonb CHECK (price_band <> '{}'::jsonb),
    region        text[] CHECK (cardinality(region) > 0),
    updated       date NOT NULL,
    visibility    text NOT NULL CHECK (visibility IN ('public', 'internal')),
    content_hash  text NOT NULL,
    text_hash     text NOT NULL,
    embedder_id   text NOT NULL,
    embedding     vector(384) NOT NULL,
    index_version text NOT NULL REFERENCES rag_index_manifest (index_version),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_id, seq)
);
