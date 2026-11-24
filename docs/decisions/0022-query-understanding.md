# 0022: Query understanding on the first search: rules first, Haiku only when needed

Status: accepted by the user with the row #11 plan, 2026-11-24 (`docs/reviews/2026-11-24-11-plan.md`, changes at
approval 1 to 5). Requirement: RAG-04. Amends decision 0007's consequence that RAG-04 "would put a Haiku call
before first text".

## Context

- **RAG-04:** rewrite, split multi-part questions, and extract structured filters (price, size, colour,
  availability). The criterion is filter extraction at least 95% correct on 100 labelled queries.
- **Decision 0007** removed a model call from the path to the first token. NFR-13 is already about 2,021 ms on
  #17's estimate. A Haiku call on every message would add its whole latency to every turn.

## Decision

1. **Rules first, in code.** `northwind_rag.query.rules_plan` reads the four filters on every message, in well
   under a millisecond. Its search text is the raw message, so a message with no filter keeps exactly its old
   first search.
2. **Haiku only when the rules mark a message `needs_model`:**
   - several asks in one message;
   - a size question rather than a size request;
   - a price with no bound, or a bound on a bare number;
   - a short reference to an earlier turn.

   A message the first search would refuse never goes to Haiku: empty, NUL or card data (hard rule 1). Neither
   does a message carrying injected text (role labels, `key=value` settings, "ignore the rules"); the rules plan
   of the rest of the message stands.
3. **Haiku's reply is JSON in a fixed schema, checked by code** (`validate_plan`):
   - allow-listed fields;
   - exact types;
   - the catalog's own colours and sizes;
   - prices as integer minor units in range and in the region's own currency;
   - at most 3 parts of at most 200 characters each, none card-shaped.

   Anything else, a timeout or an error keeps the rules plan.
4. **The deadline and the cache apply only when `LLM_MODE=live`:**
   - the deadline is 700 ms, after which the rules plan is used;
   - the cache is an in-process LRU of 256 plans.

   In record and replay every call is made and waited for. So recording cannot fork the flow away from replay,
   and every run reads every cassette, which keeps the hit log's deletion rule safe.
5. **Sequential, not parallel.** The plan said Haiku would run "concurrently with the raw-message search". As
   built, Haiku runs first and then one search. A parallel raw search would spend one of RAG-10's three searches
   every time the plan is used. The cost of running them one after the other is at most one search time, and only
   on the Haiku path.
6. **Price and availability come from live data (hard rule 2):**
   - mcp-catalog's `eligible_products` returns product IDs only, never a price or a count, from the live store in
     the token's region;
   - only the server's own `shopper-query` agent may call it, and no model holds that agent's token;
   - retrieval keeps product, variant and review chunks only for those products, and documents pass;
   - the index's text and its price bands are not read.
7. **The plan reaches mcp-rag as a signed claim (`qf`)** on a northwind-rag token minted for that one search:
   - the token carries the turn's own id, so RAG-10's cap counts the search;
   - it is never a tool argument, the model's `retrieve_context` schema is unchanged, and the CLI never holds the
     planned token;
   - filters can only narrow, and audience, region and visibility stay token-bound;
   - the product allow-list is a hex bitmap over `P0001` to `P0256`, and a plan that does not fit the 2,048-character
     token limit falls back to the plain search.
8. **The model is not told which filter ran.** For "under $50", the answer states no threshold and no price as
   fact unless it comes from `get_price` in that turn. The output check's money class is the fallback, not the
   plan (a scripted test pins both).
9. **The retriever's identity changes** (`RETRIEVER_VERSION` 3), so the saved contexts go stale. The gate and
   `make eval-snapshot` score the planned first search; Haiku's plans for eval items replay from the
   `query-understanding` suite, and a miss stops the run. RAG-03's arm comparison and the enrichment report's E1
   keep the plain search they were fixed on. The eval answerer passes no understander: its first search is the
   saved, already-planned context.

## Consequences

- **Added first-token time:**
  - on the rules path, one live-filter call and nothing else, and only when a filter was found;
  - on the Haiku path, Haiku's latency, capped at 700 ms in live mode.
- **Measurement:** `TurnUsage` logs `qu_path` and `qu_ms`. The W1 window measures Haiku's wall time per call, and
  the NFR-13 estimate adds it, weighted by the share of messages routed to Haiku.
- **Rewritten part texts are new search queries.** Their frozen vectors (decision 0012) are added once W1 has
  recorded them.
