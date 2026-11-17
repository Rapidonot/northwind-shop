# Row #12 security review, pass 1 (raw, verbatim)

Reviewer: `security-reviewer` subagent, read-only. Diff `/tmp/claude-502/northwind-12-security.diff`, base
`c54f9911cede7edfbe7daad4727c763dc51d5449`, head `e153442138b88e26c26e2f4142e613a4b602956d`. Saved unedited below.

---

Security review: row #12 (RAG-09 partial), branch req/12-catalog-tools

Diff: /tmp/claude-502/northwind-12-security.diff. Its first lines are `base c54f9911cede7edfbe7daad4727c763dc51d5449` and `head e153442138b88e26c26e2f4142e613a4b602956d`. I re-read CLAUDE.md "Hard rules" and decision 0001 from disk for this review. I also read the full source of output_check.py, the eval answerers/cli code, tool_scopes.py and the catalog error constructors. I am read-only and changed nothing.

Summary: the tool attach, the token binding, the `<catalog_data>` label and the `record_live` provenance hold up. The output-check extensions do not. Real price and stock claims reach the shopper with no tool call (two high findings, both unsafe under hard rule 2). Both are gaps in coverage this row adds, not regressions from main. Finding 3 is a regression against #13's patterns.

## Findings (most severe first)

### 1. HIGH, unsafe (hard rule 2; 0015 #12 amendment "Class 6"): common price formats skip the money class
- **Where:** `services/assistant/src/northwind_assistant/output_check.py:323-325` (the three money patterns) and `:465` (`money_value`).
- **What is wrong:** the class only matches a sign before the amount, an amount before a code or word, or a spelled count of at most 999 before a currency word. Anything else matches no pattern, so it passes with no `get_price` at all. Confirmed by reading the regexes:
  - **Code before the amount:** "It costs USD 279." / "EUR 130.66" / "CAD 189".
  - **Sign after the amount:** "It costs 130.66 €." / "130,66 €" / "279$". The fixtures have an EU region priced in EUR (`data/fixtures/prices.jsonl`), and in most EU locales the sign goes after the amount.
  - **Digits plus "pounds":** "It costs 199 pounds." 0015 logs only "twenty pounds" as a residual, so the logged residual understates this.
  - **A bare amount in a price context:** "The price is 279.00." or a markdown table with a "Price (USD)" column.
  - **Spelled amounts the parser cannot read:** "fifteen hundred dollars", "one thousand dollars", "a couple hundred bucks", "1.5k dollars". The approved plan §3.5 says "an amount the parser does not consume exactly is unvouched (fail closed, as for durations)". Durations have `_unparsed_duration` for that; money has no equivalent.
  - **The lookahead fails open, not closed:** in pattern 1, `(?![\d,.]\d)` drops the whole match when no complete reading fits. So "€9,99" and "$1,29" match nothing and pass. (Two-digit forms like "€19,99" do match "€1" and get blocked.)
  - **Markdown between the amount and the currency:** "$**279**" and "**279** USD" match nothing. The portal renders plain text, so the shopper still sees the figure.
  - **Minor:** `_DOLLAR_PREFIX` does not know AU$, NZ$, HK$ or R$, so these read as "any dollar".
- **How it fails:** a planted review saying "now only 49 €", or a hallucinated "USD 279", reaches an EU or US shopper with nothing checked.
- **Fix, fail-closed:**
  - add patterns for a code before the amount (`(usd|eur|gbp|cad|aud)\s{0,2}AMOUNT`) and a sign after it (`AMOUNT\s{0,2}[$£€]`);
  - drop the negative lookahead, match greedily (`[$£€]\s{0,2}\d[\d.,\s]*`) and let `money_value` return None, so the figure is blocked;
  - add a money `_unparsed` pass, like `_unparsed_duration`: any numberish token (including thousand, grand, k, dozen, couple) within 3 tokens before a currency word or sign that the parser did not consume is blocked;
  - treat a bare decimal amount as money in a sentence that holds price, cost, priced, costs, or a currency code, sign or word;
  - strip `_MARKUP` between the amount and the currency for matching.
- Add each example above as a test.

