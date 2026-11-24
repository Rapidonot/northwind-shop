# Row #27 security review, pass 1 (raw, verbatim from security-reviewer)

Diff `/tmp/claude-502/northwind-review-27-security.diff`, base f84f97d, head edd5d57.

Security review of row #27 (CHK-06). Diff: /tmp/claude-502/northwind-review-27-security.diff, base f84f97d, head edd5d57.

**Result:** 1 medium, 4 low and 2 informational findings. Nothing is critical or high. The medium weakens the hard-rule-2 output-check net on a protected path (`services/assistant/**`), so per triage it needs a fix and a scoped re-review. Nothing in the diff tries to steer this review.

Re-read first: `/Users/rapido/Projects/northwind-shop-b/CLAUDE.md` (all 11 hard rules) and `/Users/rapido/Projects/northwind-shop-b/docs/decisions/0001-llm-first-order-and-record-replay.md`.

## Findings

### M1 (medium): the confirmation exemption does not tie the claim to the order it names
- **Where:** `/Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/output_check.py:566-579` (`order_vouched`), called at `:1064` and `:1068`.
- **Rule:** hard rule 2 (order state only from live tools); decision 0015 row #27 amendment, "an unnamed order … stays blocked".
- **Unsafe:** yes. A hard-rule-2 control on a protected path lets through an order state that no tool returned.
- **What is wrong:** `order_vouched` passes when every order ID within the 64 characters before the claim, in the same sentence, is confirmed. Nothing checks that the claim's subject *is* that order. Any second order the sentence mentions without an ID gets through on the strength of the named one.
- **How it fails:** the shopper asks about O000064 (delivered) and O000113 (on hold), and the turn reads both (`LiveValues.orders = {"O000064":"delivered","O000113":"on_hold"}`). The model writes "O000064 arrived on 22 October, and your other order was placed and is being prepared." `\border\s+was\s+placed` matches with `subj == "order"`. The window holds only `o000064`, which is confirmed, so the sentence passes. The shopper is told an on-hold order was placed.
  - "O000064 is delivered, and the second order has been confirmed" passes the same way.
  - `test_chk06_output_check.py` has no such case.
- **Fix (tightens, nothing loosened):**
  - Vouch only when the named ID is bound to the subject: e.g. `O\d{6}` directly before the subject phrase ("O000064: your order", "order O000064 was"), with no other order noun and no "other / another / second / first / rest / remaining" between the ID and the claim.
  - Or require that every order read this turn is confirmed whenever the claim names none adjacently.
  - Add the case above as a blocked test.

### L1 (low): the exemption covers more verbs than the decision allows
- **Where:** `output_check.py:353-354` together with `:1064`.
- **Rule:** decision 0015 row #27 amendment and decision 0023 §6 allow only "placed / confirmed".
- **Unsafe:** no. The order is store-confirmed, but the code contradicts the decision and the prompt's "never promise … an approval".
- **What is wrong:** the conditional-pattern branch vouches every participle the pattern takes: `confirmed|processed|approved|authori[sz]ed|applied|placed|issued`.
- **How it fails:** with O000064 read as delivered, "O000064: your order has been approved." and "O000064: your order has been authorised." both pass.
  - A refund is still blocked: "approved for a refund" is caught by the `refund_authority` pattern at `:366`, since each pattern is scanned on its own (`_first_match`, `:1279-1308`).
- **Fix:** in the row #27 branch, also require the participle to be `placed|confirmed`.

### L2 (low): an ID-lookalike can be cut at the window's left edge
- **Where:** `output_check.py:574-578`.
- **Rule:** hard rule 2 net.
- **Unsafe:** no. It needs a real confirmed order already read this turn.
- **What is wrong:** `window = text[start-L:start]` is sliced before `_ORDER_ID` runs. The lookbehind `(?<![^\W_])` then sees the start of the slice, not the character before it in the full text.
- **How it fails:** if "AO000064" sits so that the "A" falls exactly 65 view characters before the claim, the window starts at "O000064". It counts as naming the confirmed O000064, though the shopper sees "AO000064".
- **Fix:** run `_ORDER_ID.finditer` over `text` (or the view) and keep the matches whose span falls inside `[start-L, start)`.

