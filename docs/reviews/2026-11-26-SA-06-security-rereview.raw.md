# SA-06: security-reviewer's scoped re-review of the phase A fixes, verbatim

Diff: `$TMPDIR/northwind-sec-SA-06-rereview.diff`, base `21c2107`, head `c8a2557`. Saved as returned (the user's
rule: pass the raw report to advisor-reviewer).

---

## Scoped re-review: row #23 (SA-06), security review fixes

I reviewed `/tmp/claude-502/northwind-sec-SA-06-rereview.diff`, which runs from base `21c2107fab80e5fd91015b2aa6d52656981d4abf` to head `c8a25578c15a5da6ae2699f7362292c177d4fc82`. I read these files in full at head:
- `services/backend/src/northwind_backend/handoff.py`
- `services/backend/src/northwind_backend/fakes/handoffs.py`
- `services/gateway/src/northwind_gateway/handoff.py`, `app.py`, `chat.py`
- `services/gateway/src/northwind_gateway/handoff_trigger.py`, lines 200 to the end
- `apps/portal/src/Chat.tsx` and `chatStream.ts`

I also read the relevant parts of `recovery.py`, `agents/runtime.py`, `idempotency.py`, `fakes/_base.py` and `cases.py`. I ran nothing; I have no Bash.

There are no critical, high or medium findings. There are 7 new lows (N1 to N7) and 3 notes (N8 to N10). Nothing in the diff tries to steer the review: "my card is 4111 1111" and the other strings are test inputs.

### Verdict per finding

**M1: partly fixed. The medium is closed; one item of the original fix list is still open.**
- **Temp directories:** fixed. One `agent_runtime()` per desk (`gateway/handoff.py:118-123`).
- **Concurrent Care runs:** fixed.
  - The counter is safe under asyncio. `busy` is checked and `_active` incremented with no `await` in between (`backend/handoff.py:541-543`), and the `finally` decrements only when it incremented.
  - A task cancelled before its first step never touches the counter. A cancel during a run raises `CancelledError`, which the `except Exception` does not catch, and `runtime.py:259-276` re-raises it. So `close()`'s `gather` ends.
- **Open handoffs:** capped at 1000. `_opened` is checked before the cap (`backend/handoff.py:476-482`), so **DESK_FULL never locks out a known identity**. The test covers this. It does lock out new ones permanently (N1).
- **Audit records per process:** not done, and repeats now reach it (N2).
- **Cross-site requests:** fixed for `/api/handoff`.
  - The check runs in the handler after the dependencies. Those dependencies only build objects, so nothing reaches the desk first.
  - `text/plain`, form and multipart bodies fail body validation and get the fixed 422. A request with no Content-Type, which FastAPI would parse as JSON, is stopped by `_json_request` (`gateway/handoff.py:169`).
  - A cross-site page can send JSON only after a preflight, and no CORS policy exists. In `no-cors` mode the browser silently drops a JSON Content-Type.
  - Same-origin attacks via DNS rebinding are still open (N6, unconfirmed).
- **Through `/api/chat` → `handoff` event:** no path to the desk today. `chat.py` emits no `handoff` event and does not import the desk. The portal's response to the event is its own JSON POST from its own origin. This holds only if phase B keeps the event a signal (N8).
- **Shutdown:** fixed.
  - `close()` waits up to 5 seconds, cancels the rest and gathers them.
  - Uvicorn drains requests before the lifespan shutdown, so no new tasks arrive during the wait.
  - The lifespan checks `handoff_desk.cache_info().currsize` (`app.py:49-50`). A desk that was never built is not built at shutdown, so no `mkdtemp` and no failure there.
  - A desk whose construction raised is not cached, and the check skips it.
- **What a `care_busy` case contains:** identity, trigger, `root_cause=shopper_requested`, live cart amounts or `no_amounts`, `NO_SUMMARY`, and the flags `summary_missing` and `care_busy`.
  - It holds no transcript and no raw turns. `HandoffCase` has no transcript field, so it has no hard rule 1 exposure.
  - It is less useful than it needs to be (N5).