### 2. HIGH, unsafe (hard rule 2; 0015 #12 "Exemption availability"; the named focus `_asks()`): availability claims pass with no `check_stock`
- **Where:** `output_check.py:527` (`_CLAUSE_BREAK`), `:532` (`_RELATIVE_WHICH`), `:535` (`_asks`), used at `:822`.
- **What is wrong:** `_asks` returns True whenever any whether/if/which appears earlier in the same sentence, within 60 characters, with no listed break in between. That is not the same as "the asking word governs the clause". Each of these is exempt with an empty `LiveValues` (I traced each one through the regexes):
  - **The user's own blocked example without its comma:** "If you need it soon the Sage M is available now." Short conditionals often drop the comma.
  - **"but/and/so" followed by a word not in the subject list:** "Not sure if you saw but M is available." / "Check if it fits and XL is available today." / "…and all sizes are available." / "…so Sage is available." Size letters, colour names, product names, all, both, every and each are not in the list.
  - **Other conjunctions that never break:** or, because, since, as, then, yet, while, although. Example: "Whether you hike or commute the M is still available."
  - **A relative "which" with a subject before its verb:** "The size which you asked about is available now." `_RELATIVE_WHICH` only rejects a verb directly after "which".
  - **An answer after the match:** "Here is which size is available now: the M." The colon comes after "available", so nothing breaks the clause.
  - **Less likely:** boundary and break lookalikes NFKC does not fold. A missing space after "?" ("…if it fits?The M is available now") lets the previous sentence's "if" govern. "؟", "、", "―", "‒" and U+2028 also act as no break.
- **Is your narrowing safe?** No. You narrowed the user's rule to "but/and/so followed by a subject word", and that reopens cases with a size letter, a colour or a quantifier. EV-045 only needs the exception for "and". Its "and" sits inside the noun phrase right after "which", before any verb. "but" and "so" never need the exception.
- **Fix, fail-closed, keeping all five measured false positives passing:**
  - Require an embedded-question frame. The asking word must directly follow, within 1–3 words with no break, a verb from a closed list: see, check, show(s), confirm, know, say, tell, ask, find out, sure, wonder. All five false positives have this frame ("to see whether", "will show whether", "shows which", "can't say whether"). A sentence-initial or concessive "if"/"whether" is then never exempt.
  - Make but, so, or, then, yet, because, since, although and while unconditional breaks.
  - Make "and" a break except in `which <word>{1,3} and <word>` before the first verb.
  - Do not exempt when a colon or dash follows the match in the same sentence.
  - Add every example above to `test_an_asking_word_that_does_not_govern_the_clause_leaves_a_claim`.

