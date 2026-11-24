# Row #16 security re-review, pass 2, scoped (raw, verbatim)

Reviewer: `security-reviewer` (read-only). Diff: `/tmp/claude-502/northwind-security-16-rereview.diff`,
`base 707b5c20adef1f368512e109c8096630605518db`, `head f3f97a1ccae2c8cf406854d281d427d0a83ae4fc`. Saved verbatim by the
session, 2026-11-17.

---

## Security re-review, row #16 (CHK-01, cart tools), pass 2 (scoped)

**Scope.** I reviewed `/tmp/claude-502/northwind-security-16-rereview.diff`. Its first two lines are `base 707b5c20adef1f368512e109c8096630605518db` and `head f3f97a1ccae2c8cf406854d281d427d0a83ae4fc`. I read CLAUDE.md "Hard rules" and decision 0001 fresh, and read the source at head. Line numbers point at head. I did not run any test, because I have no Bash.

**Result.**
- 6 of the 8 findings are closed (F2, F3, F5, F6, F7, F8). 2 are partly closed (F1, F4).
- 4 new findings: 1 medium, 3 low.
- Nothing is critical or high, and nothing is unsafe.
- The W4 fixed-retriever change is not unsafe for hard rules 2, 5 or 7. I have one low hardening suggestion for it (N4).

---

### Verdicts on F1–F8

**F1: partly closed.**
- **Closed, the hard-rule-2 half:**
  - A replay stores only `result`, `variant_id` and `quantity`: `services/mcp-catalog/src/northwind_mcp_catalog/cart.py:425-430`.
  - It returns them with `"replayed": true` and a live `view()`: `cart.py:420-422`.
  - So no stored price, stock or line can come back. A variant that has since become internal is filtered by `view()`, which also closes pass 1's unconfirmed hard-rule-7 point.
  - The schema example is no longer built from the arguments: `cart.py:457-461`, `509/514/521`.
  - Pinned by `services/mcp-catalog/tests/test_cart.py:691` (remove, re-add, same key: replay with an empty live cart), `test_cart.py:706` (the replay shows the price after `set_price`), `test_cart.py:596`, and `test_chk01_criteria.py:597, 616`.
- **Not closed, the "silently skips a real later write" half:** it is still reachable. See N1.

**F2: closed.**
- `services/assistant/src/northwind_assistant/tools.py:244-261` records only each line's `unit_price`. Totals and the subtotal are parsed for shape only. Cart-line stock is never recorded.
- `tools.py:235-243`: a refusal records only the requested variant's stock.
- Pinned by:
  - `services/assistant/tests/test_chk01_cart.py:126, 133, 155, 189`. Line 189 blocks "$278.00" both as a total and as "The Squall jacket is $278.00", and blocks "Only 5 left."
  - `test_chk01_assistant_criteria.py:139, 149, 170, 257`
  - `evals/tests/test_chk01_eval_cart.py:110`
- What remains is that `LiveValues.prices` is still not bound to a variant. That predates this row and is shared with `get_price`, so it is not a regression.

**F3: closed (the test now fails until re-record, by design).**
- `test_chk01_cart.py:283-288` (`offered_cart_writes`), `:303`, `:317`. There is no `xfail` or `skip`.
- I confirmed the path it reads, `match.body.tools`, matches the real cassette layout (`cassettes/assistant-redteam/0226…json:9-10`).
- All 33 red-team cassettes list `mcp__northwind-catalog__*` tools. So once W2 re-records, the check can pass; it is not permanently red.
- eval-answers judge cassettes have `test_id` `eval-judge`, so they are never scanned as adversarial items.
- **Gate-report note:** `make test` and `make ci` are red at this head until W2 and W5. The fix is to re-record. Removing or loosening either assertion would be stop 5.

**F4: partly closed (closed in code, not pinned).**
- `cart.py:303` opens with `create=False`. `services/mcp-catalog/src/northwind_mcp_catalog/cart_store.py:77-78` returns a detached cart and never stores it.
- The only test, `test_cart.py:722`, calls `CartStore` directly. No test drives `get_cart` over MCP and checks that nothing was stored. If `cart.py:303` went back to the default `create=True`, every test would still pass.
- **Suggested fix:** call `get_cart` through the server for several fresh sessions with a small `max_carts`, then assert that an existing cart survives and `len(server.carts)` is unchanged.
- Low residual: a write attempt that is refused still creates a cart. `cart.py:414` opens with `create=True` before `write()` runs. Only a `shopper-assistant` token can reach that path, so each attempt costs a model turn. Not unsafe.

