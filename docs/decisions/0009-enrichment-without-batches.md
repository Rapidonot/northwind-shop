# 0009: Enrichment uses the runner path, not Batches, and stays out of the shared index

Date: 2026-11-05 · Row: #7, RAG-02 · Status: decided by the user in the row brief

Supersedes the part of decision 0005 that made runner support for Batches charging a prerequisite of
#7, and the part of decision 0001 that named `make record` and `make eval-live` as the only live
paths.

## Context

Row #7 planned Haiku through the Batches API, which the runner refuses because the ledger cannot price
it. At 2,351 short chunks the job is too small to be worth a new charging path in the process that
holds the key.

## Decision

1. **No Batches API.** Enrichment is plain Messages calls through the existing runner path, with
   prompt caching, in one spending window. No runner field or beta is widened.
2. **`make enrich` is a third spending target.** It runs only through a window the user opened
   (`SUITE=rag-enrichment`), with `LLM_MODE=live` pinned on the recipe. With no window it exits before
   any model call.
3. **Live mode, no cassettes.** 2,351 recordings would add about 20 MB, and the cache in
   `data/enrichment` is the record of the run. The ledger still records every call. Tests use scripted
   fakes.
4. **The generated sentence stays out of `text`.** It sits in `rag_chunks.context` and is embedded in
   front of the text. Retrieval never returns it, so no model reads it and nothing cites it. A row that
   wants it read must decide that on purpose.
5. **The cache key adds the parent.** It is prompt version, visibility, chunk text hash and parent
   hash. The sentence is written from the parent as well as the chunk, so if a product's text changes
   while a chunk's does not, chunk hash alone would keep a stale sentence. Two chunks with the same
   text under the same parent share one entry: R00778 and R00779 do, so the run is 2,350 calls.
6. **The enriched index is its own database, `northwind_enriched`.** A database rather than a schema,
   because a mistyped database name fails to connect, while a mistyped `search_path` falls through to
   `public`. Advisory locks are per database too, so an enriched ingest cannot block `make ingest`.
   `make ingest` stays plain, and `make ingest-enriched` refuses `northwind`.
7. **Failed text is not kept.** A context that fails a check is stored as a reason code, and the chunk
   is indexed plain. Every ingest re-checks every context, so a cache edited by hand gets no further
   than a model reply would.
8. **Two planted reviews state a discount on purpose.** R00881 (TRAP-INJ-013) and R00712
   (TRAP-INJ-003) pass the new source scan for that exact text only. Generated text has no exemption.

## Cost and pacing

The runner settles a 429 at worst case and the proxy drops `retry-after`, so the driver paces itself:
3 workers (the runner allows 4), one shared pause, a request rate that halves per 429 streak, and a
stop at 150 rate-limit replies. SDK retries are off, because a hidden retry is a hidden charge.
`thinking` stays unset, since the runner refuses `disabled`, and `effort: low` keeps output short.

## Not decided here

Switching the shared index to enriched. That changes saved contexts and needs `make eval-snapshot`,
`make eval-live` and a promote, so it waits for row 5b and belongs at row #8, which does all three
anyway.
