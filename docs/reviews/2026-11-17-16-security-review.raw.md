# Row #16 security review, pass 1 (raw, verbatim)

Reviewer: `security-reviewer` (read-only). Diff: `/tmp/claude-502/northwind-security-16.diff`, `base 5539421`,
`head 707b5c20adef1f368512e109c8096630605518db`. Saved verbatim by the session, 2026-11-17.

---

## Security review: row #16 (CHK-01, cart tools)

**Scope.** I reviewed `/tmp/claude-502/northwind-security-16.diff`. Its first lines are `base 5539421` and `head 707b5c20adef1f368512e109c8096630605518db`. I checked it against the 11 hard rules in CLAUDE.md and against decisions 0001 and 0005, both read fresh for this review. Where the diff did not give enough context, I also read the source files themselves. Every line number below points at the head checkout.

**Result.** 8 findings: no critical or high, 4 medium (2 of them unsafe), 4 low.

---

### F1: medium, unsafe (hard rule 2; hard rule 9 semantics). A replayed idempotency key returns an old cart snapshot as if it were live, and silently skips a real later write

- **Where:**
  - `services/mcp-catalog/src/northwind_mcp_catalog/cart.py:402-412`: the stored `CallToolResult` is returned as-is.
  - `cart.py:442`: the key example in the schema.
  - `services/assistant/src/northwind_assistant/tools.py:257-271`: `_record_cart` records whatever comes back.
- **What is wrong:**
  - The stored value is the entire result. That includes the `cart` view with each line's `unit_price`, `line_total`, `on_hand` and the `subtotal`, all as they were when the key was first used.
  - Keys live per (session, region) across turns, up to 50 of them, with no check that the cart is still in the state the key saw.
  - The schema's example key, `add-p0001-blk-m-1`, is built only from the arguments. So the natural key for a later identical request is the same string.
- **How it fails:**
  1. Turn 1: the shopper adds P0001-BLK-REG-M with key `add-p0001-blk-m-1`.
  2. Turn 2: the shopper removes it.
  3. Turn 3: the shopper adds it again. If the model reuses the same key, the server replays turn 1's "added" result and the cart does not change.
  - The assistant then says "it's in your cart" while the cart is empty. The same happens with `remove-…` keys after a re-add: the shopper is told the item was removed, but it is still in the cart.
  - `record_live` puts the replayed turn-1 prices and stock into `state.live`. If the price changed in between (`set_price`), the output check now accepts a stale price as a live one.
  - The test at `services/mcp-catalog/tests/test_chk01_criteria.py:597` confirms the replay returns identical bytes. Nothing tests the remove, re-add, same-key sequence.
- **Suggested fix:**
  - Store only the write's outcome (`result`, `variant_id`, `quantity`). On replay, rebuild `cart` from the live store and mark the result `"replayed": true`.
  - Scope keys to the turn, `(claims.turn, key)`. Retries happen inside one turn's token, so a key from an earlier turn should count as a new write.
  - Change the schema example so a key is not derived from the arguments alone.
  - Add a test for remove, re-add, then the same key again.

### F2: medium, unsafe (hard rules 2 and 6). Cart totals and cart-line stock let model-chosen quantities vouch for unrelated price and stock claims