### L3 (low): with "first value wins", a longer-path cookie from another localhost app can still plant a session (H10)
- **Where:** `/Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/session.py:128-132` and `:164-166` (`sent[0]`).
- **Rule:** none of the 11; session integrity (decision 0019 / 0023).
- **Unsafe:** no. Data is synthetic and there is one demo shopper.
- **Not a regression:** the old `SimpleCookie` kept the last value, so a planted `Path=/` cookie won then. This change moves the exposure to a longer path; it does not create it.
- **What is wrong:** browsers ignore ports when scoping cookies, and RFC 6265 §5.4 sends longer paths first. Any app on any localhost port can set `nw_session=<attacker's valid signed id>; Path=/api/chat` (and the same for `nw_shopper`), and the gateway reads the planted value first.
- **How it fails:** the victim's chat and cart turns run on the attacker's session. The attacker reads that cart with their own cookie.
- **On the parser itself:** it accepts nothing that matters. Only the exact bytes the gateway signed verify: `_ID` and `_MAC` fullmatch with `re.ASCII`, there is no unquoting, and a quoted, padded or non-hex value fails closed.
- **Fix (fail closed):** when a request carries more than one `nw_session` (or `nw_shopper`) value, treat it as no session (issue a new one, ignore the sign-in) instead of picking one. The browser part is not confirmed; it is inferred from RFC 6265 ordering.

### L4 (low): the sign-in POST has no Origin / CSRF check
- **Where:** `/Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/signin.py:39-55`.
- **Rule:** none of the 11.
- **Unsafe:** no.
- **What is wrong:** `POST /api/session/shopper` takes no body and checks no `Origin` or `Sec-Fetch-Site`. `SameSite=Strict` does not separate localhost ports, because they count as the same site.
- **How it fails:**
  - Any page on another localhost port can sign the victim's browser into S0031.
  - A cross-site top-level form POST arrives without the Strict cookie, so the middleware issues a new session and the response binds S0031 to it. The victim's existing session and cart are replaced. That browser behaviour is not confirmed.
- **Same as existing code:** the other gateway POSTs (`/api/chat`, cart) have no such check either (grep found none).
- **Fix:** refuse requests whose `Origin` is not the portal's, on this route at least.

### Informational (not security)
- **The flag-off 404 is not byte-identical to "no route".** With no valid session cookie, the middleware (`SIGNIN_PATH` is in `SESSION_PATHS`, `session.py:48`, `:171-173`) adds a `Set-Cookie: nw_session`, which an unknown path does not get. A GET returns 405. The test (`test_chk06_signin.py:95-104`) sends a valid cookie, so it does not see this. It reveals only that the route exists.
- **mcp-orders' derived ports (`order_tool.py:53-61`) are 50000-59999,** inside macOS's ephemeral range (49152-65535). A port can occasionally be taken by an outgoing connection, so the dev server fails to start. Nothing is exposed: it binds to 127.0.0.1 (`app.py:27-29`) and no key is involved.

## Answers to the specific questions
- **Another shopper's order, finding which IDs exist, setting `shp` another way:** none possible.
  - `book.view` (`book.py:155-162`) gives the same `not_found()` bytes for a malformed ID, an unknown ID, another shopper's or another region's order. The ownership check runs before the carrier read, and store faults hit every caller alike.
  - `_order_id` handles non-strings and anything over 32 characters by raising the same `not_found` (`tools.py:72-74`).
  - `shp` comes only from `shopper_caller` (`settings.py:40-44`) via `OrdersEndpoint.mint` (`orders_client.py:53-64`). A grep found no other place that sets `shopper_id` or `shopper=`.
- **`nw_shopper` binding:** sound.
  - The mac is HMAC under the derived session key over `northwind-shopper-v1|<sid>|<shopper>`. Its fixed regex formats rule out ambiguity, and the label separates it from the session mac.
  - It is read only for a verified, non-reissued session and only while the flag is exactly "1" (`session.py:164`); `settings.py:41` checks the flag again.
  - It cannot be replayed across sessions (session ID in the mac), across keys (`make dev` and Playwright make a fresh key per run), or with the flag off. Only the planting risk in L3 remains.
