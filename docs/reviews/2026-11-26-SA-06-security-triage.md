# SA-06 phase A: triage of the security review

Report: `docs/reviews/2026-11-26-SA-06-security-review.raw.md` (diff base `5d74f0a`, head `21c2107`). No critical or
high. Triage follows CLAUDE.md's fixed rule for a non-routine row: a medium inside the row's files is fixed; a low
that is a real bug in the row's files is fixed; a low elsewhere is logged with an owner.

| Finding | Severity | Outcome | Where |
|---|---|---|---|
| M1: unbounded Care runs, temp directories and memory; cross-site trigger; shutdown | medium | **fixed**: open-handoff cap (`DESK_FULL`), Care-run cap (`care_busy`), one runtime per desk, `close()` in the lifespan, `/api/handoff` takes `application/json` only. The same check for `/api/chat` is not this row (the user, 2026-11-26): it is pre-existing and fails decision 0021's real-bug test on localhost in replay, so it goes in the PR body only, not Open items | `70a4307` |
| M2: matcher quadratic in message length | medium | **fixed**: linear per clause; the same answers on all 111 labelled phrases and on 60,000 fuzzed messages; the worst case drops from 1.2 s to under 10 ms; the timing test failed 3 of 6 on the old code | `cc1bbdf` |
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