**F5: closed.**
- Each stored entry is now a fingerprint plus a 2-3 field outcome: `cart.py:427-428`, `cart_store.py:38`.
- Rough estimate, not measured: about 200 B per key, so about 100 MB worst case at 50 keys × 10,000 carts.
- Indirectly pinned by `test_cart.py:706`.

**F6: closed.**
- `services/gateway/src/northwind_gateway/cart.py:36-43` catches `TokenKeyError` and returns `None`. Lines 61-63 then return the fixed 503.
- The removed `lru_cache` also fixes the key mismatch with the middleware.
- Pinned without the override by `services/gateway/tests/test_cart_session.py:233`.

**F7: closed (logged, as suggested).** `PROGRESS.md:155`, owner #19.

**F8: closed.**
- `services/assistant/src/northwind_assistant/prompt.py:39-42` offers alternatives by product name and variant ID only.
- `evals/src/northwind_evals/answerers.py:357-369` shows the judge the same two fields.
- Pinned by `test_chk01_cart.py:257` and `evals/tests/test_chk01_eval_cart.py:120`.
- Low residual: see N3.

---

### New findings

**N1: medium, not unsafe (hard rule 9 semantics; the rest of F1). With per-cart keys and turns that have no memory, a new request can still become a silent replay, and the replay tells the model "added".**
- **Where:**
  - `cart.py:421`: the replay puts the original `"result": "added"` / `"removed"` at the top level.
  - `cart.py:457-461`: the key description.
  - `cart.py:509, 514, 521`: fixed example keys.
- **What is wrong:**
  - Keys are per cart and last across turns. Each turn is a fresh context (SA-05), so the model cannot know which keys earlier turns used. "Never used before" is an instruction it has no way to follow.
  - The description says to reuse a key "only to retry the same change **in this turn**". That implies a key from an earlier turn is free to reuse, which is the opposite of what the server does.
  - The examples are fixed literals (`7f3c9a21`, `c41d06e8`, `9b2e5f70`). A model that copies an example's key reuses the same key in every turn.
- **How it fails:**
  1. Turn 1: add V with `7f3c9a21`.
  2. Turn 2: remove V with `c41d06e8`.
  3. Turn 3: add V again with `7f3c9a21`. The server replays: `{"result":"added","quantity":1,"replayed":true,"cart":{"lines":[]}}`. The cart does not change, and the model may say "Added" from the top-level `result`.
  4. Turn 3, other case: adding a different variant W with `7f3c9a21` gets `idempotency_conflict` (isRetryable false). The shopper cannot add anything with that key until 50 newer keys evict it.