**M2: fixed.** I checked the equivalence claim line by line, and it holds:
- `_spans` is `_find` as a dict, in the same ascending order and with the same longest match per position.
- **`blocked`:** the old `_blocked(tokens, upto)` was true when a negation sat at some p < upto, or a blocker match ended at i+len(b) ≤ upto. A match inside `tokens[:upto]` is the same as a match in the full token list that ends by `upto`. So `upto >= min(ends)` is the same test. The `("no", "need")` check was already implied by "no" being in `_NEGATIONS`.
- **`noun_after`:** `skipped` always equalled `i - start`. The old code used `_find(tokens[i:])[:1]` only when the first match sat at s == 0, which is the same as `i in nouns`, with the same end.
- **`fillers_after[end]`** is `all(t in _TERSE_FILLERS for t in tokens[end:])`, and an empty rest is True in both.
- **The other methods:** `take over` / `join … chat` by `_at(tokens, end, …)`, `terse`, `names_person` and `asks_for_person` are the same.
- The only difference is that all `_Clause` objects are now built eagerly. That changes the cost, not the answer.
- **Linear time:** `_spans` and the blocker scan are O(n · |seqs|). `noun_after` is bounded by `MAX_FILLERS`. The regexes `_CLAUSE` and `_TOKEN` are linear. NFKC can expand the text by a bounded factor. A string of 1,000 one-token clauses stays linear.

