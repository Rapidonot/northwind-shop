# Row #17 security review, raw output (security-reviewer, verbatim)

Diff `/tmp/claude-502/northwind-security-17.diff`, base `437968795584d9aeb7757ca0d80e4ba15128acf7`, head `2925a7f4ece1ae610990b1f54c89ffa1afb85f14`.

Security review of row #17 (SA-02 complete, NFR-13 partial, D8 prompt lines)

Diff: /tmp/claude-502/northwind-security-17.diff. Base 437968795584d9aeb7757ca0d80e4ba15128acf7, head 2925a7f4ece1ae610990b1f54c89ffa1afb85f14; the SHA lines match what you gave me. I read CLAUDE.md "Hard rules" and docs/decisions/0001-llm-first-order-and-record-replay.md fresh. I also read these files to check how the diff is used: turn.py, tools.py (`record`, `parse_sources`), mcp-rag render.py, northwind_rag sources.py and store.py, rag_tool.py `SOURCE_ID`, common llm.py (placeholder key), llm-replay scripted.py (`ServerThread`), and the full Chat.tsx and chatStream.ts.

**Result: no critical, high or medium findings. There are 2 low findings.**

## Findings

**L1. Low. The portal does not check the items inside the `sources` array. Hard rule 11, indirectly; robustness.**
- Where: /Users/rapido/Projects/northwind-shop/apps/portal/src/chatStream.ts:40, used at /Users/rapido/Projects/northwind-shop/apps/portal/src/Chat.tsx:130 (`card.id`), :77–78 (`aria-label={card.title}`, `{card.title}`) and :84 (`sourceLabel`).
- What is wrong: `Array.isArray(body.sources) ? (body.sources as SourceCard[]) : []` checks only that the value is an array. Nothing checks the items in it.
- How it fails:
  - A `citations` event with `sources: [null]` throws a TypeError on `card.id` while Chat renders.
  - `sources: [{"id":"P0178","type":"product","title":{}}]` throws "Objects are not valid as a React child".
  - Chat has no error boundary, so either error unmounts the whole Chat section. That includes `<AiDisclosure />`, so the answer and the AI disclosure both disappear.
- Why it is only low: the gateway is the only producer, and `_card` in chat.py always sends strings. Nothing in the diff lets an attacker shape these items. It does go against the brief ("a malformed `sources` value never yields a link or card"): the edge tests in ChatSourcesEdges.test.tsx cover only `sources` that is not an array (a string, an object, null, a number).
- Fix:
  - In `toEvent`, keep an item only if it is a non-null object whose `id` and `type` are strings. Copy `title`, `brand` and `department` only when they are strings.
  - Add Vitest cases for `[null]`, `[3]`, `[{id: 1}]` and `[{...RUG, title: {}}]`. Each should give no card, the answer should still render, and AiDisclosure should still be present.

**L2. Low, hardening only; no failure confirmed. Card text never goes through the output check, and the cards do not check themselves for prices, stock or promotions. Hard rule 2.**
- Where: /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/source_cards.py:46–54 (`_card`: title at :49–50, brand and department at :52–53).
- My judgement on relying on RAG-09: for what hard rule 2 requires today, it is sound.
  1. The title is the first line of `chunk.text`. That line is the product name for products and variants (`with_prefix`), and "Title > section" for documents.
  2. `check_chunk` scans the whole `chunk.text` with `find_money_or_stock` and `find_promotions`. It runs in `build_chunks` (sources.py:441–442) and again on every index write in `sync_chunks` (store.py:244–245).
  3. The scan's only exemption, `PLANTED_PROMOTIONS`, applies only when the record is a review. Reviews never put text into a card (source_cards.py:47–48).
  4. Generated context is used only for ranking and never appears in the returned text (retrieval.py:14).
  5. The card's `type` comes from mcp-rag's server-side render. Review text is escaped there (render.py:23, :42), so a review cannot pose as a `product` block.
- What remains: card fields reach the shopper without passing the output check, which has more patterns than the ingest scan (for example "limited stock"). If the index guarantee ever slipped, a card would show a price-bearing title that the same text in an answer would have been blocked for. One example is a row written outside `sync_chunks`. No current path does this, so I could not confirm a failure.
- Fix (optional, cheap): in `_card`, run `find_money_or_stock(_normalised(field))` and `find_promotions(...)` on the title, brand and department, and drop any field that hits. Add a test: a trusted product whose first line is "Tidewick Jacket $49.99" must give `title=None`. Today `test_a_title_is_the_first_line_only…` proves only that later lines are left out.

## Answers to your three focus areas

1. **Chat.tsx and chatStream.ts link building: no findings, apart from L1.**
   - There is no `dangerouslySetInnerHTML`, `innerHTML`, `insertAdjacentHTML`, `document.write` or `eval` anywhere in apps/portal/src.
   - A link is made only when `cited.has(id)` is true, so `href` is always `#source-<a cited ID>`. Cited IDs are limited to `SOURCE_ID` (`[A-Za-z0-9._-]+`) at mcp-rag and to `seen` in `cited_ids`. The href always starts with `#`, so it cannot become a `javascript:` URL.
   - Crafted markers stay plain text, and the tests cover them: HTML, quotes, `javascript:`, nested and unclosed brackets, and look-alikes (`P0178-evil`, `P0178#x`). A Cyrillic look-alike also fails the exact-match `Set.has`.
   - A `sources` entry for an uncited ID gets no card (Chat.tsx:130; the bug test-writer found is fixed).
   - Titles render as React text children.
   - There is no hash router, so `#source-…` links do not trigger navigation.

