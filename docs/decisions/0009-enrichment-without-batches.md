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
3. **Live mode, no cassettes, no proxy log.** 2,351 recordings would add about 20 MB, and the cache in
   `data/enrichment` is the record of the run. The ledger still records every call. The proxy log would
   hold whole requests, and internal chunks carry canaries, so `log_path` is `None`. Tests use scripted
   fakes. GitHub CI's scan step lists its paths, so a tagged pytest test scans the committed cache.
4. **Context text is ranking-only.** It sits in `rag_chunks.context` and is embedded in front of the
   text, so it changes which chunks are found and nothing else. It never enters a model prompt or a
   shopper view without a new review. Retrieval never returns it, and a test fails on any new reader
   of `context` or `embed_text` outside the index writers. Row #8's choice of what FTS indexes is such
   a review.
5. **The cache key adds the model and the parent.** It is prompt version, model, visibility, chunk text
   hash and parent hash, stored with no digits so that no key can read as a card number. The sentence is written from the parent as well as the chunk, so if a product's text changes
   while a chunk's does not, chunk hash alone would keep a stale sentence. Two chunks with the same
   text under the same parent share one entry: R00778 and R00779 do, so the run is 2,350 calls.
6. **The enriched index is its own database, `northwind_enriched`.** A database rather than a schema,
   because a mistyped database name fails to connect, while a mistyped `search_path` falls through to
   `public`. Advisory locks are per database too, so an enriched ingest cannot block `make ingest`.
   `make ingest` stays plain, and `make ingest-enriched` accepts only an allowlist:
   `northwind_enriched` and `northwind_enriched_test`, which the tests use.
7. **Failed text is not kept.** A context that fails a check is stored as a reason code, and the chunk
   is indexed plain. Typographic quotes and dashes fold to ASCII first, the folded text must be
   printable ASCII, and that form is what is checked and stored. Every ingest re-checks every context
   it uses and refuses a public one that repeats internal text, so a cache edited by hand gets no
   further than a model reply would. The unused review sentences are checked by a test instead. The patterns cannot catch every paraphrase, which is why rule 4 matters more
   than they do: the advisor reads the 15 trap entries after the run.
8. **Two planted reviews state a discount on purpose.** R00881 (TRAP-INJ-013) and R00712
   (TRAP-INJ-003) pass the new source scan for that exact text only. Generated text has no exemption.

## Cost and pacing

The runner settles a 429 at worst case and the proxy drops `retry-after`, so the driver paces itself:
3 workers (the runner allows 4), one shared pause, a request rate that halves per 429 streak, and a
stop at 150 rate-limit replies. SDK retries are off, because a hidden retry is a hidden charge.
`thinking` stays unset, since the runner refuses `disabled`, and `effort: low` keeps output short.

## Result

The run cost $0.113222. Enriching every chunk gave recall@5 0.742 against 0.750 plain, so the RAG-02
criterion was not met, and a planted review's own sentence lifted it into top 5s it had no place in:
R00069 from rank 33 to 5 on EV-002, and 35 to 2 on EV-080, and R00033 from 7 to 5 on EV-095. The chunk
body is the attack, and retrieval returns the body.

## Reviews get no context

**A security rule, not a recall choice.** Reviews are written by shoppers. Context goes only to an
allow-list of source types: product, variant and document, matching row #8's list. Reviews get none, a
new source type gets none, and the index refuses a review that holds one. The review sentences from the
first run stay in the cache, unused.

Measured on the isolated index: recall@5 0.789, recall@10 0.852, MRR 0.649, against 0.750, 0.832 and
0.608. The three lifts are gone.

**What it costs.** The rule removes a lever the attacker's own text controls. It does not remove planted
reviews from results:

| | Planted appearances in a top 5 | Reviews' share of all top 5s |
|---|---|---|
| Plain | 20 | 25.0% |
| Allow-list (adopted) | 23 | 26.8% |
| Everything enriched | 16 | 39.0% |

Three planted reviews entered a top 5 under the allow-list and none left: R00970 on EV-030, R00033 on
EV-084, R00810 on EV-096. Their stored vectors are unchanged and each sat at rank 6, so enriching their
neighbours moved those away from the query. The rise looks systematic, not specific to the planted
ones: every plain review gains a little on enriched catalog chunks. Planted count alone flatters the
all-enriched index, whose reviews fill 39% of top 5s.

These figures are one unfiltered query per item. A shopper's search filters by region and can run three
times a turn, so a chunk at rank 6 here can reach a shopper's top 5.

**That delta does not satisfy the criterion.** It is on the same 64 items, after the fact. Nine
configurations were looked at, and the choice was made having seen all of them:

| Enriched | recall@5 | recall@10 | MRR |
|---|---|---|---|
| None (plain) | 0.750 | 0.832 | 0.608 |
| All | 0.742 | 0.837 | 0.610 |
| Variants only | 0.805 | 0.852 | 0.654 |
| Products only | 0.719 | 0.852 | 0.572 |
| Reviews only | 0.727 | 0.816 | 0.581 |
| Documents only | 0.734 | 0.832 | 0.623 |
| All but reviews (adopted, on security grounds) | 0.789 | 0.852 | 0.649 |
| All but products | 0.773 | 0.840 | 0.629 |
| All but documents | 0.750 | 0.837 | 0.595 |
| All but variants | 0.641 | 0.832 | 0.543 |

Variants only scored highest and was rejected for that reason: it is the best of the single-type
configurations picked after seeing the scores. **The criterion counts only once it is confirmed on items
not used for this choice**, which row #32's new items can do. Until then RAG-02 stays partial.

## Not decided here

Switching the shared index to enriched. That changes saved contexts and needs `make eval-snapshot`,
`make eval-live` and a promote, so it waits for row 5b and belongs at row #8, which does all three
anyway.