### 3. MEDIUM, unsafe (hard rule 2; D3): negated stock claims pass when the tool says in stock
- **Where:** `output_check.py:505` (`stock_vouched`) and `:303-305`, where #13's patterns now carry `live_stock`.
- **What is wrong:** `stock_vouched` reads only the span. "in stock", "available now" and "currently available" take the positive branch (`all(n > 0)`) even when a negation comes before them.
- **How it fails:** `check_stock` returns on_hand 5, and the model writes "The M isn't in stock right now." or "The M is not available now." Both pass. The polarity is inverted: the true statement at on_hand 0 is blocked.
- **Regression:** on main, "in stock" had no exemption, so "not in stock" was always blocked. Now it passes once any variant is in stock.
- **Fix:** in `_exempt`, look back in the clause for a negation (not, n't, no longer, never, none, no). A negated positive phrase passes only under the negative rule: every checked variant at 0.

### 4. MEDIUM, unsafe (hard rule 2; the row's own "number words" open item): stock quantities the count pattern cannot read pass unchecked
- **Where:** `output_check.py:330-335` (`_COUNT` is `\d{1,4}|_SPELLED_COUNT`) and `:500` (`_COUNT_IN`).
- **How it fails:**
  - "There are a dozen left." / "Only a few left!" / "only a handful left" / "a couple left" match no pattern and pass with no check. "dozen" is in TRIGGER and `_NUMBERISH_WORDS`, but not in `_COUNT`.
  - "There are 12345 left." matches nothing (five digits).
  - "10000 in stock" passes on the "in stock" polarity alone, with the count never checked.
  - "1,200 units available" is read as 200.
- **Fix:**
  - set `_COUNT` to `\d[\d,]*` and make `_COUNT_IN` parse the whole token;
  - add quantifier patterns (dozen, few, handful, couple, several, plenty, lots, hundreds) before left / remaining / in stock / available, with no exemption, since no tool returns them.

### 5. MEDIUM (hard rule 7 defence in depth; plan §3.6): the eval leak guard on catalog results runs too late
- **Where:** `evals/src/northwind_evals/answerers.py:417`, with `serve_catalog` at `:468-481`.
- **What is wrong:** refinements are refused inside `SavedFirstIndex.search` (line 212) before the model sees them. Catalog results are only checked after the whole turn.
- **How it fails:** in record mode, a canary or internal text in a catalog result has already reached the model. It is also already written into the answerer's next request, which the proxy stores in `cassettes/eval-answers` and `.llm-proxy-log`. Only then is `AnswerError("leak")` raised. The plan's claim "before they reach the judge or a cassette" holds for the judge only.
- **Mitigation:** mcp-catalog's visibility filter is the primary control.
- **Unconfirmed:** whether any other scan finds canaries in cassettes. `scan-cassettes` is documented as checking secrets and PANs.
- **Fix:** run `index._leaking` inside `Recording.call_tool` on the unlabelled body. On a leak, return the fixed transient error envelope and set the leak flag, as the index does.

### 6. LOW (hard rule 2 residual; D2): MAX_TURNS 6 does not bound catalog calls
- **Where:** `services/assistant/src/northwind_assistant/turn.py` (`MAX_TURNS`).
- **What is wrong:** it caps model rounds, not `tool_use` blocks. One response can issue dozens of parallel `get_price` / `check_stock` calls. Only `max_tokens` (8192) limits that, so a turn can make hundreds of catalog calls.
- **Why it matters:** with change 6's residual (any `get_price` result this turn vouches any figure), a broad price sweep makes most plausible figures "live". Budget caps still bound spend.
- **Fix (the hardening item):** a per-turn cap in mcp-catalog keyed on the token's `turn` claim, as mcp-rag does for RAG-10. Optionally, also stop recording live values past N per turn.

### 7. LOW (evals; the user's change 3): `live_backed` can lower the citation requirement
- **Where:** `output_check.py:1208` and the call at `:1226`; used at `evals/src/northwind_evals/harness.py:288`.
- **What is wrong:** for availability matches it counts `stock_vouched` instead of a claim. So "I can check whether the S is available too." counts as a live-backed claim whenever all checked variants are above zero. It also counts per sentence, while the judge may merge repeated price mentions into one claim. Either way, `citable - live_claims` drops more than the live claims.
- **Unconfirmed:** whether the judge classes a price claim as `product`. If it classes it as `other`, the subtraction removes real product claims.
- **What holds:** the change cannot hide an invented source. The rule that no citation may name a source the turn did not return is independent, and `test_an_invented_source_fails_whatever_the_live_claims` and `test_a_live_marker_is_not_a_citation` cover it.
- **Fix:** skip availability matches where `_asks` is true. Cap `live_claims` at the number of judge claims whose kind is product.

### 8. LOW (hard rule 2 residual, not logged): stock polarity about an unchecked variant passes
- **How it fails:** the model checks only the XL (on_hand 4), then writes "The M is in stock." It passes.
- **What to log:** 0015 logs this kind of residual for counts and prices only. Add polarity claims to the PROGRESS "Open items" entry.

### 9. LOW (hard rule 5; scope note for the user): the judge gets every catalog result as support, not just live values
- **Where:** `answerers.py` `live_of`.
- **What it does:** it passes every successful catalog result to the judge, including `search_products`, `get_product` and `get_variant`, not only price and stock. A product attribute present only in `get_product` JSON now counts as "supported", including for the absolute `fabricated_ok`.
- **Ask the user:** confirm this is what D6 meant. Otherwise limit `live` blocks to `get_price` / `check_stock`.

No prompt-injection or review-steering text was found in the diff, the fixtures or the review documents.

## Checked, no findings
- **Hard rule 1:** catalog tool arguments still pass `card_data_refused`. The gateway refuses card data before any model call. No logging was added.
- **Hard rule 2:** no finding beyond findings 1–4, 6 and 8.
  - **`record_live` provenance holds.** Live values come only from a `ToolResultBlock` whose `tool_use_id` belongs to a `ToolUseBlock` named PRICE_TOOL or STOCK_TOOL, and the label must name the same tool. They must also have `is_error` false, exactly one strict `<catalog_data>` label, the caller's region, and well-formed amount, currency and stock values.
  - **What cannot create a live value:** model text, retrieved text, a forged label in a source, `retrieve_context` output, another tool's label, or another region's result.
  - **`unlabel` is the only reader.**
  - **D5 holds:** the shopper's own figure is blocked.
  - **A split figure is never released early:** `$`, `£`, `€` and digits are triggers. A match at the buffer end is held, and the text is re-scanned until the trigger leaves the L window. Spelled amounts that start at a non-trigger "a" are handled by the Reset path.
- **Hard rule 3:** synthetic fixtures and test values only.
- **Hard rule 4:**
  - the token key comes from the environment only, and `CatalogEndpoint.key` is `repr=False`;
  - the CLI gets per-server bearer tokens, never the key;
  - the eval run uses a fresh `secrets.token_bytes(40)`;
  - `NORTHWIND_MCP_TOKEN_KEY` is generated per `make dev` run and not echoed.
- **Hard rule 5:** no finding beyond findings 7 and 9. The prompt adds the site/support rule.
- **Hard rule 6:**
  - `label()` escapes the body: NFKC, `ensure_ascii`, and `<`, `>`, `&` written as `<`, `>`, `&`. JSON escapes newlines, so no value can close or forge the tag.
  - The `tool` attribute comes from TOOL_NAMES only.
  - Errors are the bare fixed-text envelope (`tools.py:59-99`, `server.py:45-51`).
  - The prompt labels catalog output as data.
  - The judge blocks go through `as_data` (NFKC, then `html.escape`).
- **Hard rule 7:** the server-side visibility filter is unchanged; finding 5 is defence in depth only.
- **Hard rule 8:**
  - `tools=[]` and `permission_mode="dontAsk"`;
  - exactly two MCP servers;
  - `allowed_tools` is `retrieve_context` plus the five read tools, taken from `tool_scopes`;
  - the catalog server lists only scoped tools and has no resources;
  - `set_price` / `set_stock` are not tools, and no refund, capture or assign tool exists;
  - `mint(server=)` is required, `_verify` is private, and `verify_for` checks `srv`, so each server refuses the other's token (tested). One key across servers is the logged L3 item.
- **Hard rule 9:** not applicable to this change (no state-changing tool added).
- **Hard rule 10:** envelopes unchanged. An empty `search_products` result stays a labelled success, distinct from an error.
- **Hard rule 11:** the portal keeps `<AiDisclosure />`, and the banner makes no figure claim.
- **Decision 0001, placeholder key:** `agent_runtime` / `llm.py` are untouched. The CLI still gets only the placeholder, and the new MCP headers carry MCP tokens, not Anthropic credentials.
- **Decision 0001, Makefile does not export the key:** lines 35-38 still `override` and `unexport ANTHROPIC_API_KEY`. The change only adds the `dev-mcp-catalog` target.
- **Decision 0001, CI:** no workflow change; CI stays in replay with no key in secrets.
- **Decision 0001, key file:** nothing copies `LLM_ENV_FILE` or `.env`.
- **Decision 0001, budget caps:** `LLM_BUDGET_USD` is unchanged. The planned RUN_BUDGETs are per-window values the user sets.
- **Decision 0001, cassette scanner:** `scan.py` and the scan paths are unchanged, so new cassettes will be scanned. Catalog results in request bodies hold only synthetic prices.
- **`judge.py` unchanged:** it is not in the diff. I could not run the byte-compare test against origin/main.

## Could not confirm (no command execution)
- That the tests pass, including `test_judge_py_is_unchanged_from_main`.
- Whether the harness ever runs items concurrently. `CatalogLog` and `SavedFirstIndex` are both reset per item, so they would mix between items if it did.
- Whether the CLI flags an unreachable MCP server's result as `is_error`. Either way the text is not a label, so it is ignored.
- Whether any scan finds canaries in cassettes (finding 5).
- How the judge classes price claims (finding 7).

Files: /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/output_check.py, /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/tools.py, /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/turn.py, /Users/rapido/Projects/northwind-shop/services/common/src/northwind_common/catalog_tool.py, /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/answerers.py, /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/harness.py, /Users/rapido/Projects/northwind-shop/docs/decisions/0015-output-control.md