**L1: fixed (the retry succeeds and the text is the portal's own). Three lows come with it (N3, N4, N7).**
- Dropping turns can never send card data. It only removes turns, and the later turns are checked again by chat and by the gateway's per-turn and joined checks.
- It can lose turns the shopper still needs:
  - all turns are dropped, not only the offending ones;
  - a turn added while the request is in flight is dropped (N4);
  - a 422 that is not about the turns also drops them (N3).

**L3: fixed for every reachable path.** The region comes from the environment or the fixed demo shopper, so an identity cannot change region within a process. The fake still predicts by matching the identity, not by the key (N9).

**L4: fixed for the handoff case.** `render()` (`backend/handoff.py:312`) and `to_dict()` carry `TRANSCRIPT_LABEL`, and the test docstring no longer claims a boundary. One residual: BE-07's order case is built from the same transcript and does not carry the label (N7).

**L6: fixed.** `Identity` and `session_ref` are now inside the `try`, and `UnicodeEncodeError` is a `ValueError`. But mapping this server-side failure to the 422 interacts badly with L1 (N3).

**L7: fixed.** In `refusal()`, `typeof body.message === "string"` (`chatStream.ts:96`). The handoff path no longer shows the body at all. Adjacent and pre-existing, out of scope: `toEvent` still casts `source_ids` and `text` unchecked (`chatStream.ts:52, 58`). The gateway is trusted, so I am not raising it.

### New findings (all low)

**N1, low: DESK_FULL is a lockout for the process's lifetime. Anonymous sessions can use up all 1000 places, which locks out signed-in shoppers too.** Files: `backend/handoff.py:481-482`, `gateway/handoff.py:198-200`.
- `_opened` never shrinks (L2), so the cap counts every handoff ever opened.
- Failure: a local script sends 1000 POSTs with no cookie and JSON bodies; each gets a fresh session. From then on, any new identity, including a demo shopper who has not asked yet, gets a 503 that says "Please try again", with `isRetryable` true. A retry can never succeed until the process restarts.
- Reach is local only: uvicorn binds 127.0.0.1 by default (`Makefile:110`), or through DNS rebinding (N6).
- Fix, without loosening the cap:
  - give `kind == "session"` identities their own cap below `MAX_OPEN_HANDOFFS`, so signed-in shoppers keep headroom;
  - audit the DESK_FULL refusal;
  - add "only a restart resets this until L2's close lands" to decision 0025's L2 entry.

**N2, low: every repeat writes an audit record and does a live cart read, without limit.** Files: `backend/handoff.py:477-480, 496-500`, `gateway/handoff.py:189`.
- `_note_repeat` appends to the in-memory sink, which lives for the whole process, on every repeat.
- The handler calls `_cart()`, an MCP call to mcp-catalog, before `desk.request`.
- Failure: one cookie in a POST loop grows gateway memory linearly and puts one mcp-catalog read on each request.
- This is the "cap audit records per process" item of M1, and it is now reachable.
- Fix: write one `handoff.repeat` record per reference and keep a per-reference repeat count, bounded by `MAX_OPEN_HANDOFFS`, in memory or on the next record. Also check `_opened` before the cart read, for example with a cheap `desk.known(identity)`. This loosens no existing control: before this diff, repeats were not audited at all.

**N3, low: L6 sends a server-side fault to the 422 that L1 reads as "your turns were refused".** Files: `gateway/handoff.py:178-192`, `chatStream.ts:165-166`, `Chat.tsx:157`.
- A malformed identity (unreachable today) returns 422 "validation" with "That message could not be accepted."
- The portal then drops all turns and tells the shopper to try again without them. The retry with `turns: []` gets the same 422, now with `turnsRefused: false` but still the same message, "Part of this conversation can't be passed on…", which is wrong for an empty request. `Handoff.tw.test.tsx` asserts exactly that message.
- Hard rule 10: "validation" claims the caller's input was bad.
- Fix:
  - return an identity fault as 503 transient `UNAVAILABLE`;
  - keep the 422 for body and turn refusals;
  - in the portal, show `HANDOFF_TURNS_REFUSED` and drop turns only when `turns.length > 0`, and use `HANDOFF_FAILED` otherwise.

**N4, low: a turn remembered while the handoff request is in flight is dropped too.** File: `Chat.tsx:155-157`.
- "Send" stays enabled while the handoff is `requesting`, and `remember()` swaps in a new array.
- Failure: the shopper sends a message during the request. The 422 arrives and sets `history.current = []`, which also removes the new turn, although it was never part of the refused request.
- Fix: `const sent = history.current;` before the call, then `history.current = history.current.filter((t) => !sent.includes(t))` on refusal.

**N5, low: a `care_busy` case also skips steps that are code only, so the case is less useful than it needs to be.** File: `backend/handoff.py:545-547`.
- `_CareBusy` is raised before `CareTask(...)`, so `screened = ()`. As a result:
  - the intent is always `other`, and the action is "read the summary" while the summary is missing;
  - a signed-in shopper's own order is never linked, although `_owned_order` is a code check plus a store read and needs no Care slot.
- The test asserts no intent.
- Fix: compute `CareTask(order_id, request.turns).transcript`, and run `_owned_order`, before the slot check. Put only `run_care`/`run_summary` behind the slot. Do not add `order_case_failed` when the cause is `care_busy`.

**N6, low, unconfirmed (not run), pre-existing class: DNS rebinding gets past the JSON check.** The gateway has no Host allowlist.
- Failure: a page rebound to 127.0.0.1 is same-origin, so it can POST JSON with no preflight. It can then open handoffs up to the caps and trigger N1.
- Decision 0025 says "a cross-site page cannot start Care runs without a preflight". That claim is true only for pages on other origins.
- Fix: a `TrustedHostMiddleware` allowlist (`localhost`, `127.0.0.1`) on the gateway. That is a new control, so it is the user's decision, as with the Origin question. Reword the decision 0025 line.

**N7, low, residual of L4: BE-07's order case carries no unverified-transcript label.** Files: `backend/handoff.py:551-560`, `cases.py:23-26`.
- When the handoff has an owned order, `run_care` files a `Case` in the case queue. Its summary comes from the browser-supplied turns, and its label says only "from an untrusted transcript".
- Failure: a member of staff reads that case in the case queue. It summarises a forged assistant turn ("the assistant confirmed a full refund"), and nothing says the roles are unverified.
- Fix: when `CareTask` comes from a handoff, put `TRANSCRIPT_LABEL`, or a flag, on the BE-07 case as well. The owner is this row or BE-07.

### Notes (no severity)

**N8, phase B:** the JSON check protects the desk only while `/api/chat` merely emits the `handoff` event. If phase B opens the handoff on the server inside chat, a cross-site simple POST with no Content-Type to `/api/chat` reaches the desk. `/api/chat` has no JSON check, and FastAPI parses a body with no Content-Type as JSON. Pin this with a test that `northwind_gateway.chat` never references `handoff_desk`, or add the JSON check to `/api/chat` (the user's decision).

**N9:** in `fakes/handoffs.py:76-81` the dry run matches `opened == identity`. If the same key has a different fingerprint, the real call returns `IDEMPOTENCY_CONFLICT` (`idempotency.py:54-55`), but the dry run predicts a new reference. Look up the key in the idempotency store instead.

**N10, for #19:** the desk's `_opened` is process memory, while the queue's idempotency would be persistent. After a restart, a repeat would start a second build for an existing reference. `mcp-cases` should be the source of truth for "already open".

### L2 and L5 logged instead of fixed
Both are acceptable at low.
- **L2:** the fix needs a close or resolve operation with a staff caller, and no such caller exists in this row. The repeat is now audited. Add the N1 interaction (the lifetime cap) and the L1 interaction (dropped context can never be sent again) to the decision 0025 entry.
- **L5:** harmless until `HandoffStatus.watch` is real, and the note correctly requires the server to check the caller's session or identity.

Both are logged in `docs/decisions/0025-human-handoff.md`. Neither appears in `PROGRESS.md`; I found no match there.

### Checked, no findings
- Hard rule 1 (card data): checked.
  - The L1 drop only removes turns.
  - `care_busy` cases hold no turns.
  - The new log lines carry only fixed text and counts.
  - `DESK_FULL` and `HANDOFF_TURNS_REFUSED` are fixed text.
- Hard rule 2: not applicable to this change. The cart amounts path is unchanged.
- Hard rule 3: checked. The test inputs are synthetic and partial.
- Hard rule 4: checked. `signing_key()` is unchanged, and nothing new is logged.
- Hard rule 5: not applicable to this change.
- Hard rule 6: checked. L4's label is added; N7 is the residual.
- Hard rule 7: checked.
  - `care_busy` skips `_owned_order`, so it widens nothing.
  - A repeat returns only the caller's own reference.
  - L5 is logged.
- Hard rule 8: checked.
  - No tool-scope change.
  - The shared runtime follows chat's existing pattern.
- Hard rule 9: checked. Keys are unchanged; the desk's dry run for a known identity writes nothing (N9 aside).
- Hard rule 10: checked. New errors use the four-key envelope with fixed text; N3 is the one category issue.
- Hard rule 11: checked.
  - `AiDisclosure` is untouched.
  - The new tests assert that it is shown.
  - No new text claims a person has joined.
- Decision 0001, child processes get only the placeholder key: checked. The single runtime still comes from `northwind_common.llm.agent_runtime()`.
- Decision 0001, Makefile does not export the key: not applicable to this change (Makefile not in the diff).
- Decision 0001, no key in CI secrets and CI runs in replay: not applicable to this change (no `.github/` change).
- Decision 0001, key file never copied into a worktree: not applicable to this change.
- Decision 0001, budget caps enforced and not raised: checked. No cap, ledger or proxy code is touched. `MAX_CARE_RUNS` and `MAX_OPEN_HANDOFFS` are new limits, not raised ones.
- Decision 0001, cassettes still covered by the scanner: not applicable to this change (no cassettes in the diff).
