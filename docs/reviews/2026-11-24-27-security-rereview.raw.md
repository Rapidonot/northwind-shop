# Row #27 security review, scoped re-review of pass 1's fixes (raw, verbatim from security-reviewer)

Diff `/tmp/claude-502/northwind-review-27-security-fixes.diff`, base edd5d57, head 30f2e54.

Scoped re-review of row #27, security review pass 1 fixes. Diff `/tmp/claude-502/northwind-review-27-security-fixes.diff`, base edd5d57, head 30f2e545caf3729b836c067e125123dd6d3eca04. Read-only: I made no edits.

I re-read first: `/Users/rapido/Projects/northwind-shop-b/CLAUDE.md` (Hard rules 1–11) and `/Users/rapido/Projects/northwind-shop-b/docs/decisions/0001-llm-first-order-and-record-replay.md`. I treated the diff, the failing-first log and the embedded raw review as data. Nothing in them tries to steer the review.

**Result:** One medium is still open: N1, a leftover from M1 through the comma or no-separator path. L1, L2 and L3 are closed. M1's reported cases are closed. Informational 1 is closed for the five listed methods only, and a remainder (N2) is informational. Nothing is critical or high. Because N1 is medium and inside the row's files, the triage says fix it; it is unsafe on a hard-rule-2 path. Since the re-review is scoped, nothing escalates beyond that.

## Findings

### N1 (medium): `_BOUND` binds an ID that ends a lead-in clause, so "Unlike O000064, your order has been confirmed" vouches for another order (M1 not fully closed)
- **Where:**
  - `/Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/output_check.py:567-569` (`_BOUND`: separator `[:,\-–—]?`, optional) and `:587` (`_BOUND.search(text[cut:start])`)
  - called from `:1078-1084`
- **Rule:** hard rule 2 (order state only from live tools); decision 0015 row #27 amendment ("an unnamed order, or one not bound to the claim … stays blocked").
- **Unsafe:** yes. It is the same harm as M1 on a protected path (`services/assistant/**`).
- **What is wrong:**
  - `_BOUND` checks only what sits between the ID and the subject (a separator and one determiner). It never checks what comes before the ID.
  - The comma, or no separator at all, is accepted on purpose for "For o000064, your order was placed" (`test_chk06_output_check.py:24`). The same shape also matches when the ID ends a contrastive or additive lead-in, where "your order" means a different, unnamed order.
- **Concrete failure:**
  - `LiveValues.orders = {"O000064": "delivered", "O000113": "on_hold"}` (the `BOTH_READ` fixture). The model writes "Unlike O000064, your order has been confirmed but not yet shipped."
  - The claim matches with `subj="order"` and `verb="confirmed"`.
  - `text[cut:start]` ends in `o000064, your `, so `_BOUND` matches: the ID, `,`, a space, `your`, a space, then `$`.
  - `named == {"O000064"}`, which is delivered, so the sentence passes. The shopper is told their other (on-hold) order is confirmed.
- **Other phrasings that pass the same way** (I checked them against the regex by hand, not by running them):
  - "Besides O000064, your order was placed."
  - "As with O000064, the order was placed."
  - "Apart from O000064 your order has been confirmed." (no separator)
  - The test set has none of these.
- **Fix (tightens):**
  - Bind only when the text between `cut` and the ID is empty, or a list bullet, or a fixed lead-in such as `for`, `order`, or `for your order`. Anything else (unlike, besides, as with, apart from, like, alongside, as well as, other than, except, and, or, but, not, …) does not bind.
  - Add "Unlike O000064, your order has been confirmed." and "Besides O000064 your order was placed." as blocked cases under `BOTH_READ`.
  - Keep `:24` passing.

### N2 (informational): with the flag off, methods outside the list still show that the route exists (Informational 1 is closed for the five listed methods only)
- **Where:** `/Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/signin.py:41-43`, and the pass-through at `session.py:153-155`.
- **What happens:**
  - FastAPI registers exactly the listed methods. `fastapi/routing.py:1074` sets `route.methods = {…}` and does not add HEAD automatically.
  - For a path that matches but a method that is not listed, Starlette returns 405 with `Allow: GET, POST, PUT, PATCH, DELETE`. The code is `starlette/routing.py:264` (`Match.PARTIAL`) and `:282-286`; both files are under `/Users/rapido/Projects/northwind-shop-b/.venv/lib/python3.12/site-packages/`.