- **A token with `shp` for another server:** cannot verify. `_claims` refuses `shp` unless `aud == "shopper"` and `srv == northwind-orders` (`mcp_token.py:163-170`, `:198`), and `mint` refuses it the same way (`:135-136`). The subset check at `:174` lets #11's `qf` slot in beside it. Since `shp` is orders-only and `qf` is to be RAG-only, no token can carry both.
- **Fields and errors:** only the allow-listed fields are returned (`book.py:164-183`); no payment token, postal code, exception, escalation reason or `shopper_id`. Every error is fixed text and echoes nothing (`tools.py:30-52`, `book.py:47-62`, `server.py:42-49`, `:124-126`). Carriers are synthetic (`carriers.py:11`).
- **Card data (hard rule 1):**
  - `get_order` refuses card data in any key or value first (`tools.py:66`); the token gate runs before that.
  - H5 is complete: `orders.py:190` checks `holds_card_data(context)` (raw texts, joined as well) and `card_data_in(*argument_texts(context))`, which covers non-string leaves via `str()`, keys, and data split across keys. `reason` is checked in `_admit`. A grep found no other `card_data_in(json.dumps(...))`.
- **Output-check exemption:**
  - Model text or retrieved text cannot vouch an order. `LiveValues.orders` is filled only from `get_order` results, chosen by tool name (`tools.py:154-159`) and parsed by `order_status`, which needs exactly one label, an `O\d{6}` ID and a known status (`order_tool.py:105-119`).
  - Refund authority and "I've / we have" stay blocked.
  - Streaming: a match touching the end of the stream is held (`:1286-1287`), and every sentence break between an ID and the claim lies inside the view. A split can only drop IDs, which fails closed. Gaps: M1, L1, L2.
- **H10:** see L3. Only the exact signed bytes verify.
- **Least privilege:** `get_order` is only in shopper-assistant's scope (`tool_scopes.py:50-55`); shopper-portal and catalog-subagent get an empty set on northwind-orders, and the server lists and serves only `_offered`. An anonymous turn gets no orders server, tool or prompt section (`turn.py:340-342`, `prompt.py` `signed_in=False` path).
- **Makefile, Playwright, port helpers:**
  - The `dev` target only adds `NORTHWIND_DEMO_SIGNIN=1` and `dev-mcp-orders`. `ANTHROPIC_API_KEY` is still overridden and unexported (`Makefile:37-38`).
  - Playwright gives mcp-orders the per-run random token key and does not set the sign-in flag.
  - Everything binds 127.0.0.1, and Vite proxies to localhost only.

## Checked, no findings
- Hard rule 1 (card data): no finding (see above).
- Hard rule 2 (live data): no finding beyond M1, L1 and L2. Status and scans are read live on every call (`book.py:155-183`), and no price or stock is in the order result.
- Hard rule 3 (synthetic data): no finding. S0031 and the carriers are synthetic; the test key `b"o"*40` is used only in `testing.py`.
- Hard rule 4 (secrets): no finding. The token key comes from the environment only; `make dev` generates it per run without echoing it; the shopper ID is not logged (grep of log calls; telemetry holds counts only).
- Hard rule 5 (product claims): not applicable to this change.
- Hard rule 6 (tool text is data): no finding. `<order_data>` is NFKC-normalised with `<`, `>` and `&` escaped (`order_tool.py:85-91`); the server is attached because of the caller, never because of content; the prompt section labels results as data.
- Hard rule 7 (internal documents, region): no finding. Fields are allow-listed, the order's region must equal the token's region, and the region becomes the shopper's own.
- Hard rule 8 (least privilege): no finding.
- Hard rule 9 (idempotency): not applicable. No state-changing tool was added; `escalate` keeps its idempotency key and `dry_run`.
- Hard rule 10 (structured errors): no finding. `not_found` (validation), `label_created` (a valid empty result) and `tracking_unavailable` / `orders_unavailable` (transient, retryable) are distinct; the sign-in 503 is the full envelope.
- Hard rule 11 (AI disclosure): no finding. The signed-in prompt keeps every rule (`test_chk06_tw_gaps.py:41`).
- 0001, placeholder key in child processes: no finding. The diff does not touch it; `turn.py` still builds through `agent_options`.
- 0001, Makefile does not export the key: no finding (`Makefile:37-38` unchanged).
- 0001, no key in CI secrets, CI in replay: not applicable. No workflow changed.
- 0001, key file never copied into a worktree: not applicable. Nothing touches it.
- 0001, budget caps enforced and not raised: not applicable. No cap or proxy code changed.
- 0001, cassettes still scanned for secrets and PANs: no finding. The scanner is unchanged and the diff adds no cassette; `cassettes/assistant-orders/` is still to be recorded in the user's window and will be scanned by `make ci`.
- M18 (`infra/docker-compose.yml:12`): correct, binds to 127.0.0.1. As the plan notes, it takes effect only after runner-sync and the user's restart.

Read-only: I made no edits.
