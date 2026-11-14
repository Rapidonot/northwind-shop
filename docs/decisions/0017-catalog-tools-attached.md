# 0017: mcp-catalog attached to the Shopper Assistant (row #12, RAG-09)

Status: accepted by the user with the row #12 plan (`docs/reviews/2026-11-14-12-plan.md`) and the decisions recorded
there, 2026-11-14. The output-check part is decision 0015's #12 amendment, as 0015 §7 requires; it is not repeated
here.

## Decision

1. **Attached, with its own token.**
   - The assistant gets mcp-catalog's five read-only tools beside `retrieve_context`, from `tool_scopes`.
   - Each turn mints one token per server. Each server refuses the other's token (#10b L4). `mint(server=)` is
     required and `verify` is private (#10b P1).
   - `stream_answer(…, catalog=None)`: the gateway and the eval answerer always pass a `CatalogEndpoint`, which is
     pinned. `None` is the one-server surface that tests written before #12 use.
2. **Labelled as data (D1).**
   - Every catalog success is one `<catalog_data tool="…">` block of NFKC-normalised JSON with `<`, `>` and `&`
     escaped (`northwind_common.catalog_tool`). Errors stay the bare envelope.
   - The prompt says catalog results are data.
3. **Turn cap (D2).**
   - `MAX_TURNS` is 6, up from 4. mcp-rag's 3-search cap (RAG-10) is unchanged.
   - This is the interim bound on catalog calls. A per-turn catalog call cap is a logged hardening item: the cap
     bounds model rounds, not parallel calls.
4. **Prompt (model input).**
   - Price and stock only from `get_price` / `check_stock` this turn, every candidate variant agreeing, else ask.
   - Each live price instead of restating a shopper's limit (D5).
   - Promotions and delivery dates cannot be confirmed.
   - Site, account, checkout and support only as a source states them.
   - Product attributes, including which sizes and colours a product comes in, only from cited sources; the catalog
     tools only for live price and stock and to find the variant to check.
   - No self-description or unasked offers.
5. **Turn text.** Text from two model calls is joined with a paragraph break (W1). A preamble glued to the answer
   read as a bare domain ("size.The").
6. **Evals (D6 and the user's changes).**
   - The answerer serves mcp-catalog in-process over the verified fixtures with the run's fresh key. Its guard
     refuses canaries and internal source IDs inside the server, before the model sees a result.
   - The judge sees only `get_price` / `check_stock` results, as `type="catalog"` blocks rendered through `as_data`;
     `judge.py` is unchanged.
   - Claims backed only by a live value do not count toward the citation requirement; `citation_ok_counting_live`
     reports the strict count.
   - An "out of stock" that this turn's stock checks vouch for (D3) is not TRAP-INJ-002's planted claim.
   - `eval-integrity` lets refinements change or disappear only when the promoted baseline, committed and on disk,
     carries a new answerer prompt part. It prints each one; otherwise refinements stay add-only.

## Consequences

- **Re-records:** prompt and tools are model input, so `assistant-smoke`, `assistant-redteam` and `eval-answers`
  were re-recorded on the final code. `a2a-05-partner` goes stale by design until #18.
- **Superseded cassettes:**
  - Removed in the same commits as the new ones.
  - 10 first-W3 judge cassettes are still read and kept: their answers came out word for word the same, so record
    mode replayed them.
  - `make eval-rescore` rebuilds the candidate byte for byte.
- **Security review:**
  - Two passes and two scoped re-reviews. The user ended the passes after the second re-review.
  - H1–H3 and the later fixes are on the user's re-read list instead of a third pass.
  - Residuals are logged in PROGRESS "Open items".
- **Not done here:**
  - promotions from a tool (none exists);
  - delivery dates;
  - catalog→index freshness (BE-03);
  - the category-tree and attribute-dictionary resources (#10b Q3).