- **Where:** `services/assistant/src/northwind_assistant/tools.py:257` (`line_total` added to `live.prices`), `:263` (`subtotal`), `:271` (each line's `on_hand` into `live.stock`).
- **What is wrong:**
  - `LiveValues.prices` is a set of (amount, currency) pairs that is not tied to any variant. `output_check.py:1010-1026` exempts any matching figure anywhere in the answer.
  - Line totals and subtotals are server-computed, but they depend on quantities the model picks: 1 to 10 per line, 20 lines.
- **How it fails:**
  - The cart holds 2 of a $60.00 item, so the line total is 120.00 USD. The answer says "the Squall jacket is $120.00", which is invented, and the check lets it through.
  - Injected text that steers a cart write (the accepted residual) can produce a whole family of vouched amounts.
  - The same goes for stock. Every cart line's `on_hand` joins `live.stock`. `stock_vouched` (`output_check.py:702-708`) passes a count that matches any recorded value, and passes "in stock" when every recorded value is above zero. So a cart read can vouch a stock claim about a product that was never checked.
- **Suggested fix:**
  - Record only `unit_price` in `live.prices`.
  - Keep totals and the subtotal in a separate set that is exempt only inside a "total/subtotal" phrase. Better still, bind prices and stock to a variant.
  - Do not loosen the check to compensate.

### F3: medium (not unsafe; hard rule 6 / NFR-03 residual). The accepted "recorded check" against injected cart writes currently checks nothing

- **Where:** `services/assistant/tests/test_chk01_cart.py:283-300`.
- **What is wrong:**
  - The user accepted "prompt plus a recorded check" as the mitigation for injection steering a cart write. That check scans `cassettes/assistant-redteam` and the adversarial `eval-answers` cassettes for cart `tool_use` blocks.
  - All of those cassettes were recorded before the cart tools existed. A grep of `cassettes/` for `add_to_cart` finds 0 matches, so the model was never offered the tools in any recording.
- **How it fails:** the test passes today without having seen a single turn where a cart write was possible. Until W2 and W5 re-record, the implementation is weaker than the accepted mitigation, and nothing signals that.
- **Suggested fix:** make the test also assert that each scanned cassette's request offered `mcp__northwind-catalog__add_to_cart`. Until W2 and W5, let it fail or be visibly marked stale with a reason, and list it in the gate report. This tightens the check; it does not loosen anything.

### F4: medium (not unsafe; denial of service). Anyone who can reach the gateway can flush every shopper's cart with requests that cost nothing

- **Where:**
  - `services/mcp-catalog/src/northwind_mcp_catalog/cart.py:302`: `get_cart` calls `carts.open`, which creates a cart.
  - `cart_store.py:75`: a new cart is inserted, and the least recently used one is evicted past `MAX_CARTS = 10_000` (line 23).
  - `services/gateway/src/northwind_gateway/session.py:201-206`: every request without a cookie gets a fresh session.
- **How it fails:** about 10,000 `GET /api/cart` requests without a cookie each mint a new session. Each one makes `get_cart` insert an empty cart, which evicts every real cart. No model call is needed.
- **Suggested fix:**
  - `get_cart` should look up the cart without creating it, and return the empty view for an unknown session. Only a successful write should create a cart.
  - Consider limiting how fast new sessions are issued.

### F5: low (not unsafe; denial of service). The idempotency store's memory bound is about gigabytes in the worst case

- **Where:** `cart_store.py:26` (`MAX_KEYS = 50`), `cart.py:412`.
- **What is wrong:** each kept key holds a full `CallToolResult`, including a view of up to 20 lines.
- **Estimate (unconfirmed, not measured):** about 5-10 KB per result, so up to about 0.5 MB per cart and about 5 GB at 10,000 carts. Reaching it needs writes, which means assistant turns, so the LLM budget makes it costly.
- **Suggested fix:** store only the compact outcome (this also fixes F1), or add a store-wide cap on stored keys.

### F6: low (not unsafe; hard rule 10 / behaviour with the key missing). With no key, `/api/cart` returns a 500 rather than the documented 503

- **Where:** `services/gateway/src/northwind_gateway/cart.py:38-40, 56`.
- **What is wrong:**
  - Decision 0019 and `session.py` say that without the key, `/api/cart` answers 503.
  - The real dependency `cart_reader()` calls `signing_key()` while FastAPI resolves dependencies, before the handler's `try`. A `TokenKeyError` escapes, the response is a plain 500, and uvicorn logs a traceback. The exception text is `"NORTHWIND_MCP_TOKEN_KEY is not set to a key of at least N characters"`, which holds no secret, so this still fails closed.
  - `test_without_the_key_there_is_no_session_and_the_cart_is_unavailable` overrides `cart_reader`, so the real path is never tested.
  - Separately, `lru_cache` keeps the first key it reads, while the middleware rereads the key on every request. They can disagree until the process restarts.
- **Suggested fix:** read the key inside the handler's `try` (or catch `TokenKeyError` there and return `_unavailable()`), and test it without the override.

### F7: low (not unsafe; session binding). Session fixation through the shared localhost cookie jar

- **Where:** `session.py:179-180, 201-206`.
- **What is wrong:** browsers do not separate cookies by port, so every service on localhost shares the `nw_session` cookie. A page on another localhost port can plant its own validly signed `nw_session` cookie in a browser that has none yet. That browser then shares a cart with the attacker, who can read and write it.
- **Impact today:** low. Carts are anonymous and hold no personal data, and the cookie cannot be forged or read: it is HMAC-checked with `compare_digest` and is HttpOnly.
- **Suggested fix:** none needed now. Record that SA-04 must issue a new session at login and must not inherit an anonymous cart without checking it.

### F8: low (not unsafe; hard rule 5). The prompt pushes the model to state attributes taken from a catalog result

- **Where:** `services/assistant/src/northwind_assistant/prompt.py:37` versus `:40`. Also `evals/src/northwind_evals/answerers.py`, `judge_view`.
- **What is wrong:** line 40 says to offer refused-add alternatives "named as it names them". In practice that means naming a colour, size and fit taken from a catalog result. Line 37 says "never state an attribute from a catalog tool result".
- **How it fails:** `judge_view` drops names, colours, sizes and fits. So a correctly offered alternative ("the Charcoal relaxed M") has no support in front of the judge. Either the judge marks it unsupported, or the model learns to state uncited attributes.
- **Suggested fix:** offer alternatives by product name plus variant ID only, or route their attributes through a cited source. Settle this before W4 records.

---

### Could not confirm

- Whether the recorded model actually reuses keys built from the arguments (F1). No cart cassette exists yet. The mechanism is confirmed; how often it happens is not.
- The memory figures in F5. They are estimates, not measurements.
- Whether variant visibility can change while the server runs. If it can, an F1 replay could return a stored view that includes a variant that has since become internal (hard rule 7). The live `view()` filters such a variant out; a replay does not.

### Note for the gate report (not a finding)

The diff changes `evals/src/northwind_evals/answerers.py`, which is on the control surface. Expect `Guard / eval-gate-main` to fail on purpose, and the PR will need the user's `gate-change` label (decision 0009).

---

### Checked, no findings

- Hard rule 1: no card data in cart arguments, the cookie, logs or tool output. `_checked` scans every key and value, `idempotency_key` included, before any lookup. The cookie is hex. Logs name only categories and codes.
- Hard rule 2: no finding on where prices come from. Carts store only quantities, and `view()` reads price and stock live. The open issues are F1 (replay) and F2 (the check's exemption).
- Hard rule 3: no finding. Fixtures (`cart.json`, test sessions) are synthetic.
- Hard rule 4: no finding. The key comes only from the environment. `database_name` prints only a name matching `[A-Za-z0-9_.-]`, never a URL or password. The session key is derived one-way, and the MAC domains are separate: the token MAC covers `v1.<payload>`, the cookie MAC covers the id under the derived key. A cookie signed with the raw token key is rejected, and a test covers that.
- Hard rule 5: one low finding (F8); otherwise no finding.
- Hard rule 6: no finding on labelling. Cart output and the refusal's second block are `<catalog_data>` labels. `labelled_result` accepts a label after an error only when it follows our own envelope with an `out_of_stock` or `insufficient_stock` code. The model cannot write tool-result text. The open issues are F2 and F3.
- Hard rule 7: no finding. `view()`, `_variant` and the alternatives all go through `_visible_variant`, `_visible_product` or `_sees`. Alternatives must be priced in the region and in stock. P0009 and internal variants are excluded.
- Hard rule 8: no finding. `shopper-portal` gets `get_cart` only, and the server enforces this through `_offered()`. Contract tests pin that only `shopper-assistant` can write. Carts refuse a non-shopper audience. No order, payment, refund, capture or assign tool is reachable.
- Hard rule 9: every write requires a key. Conflicts are detected across tools, because the tool name is in the fingerprint. Check and write happen under one lock with no `await` inside it. Eviction behaves as documented. No `dry_run`, as the user accepted. The open issue is F1.
- Hard rule 10: no finding for the tools. Every cart error is a fixed envelope that echoes no argument. Empty and failed results stay distinguishable: an empty cart is a valid view, and `/api/cart` failures return 503. The route gap is F6.
- Hard rule 11: no finding. `ChatPanel` wraps `Chat`, so `AiDisclosure` still renders. The prompt's disclosure is unchanged.
- Session binding, `sid` validation: no finding. `sid` must be exactly 32 lowercase hex characters (fullmatch), appears on shopper tokens only, and the claim set is exact. Forged, malformed or wrongly keyed cookies get a new session. No header, body or query value can set the session. `sid` stays out of model input, tool arguments, tool results, logs and response bodies, and tests pin this.
- Decision 0001, the child process gets only a placeholder key and only the proxy holds the real key: not applicable to this change.
- Decision 0001, the Makefile does not export the key: not applicable. The Makefile is not in the diff.
- Decision 0001, no key in CI secrets and CI runs in replay: not applicable. `.github/workflows` is not in the diff.
- Decision 0001, the key file is never copied into a worktree: not applicable to this change.
- Decision 0001, budget caps enforced and not raised: not applicable. No cap or `RUN_BUDGET` changes in the diff.
- Decision 0001, cassettes still covered by the secret/PAN scanner: not applicable. No cassettes are added, and the scanner is unchanged.
- Decision 0005, the runner's key handling: not applicable to this change. No runner, proxy or `.claude` paths are touched.

No text in the diff, fixtures or plan tried to steer this review.
