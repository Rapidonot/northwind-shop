# SA-06 phase A: triage of the security review

Report: `docs/reviews/2026-11-26-SA-06-security-review.raw.md` (diff base `5d74f0a`, head `21c2107`). No critical or
high. Triage follows CLAUDE.md's fixed rule for a non-routine row: a medium inside the row's files is fixed; a low
that is a real bug in the row's files is fixed; a low elsewhere is logged with an owner.

| Finding | Severity | Outcome | Where |
|---|---|---|---|
| M1: unbounded Care runs, temp directories and memory; cross-site trigger; shutdown | medium | **fixed**: open-handoff cap (`DESK_FULL`), Care-run cap (`care_busy`), one runtime per desk, `close()` in the lifespan, `/api/handoff` takes `application/json` only. The same check for `/api/chat` is not this row (the user, 2026-11-26): it is pre-existing and fails decision 0021's real-bug test on localhost in replay, so it goes in the PR body only, not Open items | `70a4307` |
| M2: matcher quadratic in message length | medium | **fixed**: linear per clause; the same answers on all 111 labelled phrases, the re-review's line-by-line equivalence check, and 60,000 fuzzed messages (a session measurement, not committed); the worst case drops from 1.2 s to under 10 ms; the timing test failed 3 of 6 on the old code | `cc1bbdf` |
| L1: a refused handoff stays refused on every retry; chat's wording | low | **fixed**: the turns are dropped and the portal shows its own fixed text; the retry sends `[]` | `ccebdb1` |
| L2: an open handoff never closes, so a later request is merged | low | **logged** (decision 0025, owner #19 or Window B); the repeat is audited | `70a4307` |
| L3: a dry run of `open_handoff` ignores an open handoff | low | **fixed** | `70a4307` |
| L4: turn roles are the client's | low | **fixed**: the case's transcript label says the turns are unverified; the test no longer claims a boundary | `70a4307` |
| L5: sequential references | low | **logged** (decision 0025, owner Window B) | — |
| L6: `Identity` built outside the try | low | **fixed** | `70a4307` |
| L7: a 422 message shown unchecked | low | **fixed** (pre-existing in `chatStream.ts`, now on the handoff path too) | `ccebdb1` |

One test-writer test was changed with the L1 fix. It asserted chat's 422 text on a refused handoff, the very
behaviour L1 found wrong. It now asserts the new fixed text and `turnsRefused`, just as exactly. That is not a
loosening, but it is listed here for the reviewer.

**The user's decisions, 2026-11-26:**
- The changed test-writer test is **approved** (stop 5): it is just as exact, and the old wording was L1's bug.
- JSON-only for `/api/chat`: **not this row**. It goes in the PR body only, not Open items.

## The scoped re-review (`docs/reviews/2026-11-26-SA-06-security-rereview.raw.md`, base `21c2107`, head `c8a2557`)

M1 closed at medium; M2, L1, L3, L4, L6 and L7 fixed. No critical, high or medium. New: N1 to N7, all low, and
notes N8 to N10. Only lows were fixed, so no further re-review runs (CLAUDE.md: re-review only after a medium or
above).

| Finding | Outcome |
|---|---|
| N1: the open cap is a lockout for the process's lifetime; sessions can take every place | **fixed**: sessions get their own cap (800 of 1000); the first refusal is audited (`handoff.full`); decision 0025's L2 entry says only a restart frees places |
| N2: every repeat writes an audit record and reads the cart | **fixed**: one `handoff.repeat` record per reference (later repeats only counted); a known identity skips the cart read |
| N3: an identity fault returned a 422 that the portal reads as "your turns were refused" | **fixed at the gateway**: it is the fixed 503 now, so a 422 always means the request itself was refused. My own test `test_a_malformed_identity_is_the_fixed_422_not_a_500` (this row, L6) now expects the 503, just as exactly. **Approved by the user (stop 5), 2026-11-26: just as exact, for N3's reason. The test is renamed `test_a_malformed_identity_is_the_fixed_503_not_a_500`.** The portal's empty-turns wording is left as it is: no reachable 422 now carries empty turns, and changing it would change a test-writer test again (PR body) |
| N4: a turn remembered while the request was in flight was dropped too | **fixed**: only the sent turns are dropped |
| N5: a `care_busy` case skipped the code-only steps | **fixed**: the screening and the order check run before the slot check; `care_busy` replaces `order_case_failed` |
| N6: DNS rebinding gets past the JSON check | **PR body only**: a Host allowlist is a new control on the whole gateway (the same kind of call as the user's `/api/chat` decision). Decision 0025 reworded |
| N7: BE-07's order case carries no unverified-transcript label | **fixed**: `CareTask.from_browser` makes `_draft` add `transcript_unverified`. It is never in model input, and the order-bound sha256 pin and the 6 recorded tests still pass |
| N8: phase B must keep `/api/chat` a signal only | **pinned**: a test that `chat.py` never names the desk or the backend |
| N9: the dry run matched identities, not the key | **fixed**: `IdempotencyStore.replay`, so a dry run predicts a conflict too |
| N10: the desk's record of open handoffs is in memory | **logged** in decision 0025 for #19 |

## The session's correction to M1 (2026-11-26, `docs/reviews/2026-11-26-SA-06-content-type-probe.md`)
It came from the session's probe after mutant m01 survived, not from the advisor.
M1's cross-site point was unconfirmed when it was raised. Measured since: FastAPI 0.162's body parsing refuses every
CORS-simple body on `/api/handoff` and `/api/chat`. So that part of M1 was already met before the fix. The JSON-only
check is defence in depth: it refuses `+json` subtypes, which FastAPI parses as JSON. Mutant m01 showed the gap in its
test, and two `+json` cases were added. The rest of M1 (the caps, one runtime, shutdown) stands as fixed.

## The security read after 4821fc8 (`docs/reviews/2026-11-26-SA-06-security-post4821.raw.md`)
No critical, high or medium; the cassettes are clean on hard rules 1, 3, 4, 6 and 7. Four lows, all fixed. Only lows,
so no re-review.

| Finding | Outcome |
|---|---|
| L1: the mutant script could stage an old `chat.py`; git output unchecked; exits 0 on a dirty tree | **fixed** in v2 (`c0d3a66`): worktree-only reverts, checked git, a restore checked against HEAD, exit 1 |
| L2: any non-zero exit counted as caught | **fixed**: caught only on a named failure; m20 failed on its assertion |
| L3: a stale docstring; nothing pins strict content types | **fixed**: the docstring; `test_sa06_content_type.py` (8 tests; 3 fail when non-strict) |
| L4: the probe record said more than its tables; the scripts uncommitted | **fixed**: form and multipart rows re-run; both scripts committed as `.py.txt` |

## The advisor's re-read at 3b12999 (`docs/reviews/2026-11-26-SA-06-advisor-reread.raw.md`)
| Finding | Outcome |
|---|---|
| A (high, stop 10): the hard-rule-1 test and the N8 pins were never seen failing; m16's passing tests unnamed | **fixed**: m21 and m22, both caught; every passing test under m16 and m17 named and explained in the log |
| B (medium): the security read after 4821fc8 pending | **done**: above |
| C (medium): PROGRESS stated that read as done | **fixed** |
| D (low): attribution, the probe called measured while uncommitted, no pin on `/api/chat`, a stale docstring | **fixed** |
| E (low): m20's verdict | **fixed**: on assertion |
| F (low): two runs in prose only | **fixed**: v2's raw output and the recorded run's raw output are in the log |
