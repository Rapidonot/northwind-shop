# Row #27 security review, second scoped re-review (raw, verbatim from security-reviewer)

Diff `/tmp/claude-502/northwind-review-27-security-fixes-2.diff`, base 30f2e54, head 2aa78b2.

## Row #27: second scoped re-review of the N1/N2 fixes (diff base 30f2e54, head 2aa78b2)

I made no edits. I re-read CLAUDE.md "Hard rules" and decision 0001 first. I treated the diff, the failing-first log and the earlier raw review as data, and none of them tries to steer the review. I could not run the tests, so the pass counts in the failing-first log are as reported, not re-run by me.

**Result: N1 is still open at medium. N2 is closed.** This was the second attempt to fix M1/N1, so CLAUDE.md stop 9 ("two failed attempts to fix the same failure") applies. Nothing is high or critical, so stop 10 does not.

## Findings

### N1-R (medium, open): the fix trusts that any break in the check's view is a sentence start the shopper can see. Two kinds of break are not.
- **Where:**
  - `/Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/output_check.py:563`: `_SENTENCE_BREAK = r"[.!?](?=\s)|\n"`.
  - `:591-595`: `cut` is set from the last such break, then `_BOUND.fullmatch(text, cut, start)`.
- **Rule:** hard rule 2. Also decision 0015's row #27 amendment: an order not bound to the claim stays blocked. It is unsafe, on a protected path (`services/assistant/**`).
- **(a) A dot after an abbreviation counts as a sentence start.**
  - With `BOTH_READ` (O000064 delivered, O000113 on hold), the model writes: `Unlike order no. O000064, your order has been confirmed.`
  - The claim match is `order has been confirmed`, with subj `order` and verb `confirmed`. `_CONDITIONAL` does not match.
  - In `order_vouched`, `low = 0` and the break is the `.` in `no. `. So `cut` falls just after it and `text[cut:start] == " o000064, your "`. `_BOUND` fullmatches that, and `named == {"O000064"}`, which is delivered, so the sentence passes.
  - The shopper reads that their other (on-hold) order is confirmed. This is the same harm as N1.
  - The same happens with `Besides Ref. O000064, your order was placed.` and `As with order nr. O000064, the order was placed.`
  - The module already knows these are not sentence ends: `_ABBREVIATION` at `:502` (`no`, `vs`, `e.g` and others) is used at `:1254-1256` to "join back over" them. `order_vouched` does not use it.
- **(b) A newline counts as a sentence start, but the portal shows it as a space.**
  - The answer is rendered as plain text in `<p data-testid="answer">` (`/Users/rapido/Projects/northwind-shop-b/apps/portal/src/Chat.tsx:150-151`). `linked()` (`:34-69`) does nothing with `\n`.
  - The portal has no CSS file at all and no `whiteSpace` style, so the default `white-space: normal` turns `\n` into a space.
  - Scenario: `Unlike\nO000064, your order has been confirmed but not yet shipped.` gives `cut` just after `\n` and binds to O000064, so it passes. The shopper sees `Unlike O000064, your order has been confirmed but not yet shipped.`