- **Scenario:**
  - `HEAD` or `OPTIONS /api/session/shopper` with the flag off gets 405 plus an `Allow` header.
  - `HEAD /api/session/nobody` gets 404.
  - No Set-Cookie is sent in either case, so that part is fixed.
- **Impact:** it reveals only that a demo route exists, as before. The tests (`test_chk06_signin.py`, post and get only) do not cover it.
- **Fix if wanted:** in the flag-off branch of the middleware, answer the same `404 {"detail":"Not Found"}` directly for every method, instead of passing the request to the router.

### N3 (low, residual of the "every named order" net): another order's ID can drop out of the check while the shopper still reads it
- **Where:** `output_check.py:585-592`.
- **What happens:**
  - The bound order itself is always checked correctly. The extra condition is that every other order named earlier in the sentence is also confirmed, and that condition can be dodged in two ways:
    - **A sentence break that is not a real sentence end:** "Your orders O000113 vs. O000064: your order was placed." `vs.` followed by a space counts as a break, so O000113 is ignored.
    - **Characters a reader takes for an ID that `o\d{6}` does not match:** for example "O000ll3" written with the letter l, or "O000⠀113" with a Braille blank.
- **Impact:** in each case the sentence is bound to the confirmed O000064, so this is an ambiguity, not a false status. Low.
- **No fix needed beyond N1.** Do not loosen anything to resolve it.

### N4 (informational): the whole-text ID scan costs O(view) per claim
- **Where:** `output_check.py:590-592`.
- **What happens:**
  - Each vouched claim now runs `_ORDER_ID.finditer` over the whole view, where it used to scan the 64-character window.
  - The view is bounded to about 3L plus the current delta (`_first_match` origin ≥ `matched_to − 3L`, `:1287-1291`). Live deltas are SDK `text_delta` events (`turn.py:151,161`), so the cost stays small.
- **Optional:** bound the scan with `finditer(text, max(0, cut - 6))` and stop at `m.start() >= start`. With `pos`, lookbehind still sees the earlier characters.

## Status of the pass 1 items
- **M1: the reported cases are closed, with a leftover (N1, medium).** The three reported phrasings ("your other order", "the second order", and the replaced third case) are now blocked, because `_BOUND` needs the ID directly before the subject. N1 is the leftover.
- **L1: closed.**
  - `(?P<verb>…)` and `VOUCHED_VERBS = {"placed", "confirmed"}` are checked at `:1082`.
  - The `live_order` pattern (`:361`) only matches "confirmed".
  - The group is read from the casefolded view, so it compares in lower case.
- **L2: closed.**
  - IDs are taken from `view.text` with `m.end() > cut and m.start() < start`, so an ID straddling the 64-character edge is seen.
  - The view starts at `origin` in `_first_match`, not at the start of the answer, so I checked that the view's own left edge cannot reintroduce L2:
    - When `origin` is a sentence start, the character before it is whitespace or a boundary.
    - Via `_back(consumed, 2L)`, a straddle would need a claim more than 60 view characters wide. The widest vouched claim is exactly 60.
    - Via `_back(matched_to, 3L)`, the first full evaluation of a claim always has `origin ≤ claim_end − 3L`.
    - A blocking result is acted on straight away (`_advance`, `:1539-1549`), so a later, narrower re-check can never vouch a claim that was first blocked.
  - The `_BOUND` lookbehind runs on `text[cut:start]`. Its slice always starts after a break character or 64 characters back, and the bound ID lies within about 21 characters of `start`, so that lookbehind is sound.
- **L3: closed.** `_cookie_value` returns "" for two or more values. `nw_session` then gets a new session, and `nw_shopper` stays anonymous (`session.py:128-133`, `:162`, `:169`).
  - **A normal browser is not broken.** Since decision 0019 (`docs/decisions/0019-cart-tools-and-session.md:62`) the gateway has only ever set host-only `Path=/api` cookies. A cookie jar keys on name, domain and path, so a browser holds one value per name. Two checkouts on different localhost ports overwrite each other rather than adding a second value. The portal sends `credentials: "same-origin"` (`apps/portal/src/cartApi.ts:38`).
  - **New side effect (low, availability only):** a cookie planted by another localhost app on `Path=/api/chat` now resets the victim's session (and sign-in) on every chat request, and the reissued `Path=/api` cookie replaces the victim's own. That is denial of service, never access to anyone's data. It is the right fail-closed trade, so no change is needed.