- **Why not unsafe:** no stale price, stock or line reaches the model or the check. The cart panel reads live. Nothing changes that the shopper did not ask for. The action is reversible and not monetary.
- **Not pinned:** no test or recording covers the model's handling of a replay. W4 uses three different tools, so copied example keys would not collide there.
- **Suggested fix (the user's per-cart rule unchanged):**
  - Make a replay unambiguous at the top level. For example: `"result": "already_applied"`, with the original under `"original": {...}`.
  - Make the description match the server: keys are remembered for this cart across turns.
  - Have the prompt say that a `"replayed"` result means nothing changed now.
  - Raise with the user, as a scope question rather than as unsafe: a memoryless model cannot guarantee unique per-cart keys. If they want collisions ruled out, the key needs a source the model doesn't choose.

**N2: low, not unsafe (hard rule 9, fails open).**
- **Where:** `cart.py:425-430`.
- **What is wrong:** after a successful write, the outcome is rebuilt by re-parsing the result text with `catalog_tool.unlabel`. If that ever returned `None`, the key would not be stored, and a retry with the same key would apply the change a second time.
- **Today:** this cannot happen. `label()` writes compact ASCII JSON that `unlabel` reads back, and `test_cart.py:596` covers it.
- **Suggested fix:** build the outcome from the write's value directly, or fail closed if the parse fails.

**N3: low, not unsafe (hard rule 5; the rest of F8).**
- **Where:** `prompt.py:40-42`, `answerers.py:365`.
- **What is wrong:** variant IDs encode colour, fit and size (`P0001-BLK-REG-L`). Only "without describing them further" stops the model from decoding one into "the black regular L", which would be an uncited attribute. The judge sees the same `variant_id`, so it may accept a decoded attribute as supported.
- **Suggested fix:** add a test or judge rubric line saying a variant ID is not support for colour, size or fit.

**N4: low, not unsafe (hard rule 7 hardening, test only).**
- **Where:** `services/assistant/tests/test_cart_journey_e2e.py:50-69`.
- **What is wrong:** `ProductSources` ignores the caller, so it skips the index's access and region filtering. P0001 and P0008 are safe today: `data/fixtures/products.jsonl` lines 1 and 8 show `visibility: public`, `status: active`, and regions US, CA, UK and EU. Nothing asserts that, though, so a later data change could put an internal record in front of the model during recording.
- **Suggested fix (tightens the test):** in `__init__`, assert `visibility == "public"` and `REGION in regions` for both products.

### The W4 fixed-retriever change (hard rules 2, 5, 7)

Not unsafe.
- **Rule 2:**
  - The chunk text is only `name` + `description`. Neither record's description has a price or stock figure, and `price_bands` is not used.
  - Stock is set in the live store, and every check reads the server's cart.
- **Rule 5:**
  - The sources are the products' own verified records, typed `product`, so citations point at real sources.
  - The chunk is narrower than a production chunk (no colour or size list), and every query, `retrieve_context` included, returns the same two chunks.
  - So the W4 cassette is evidence for cart behaviour, not for retrieval or RAG-08 grounding. The docstring already says this.
- **Rule 7:** safe for these two public records. See N4.

---

### Could not confirm

- Whether the recorded model copies the example keys or reuses keys across turns (N1). No cart cassette exists yet.
- That `test_cart_session.py:233` passes and that the F3 tests fail as described. I could not run tests.
- Whether `mcp_catalog_url()` can raise anything other than `TokenKeyError` inside `cart_reader`. If it can, that would still be a 500. I did not read `northwind_common/localenv.py`.
- The F5 memory figure. It is an estimate.

### Checked, no findings (scoped to this diff)

- Hard rule 1: no finding. No card data path changed; `_checked` still runs before the key lookup (`cart.py:407`).
- Hard rule 2: no finding after F2 and F1's replay fix.
- Hard rule 3: no finding. The fixtures read are synthetic.
- Hard rule 4: no finding. The key is read per request from the environment. `CartReader.key` has `repr=False`. The log line holds no key.
- Hard rule 5: low N3 only.
- Hard rule 6: no finding. Labelling is unchanged.
- Hard rule 7: low N4 only. Replays now go through `view()`.
- Hard rule 8: no finding. The tool set and scopes are unchanged.
- Hard rule 9: N1 (medium) and N2 (low). Every write still requires a key.
- Hard rule 10: no finding. F6 is closed; replays and conflicts use the fixed envelopes.
- Hard rule 11: not applicable to this change.
- Decision 0001, the child process gets a placeholder key only and only the proxy holds the real key: not applicable to this change.
- Decision 0001, the Makefile does not export the key: not applicable. The Makefile is not in the diff.
- Decision 0001, no key in CI secrets and CI runs in replay: not applicable. `.github/workflows` is not in the diff.
- Decision 0001, the key file is never copied into a worktree: not applicable to this change.
- Decision 0001, budget caps enforced and not raised: not applicable. No cap or `RUN_BUDGET` changed.
- Decision 0001, cassettes still covered by the secret/PAN scanner: not applicable. No cassette added, and the scanner is unchanged.

**Gate-report notes (not findings).** The diff changes `evals/src/northwind_evals/answerers.py` (control surface), so expect `Guard / eval-gate-main` to fail on purpose and to need the user's `gate-change` label. The F3 tests keep `make ci` red until W2 and W5 re-record.

No text in the diff, the pass 1 report, fixtures or cassettes tried to steer this review.