2. **source_cards.py, turn.py and chat.py: no findings, apart from L2.**
   - Review text never reaches a card. `_card` uses an allow-list (`TRUSTED_SOURCE_TYPES`), so reviews and unknown types get only their ID and type.
   - Hard rule 7: cards come only from `state.sources`. `stream_answer` creates a fresh `ToolState` for each call (turn.py:297). It is filled only by `record()` from mcp-rag results fetched with this caller's token: the server's first search and the model's `retrieve_context` calls (`ours` holds only `ALLOWED_TOOL` IDs). Cards are emitted only on the success path. The error path sends no Citations, and the capped path cites nothing.
   - Control characters are removed and fields are capped at 120 characters. The SSE data is JSON-encoded, so text cannot inject an SSE frame.

3. **prompt.py D8 lines: no findings.**
   - Every cart rule is still in place, word for word: offer only the alternatives the result lists, by product name and variant ID, with no stock words; the `already_applied` rule; no orders in chat.
   - The totals ban is kept ("Never state the cart's line totals or total").
   - The new exception only points back to the cart rule ("exactly as the cart rule above says"), so it adds no behaviour of its own.
   - output_check.py is not in the diff, so row #16's deterministic offer-drop rule (`_OFFER_*` patterns, `DROPPED_CLASSES`) still applies whatever the prompt says.
   - "Unless the shopper asks" depends on the existing rule that only `<shopper_message>` is the shopper. The rules that treat sources and catalog data as data are unchanged, so no retrieved text gains instruction status.

**NFR-13 harness (test_nfr13_first_token.py): no findings.**
- `PacedModel` runs on `ServerThread`, which binds 127.0.0.1 on a random port (scripted.py:31–35).
- The CLI is built through `agent_runtime(base_url=model_url)`, so it gets `PLACEHOLDER_API_KEY` (llm.py:84). There is no real key, no upstream and no ledger involvement.
- It uses only lower-bound timing assertions.

**New and changed tests:** existing assertions were extended (the `sources` key was added), not loosened. The CHK-01 prompt pin moved to the new wording as planned, and it still asserts every cart rule.

## Checked, no findings
- Hard rule 1 (no card data in the model): card fields come only from indexed text, never from shopper input. The `contains_card_data` gate in chat.py is unchanged.
- Hard rule 2 (live-only price and stock): sound; see L2 for the optional hardening.
- Hard rule 3 (synthetic data only): test fixtures are synthetic.
- Hard rule 4 (secrets): no secrets in the code, docs or probe scripts. No `.env` access.
- Hard rule 5 (every claim cited): card text is tied to a cited source. Citation checking (`cited_ids` against `seen`) is unchanged.
- Hard rule 6 (retrieved text is data): no findings (focus areas 2 and 3).
- Hard rule 7 (internal documents and regions): no findings (focus area 2).
- Hard rule 8 (least privilege): not applicable. `assistant_options` and the tool lists are unchanged.
- Hard rule 9 (idempotency and confirmation): not applicable. No state-changing tool was changed.
- Hard rule 10 (structured tool errors): not applicable.
- Hard rule 11 (AI disclosure): `<AiDisclosure />` is still rendered, and the prompt keeps "You are an AI, and you say so if asked". The only related risk is the crash in L1.
- Child processes get only a placeholder key; only the proxy holds the real key: confirmed for the new harness and tests.
- The Makefile does not export the key: not applicable (the Makefile is not in the diff).
- No key in CI secrets, and CI runs in replay: not applicable (`.github/` is not in the diff).
- The key file is never copied into a worktree: not applicable.
- Budget caps are enforced and not raised: no cap or ledger code was changed. The plan keeps the $90 cap.
- Cassettes are still covered by the secret/PAN scanner: no cassette or scanner change is in this diff.
- Steering attempts: none found. The "Ignore your rules…" text in the review fixtures is test data and does not address the reviewer.

## Notes (not findings)
- The plan says titles only for "product/variant (trusted)", but the code also gives documents a title, through `TRUSTED_SOURCE_TYPES`. This matches the RAG-03 trust classification. Documents are Northwind-authored, and internal ones are filtered by mcp-rag. Worth making the plan and the PR body agree.
- Chat.tsx `linked()`, the branch for a marker with several IDs: it rebuilds the text as `ids.join(", ")`, so "[P0178,P9999]" renders as "[P0178, P9999]". The comment "links change no text" is therefore not strictly true. This is cosmetic.
- `test_no_recorded_cart_answer_points_at_the_cart_panel_or_the_screen` fails at this head by design, until window 2. So `make ci` at this head will fail.
- The probe doc's runner gap (the value of `output_config.effort` is not checked) is outside this diff. The budget cap still limits spend.