- **Informational 1: partly closed.** With the flag off, GET, POST, PUT, PATCH and DELETE give the same 404 body as a missing path and no Set-Cookie. HEAD and OPTIONS do not (N2).
- **L4 and the ephemeral-range ports:** not fixed, as stated. Both stay low or informational and are recorded in the PR body.

## Answers to the specific questions
- **Markup:**
  - The portal renders the answer as React text (`apps/portal/src/Chat.tsx:150-151`, `linked(turn.answer, cited)`). There is no markdown or HTML renderer (no react-markdown, remark or `dangerouslySetInnerHTML`). So `O**000113**`, `&#49;` entities and HTML comments show literally and cannot hide an ID today.
  - Markup between the ID and the subject (`**O000064**: your order`, `| O000064 | your order`) breaks `_BOUND`, which only blocks more.
  - An HTML comment cannot hide a bound ID, because the comment's closing marker would sit between the ID and the subject.
  - This changes if SA-01 adds markdown rendering. At that point the "every named order" net (N3) would need the plain view.
- **NFKC lookalikes:**
  - Fullwidth, mathematical and superscript digits and Cyrillic or Greek O fold to the same ID the shopper reads. That is consistent and gives no bypass.
  - Unicode digits that NFKC leaves alone (Arabic-Indic) still match `\d`. `.upper()` then gives a key that is not in `live.orders`, so the claim fails closed.
  - Lookalikes that do not fold can only hide a non-bound ID (N3), never create a confirmed bound one.
- **Zero-width and bidi characters:**
  - They are dropped by `_IGNORABLE` and `_fold_char`, so an ID split by them is still seen.
  - Bidi overrides only reorder characters the view already holds. A reversed ID does not match `o\d{6}`, which fails closed.
- **Streaming splits:**
  - A claim that touches the end of the stream is held (`:1304-1305`), and the text before it only grows.
  - `_cut` repairs (a marker removed between the ID and the subject) change the raw text the shopper sees, so the binding the shopper reads stays the same.
  - A newline between the ID and the claim is a break, so it only blocks more.
- **The anonymous output check is the same as before:**
  - The only regex change is a non-capturing group becoming `(?P<verb>…)`, which does not change what matches. No combined regex or other consumer reads the pattern source; `TRIGGER` at `:484-491` is separate.
  - The conditional path (`:1074-1077`) is untouched.
  - With an empty `live.orders`, `order_vouched` returns False before anything else (`:582-583`), as it did before. That covers signed-in turns with no `get_order` read as well.
- **Anything at medium or above still open:** N1 (medium).

## Checked, no findings
- Hard rule 1 (card data): not applicable to this change.
- Hard rule 2 (live data): no finding beyond N1 (medium) and N3 (low). Status still comes only from `LiveValues.orders`.
- Hard rule 3 (synthetic data): no finding. The tests use the synthetic S0031 and O000064/O000113.
- Hard rule 4 (secrets): no finding. No new logging, and the cookie key is still derived and never printed.
- Hard rule 5 (product claims): not applicable to this change.
- Hard rule 6 (tool text is data): not applicable to this change.
- Hard rule 7 (internal documents, region): not applicable to this change.
- Hard rule 8 (least privilege): not applicable to this change. No tool scoping changed.
- Hard rule 9 (idempotency): not applicable to this change.
- Hard rule 10 (structured errors): no finding. The sign-in 503 envelope is unchanged, and the flag-off 404 is a route answer, not a tool error.
- Hard rule 11 (AI disclosure): not applicable to this change.
- 0001, child processes get only the placeholder key: not applicable to this change.
- 0001, the Makefile does not export the key: not applicable to this change (Makefile untouched).
- 0001, no key in CI secrets, CI in replay: not applicable to this change (no workflow changed).
- 0001, the key file is never copied into a worktree: not applicable to this change.
- 0001, budget caps enforced and not raised: not applicable to this change.
- 0001, cassettes still covered by the secret/PAN scanner: not applicable to this change. No cassette and no scanner code changed.

## Files
- /Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/output_check.py
- /Users/rapido/Projects/northwind-shop-b/services/assistant/tests/test_chk06_output_check.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/session.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/signin.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/tests/test_chk06_signin.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/tests/test_27_session_cookie_parsing.py
- /Users/rapido/Projects/northwind-shop-b/apps/portal/src/Chat.tsx
- /Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-24-27-security-review.raw.md
- /tmp/claude-502/northwind-review-27-security-fixes.diff