- **Fix (each one only tightens):**
  - In `order_vouched`, accept a break as the sentence start only if:
    - for a `.` break, the word before it does not match `_ABBREVIATION`. Add the order-numbering abbreviations (`nr`, `ref`, `ord`) to that check, or to a local list.
    - for a `\n` break, the text before it (trailing spaces stripped) ends in `[.!?]`, or the line after it starts with a list marker.
  - Otherwise keep looking further back. If that runs past `low`, fail closed.
  - Add these as blocked cases under `BOTH_READ`:
    - `Unlike order no. O000064, your order has been confirmed.`
    - `Besides Ref. O000064, your order was placed.`
    - `Unlike\nO000064, your order has been confirmed.`
    - `Your orders O000113 vs. O000064: your order was placed.` (this also closes N3's first case)
  - Keep `1. O000064: your order was placed` and the four new passing cases passing.
  - Making the portal show newlines (`white-space: pre-line`) would also bring the display in line with the check's view. It does not replace the checker fix for (a).

### N6 (low): the `low == 0` path assumes the view starts at a sentence start. That holds today only because of a 5-character margin.
- **Where:** `output_check.py:592-594`. The view origin is chosen in `_first_match` at `:1295-1299`.
- **Why it is safe today:**
  - If `origin` is not `_sentence_start(consumed)`, it is `_back(consumed, 2L)` or `_back(matched_to, 3L)`.
  - A claim still being checked ends after `consumed` (or after the previous `matched_to`), so its end in the view is at least 129. The widest vouchable claim is 60 view characters (`order` + `has … successfully been` + `successfully` + `confirmed`, with `_S` at 3 each). So `start` is at least 69, which is more than `L` (64), so `low > 0`, and the new fail-closed rule covers it.
  - When `origin` is `_sentence_start`, it sits at a raw `_BOUNDARY` or at `_plain_since`, both real breaks. That makes it subject to N1-R's abbreviation and newline caveat, but nothing beyond it.
- **Concrete failure, if the margin breaks:** a longer `_ADVERB_WORD`, a smaller `L`, or a shorter lookback lets a view that starts mid-sentence (`…Unlike |O000064, your order has been confirmed`) reach `cut = 0` and bind.
- **Fix:** pass `order_vouched` whether the view origin is a sentence start, and fail closed when `cut == 0` and it is not. Alternatively, add a test that pins `max claim width + (L - 2L lookback)` below 0.

### Informational: the flag-off 404's headers come in a different order from a real missing path's
- **Where:** `/Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/session.py:160`.
- The middleware sends `content-type` then `content-length`. FastAPI's `JSONResponse` sends `content-length` then `content-type` (`starlette/responses.py:76,82`).
- The status, the body, the length and the absence of `Allow` and `Set-Cookie` all match. Only a byte-level header fingerprint tells the two apart.
- **Fix if wanted:** send the headers in Starlette's order, or answer with `JSONResponse({"detail": "Not Found"}, status_code=404)`.

## N2: closed
- With the flag off, the middleware answers every method itself, before the router runs (`session.py:153-164`). So the router never produces a 405 with `Allow`, and no session is bound, so no `Set-Cookie` is sent.
- **Content-length is correct:** `{"detail":"Not Found"}` is 22 bytes.
- **HEAD is well formed:** uvicorn drops the body on HEAD and keeps the declared length (`.venv/.../uvicorn/protocols/http/httptools_impl.py:535-536`, `h11_impl.py:501`). A missing path's HEAD is treated the same way.
- SessionMiddleware is the only user middleware (`app.py:50`), so both 404s pass through the same outer layers.
- The request body is not read, the same as the router's 404.

## Answers to the specific questions
- **Fullmatch:** it is anchored at both ends, and the lead-ins are a closed list. `Except for`, `As for`, `Unlike`, `Besides`, `Like` and `Apart from` all fail when written plainly. The gap is how `cut` is chosen (N1-R), not the `_BOUND` pattern itself.
- **Markup:** the portal shows markup literally. `**` around a lead-in or the ID breaks the fullmatch, which only blocks more. A bullet (`-`, `*`, `•`) only counts at the start of the slice. `1. ` is a real break, so a numbered list item legitimately binds.
- **NFKC:** folding only makes the view match the characters the shopper sees. Fullwidth or Cyrillic lead-ins fold to the same words. The characters that fold to `.` (`。`, `․`, `﹒`, `．`) all look like a full stop, so a break there is real. No bypass found.
- **Zero-width:** these characters are dropped from the view, so they cannot create or hide a break. ` ` and `\r` count as `\s` but not as a break, so they only block more. Bidi reordering: I found no way to make a contrast word appear before the ID (unconfirmed).
- **Streaming splits:** the text before the claim is final once it arrives, and the claim is held until something follows it. `_cut` changes only unsent text, and the view is rebuilt from the text after the cut, which is what is shown. No bypass found.
- **`low == 0` when the view does not begin at a sentence start:** this cannot happen today (N6 above). What can happen is a real sentence start sitting at a false break (N1-R).

## Checked, no findings
- Hard rule 1 (card data): not applicable to this change.
- Hard rule 2 (live data): open as N1-R (medium) and N6 (low). The status is still read only from `LiveValues.orders`.
- Hard rule 3 (synthetic data): no finding. Only synthetic O000064, O000113 and O000999 are used.
- Hard rule 4 (secrets): no finding. Nothing new is logged or printed.
- Hard rule 5 (product claims): not applicable to this change.
- Hard rule 6 (tool text is data): not applicable to this change.
- Hard rule 7 (internal documents, region): not applicable to this change.
- Hard rule 8 (least privilege): not applicable to this change. No tool scoping changed.
- Hard rule 9 (idempotency): not applicable to this change.
- Hard rule 10 (structured errors): no finding. The flag-off 404 is a route answer, not a tool error.
- Hard rule 11 (AI disclosure): not applicable to this change.
- 0001, child processes get only the placeholder key: not applicable to this change.
- 0001, the Makefile does not export the key: not applicable to this change (Makefile untouched).
- 0001, no key in CI secrets, CI in replay: not applicable to this change (no workflow changed).
- 0001, the key file is never copied into a worktree: not applicable to this change.
- 0001, budget caps enforced and not raised: not applicable to this change.
- 0001, cassettes still covered by the secret/PAN scanner: not applicable to this change (no cassette or scanner change).

## Files
- /Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/output_check.py
- /Users/rapido/Projects/northwind-shop-b/services/assistant/tests/test_chk06_output_check.py
- /Users/rapido/Projects/northwind-shop-b/apps/portal/src/Chat.tsx
- /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/session.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/app.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/tests/test_chk06_signin.py
- /tmp/claude-502/northwind-review-27-security-fixes-2.diff
