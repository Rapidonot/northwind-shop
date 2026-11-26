# BE-04 (row #25) security-reviewer, scoped re-review, verbatim

Diff: base 03589a2e079113e409f144d21188c47559a9de83, head 6643236358015ad084b382263f898723950d811c.

Result: no critical, high or medium findings. 3 new lows, and 2 unconfirmed items still open. The Postgres side was checked by reading only (U2 still applies).

- **N1 (low):** a commit that fails with `hold_expired` on the two-clock path leaves the reservation stored as `held`, unexpired and holding nothing, so a rerun passes the L1 check and authorizes money (postgres.py:311-314, memory.py:231-235; hard rule 9).
  - On the `any(swept)` branch the code gives back the unswept holds and sets `swept = true` on every hold, then returns `EXPIRED` inside `with conn.transaction()`, which commits those writes. The reservation row keeps `status = 'held'` and an `expires_at` later than the early clock, so `reported()` (model.py:273) says `HELD` until `expires_at`. The fake does the same.
  - A rerun's same-key `reserve` returns `Ok(HELD)`, the L1 check at orders_place.py:857 accepts it, and money is authorized against a reservation that holds nothing. Commit-before-capture then refuses, so nothing is captured, but an authorization is left to settle. It needs differing clocks plus a rerun inside the hold window, hence low.
  - Fix: on that `EXPIRED` return, set `expires_at = LEAST(expires_at, now)` or store a terminal status, in the fake too, with a contract test.
- **N2 (low):** the new `stock` field (M2) is worked out from in-process flags rather than read from the store, so on failed or doubtful paths it can state something false (orders_place.py:823-834, with 803, 818 and 884-887).
  - It says `"released"` whenever `p.committed` is false, but `handoff()` is built (line 803) before `_release_stock()` runs (line 818), and that release's result is never checked.
  - (a) A commit returning `inventory_unavailable` after a COMMIT that took effect (L4 admits this; the code is not in `DOUBTFUL`) leaves `p.committed` false: the context says `"released", restock_needed: false`, the later release returns `Ok(committed)` and changes nothing, and staff are told no restock is needed although the units left stock and no money was captured. That is the M2 situation again.
  - (b) A doubtful capture leaves `p.captured` None, so `restock_needed: true` although the capture may have gone through.
  - (c) A failed release reports `"released"` for a hold that is still live; it expires on its own, so this one is minor.
  - Fix: build it from observed facts, with `"commit_uncertain"` or `"unknown"` when a commit failed other than by a clean business refusal or the capture is doubtful, and `"released"` only after the release returns `Ok`.
- **N3 (low):** the new codes map to an escalation reason with the wrong recommended action, and the L1 path drops the stock field (orders_place.py:860-867, 1012-1013; escalation.py:29).
  - (a) `reservation_conflict` (no longer in `_CONFIRMATION`) and the L1 codes `hold_expired`, `hold_released` and `hold_committed` fall through to `port_failure`, whose action is "retry the order later". A retry can never succeed: the inventory key comes from the order, and a later run makes no write on an escalated order.
  - (b) On the L1 path `p.reservation` stays None, so `stock` is None even when the stored reservation is `committed`: the M2 case on a rerun after a crash between commit and capture. Whether a same-run rerun can reach `_reserve` with a committed hold is unconfirmed.
  - Fix: reason codes with their own `RECOMMENDED` entries, and on the L1 path a `stock` field built from the returned reservation.
- Stale text, not a finding: memory.py:7's docstring still says `idempotency_conflict`.

**Verdicts on the first-pass items:**
- **M1: fixed** (by reading; the Postgres tests have not run). Lock order: the reservation row, the stock rows in one sorted `FOR UPDATE`, then this key's hold rows `FOR UPDATE`. A sweep can't interleave, because every hold-row lock is taken while holding that hold's stock-row lock. Under READ COMMITTED the re-read takes a fresh snapshot. No deadlock cycle and no double give-back on any branch. Residual: N1.
- **M2: partly fixed.** The field is code-built and stays out of tool results (the test asserts `escalate_order` returns only `{status, reason}`), and the open item is logged for #19. It can be wrong on failed or doubtful paths (N2) and is None on the L1 path (N3). The `CONTEXT_KEYS` pin change is correctly held as stop 5.
- **L1: fixed**, but N1 reopens it on the two-clock path, and N3 covers the reason mapping.
- **L2: fixed**, in Postgres (postgres.py:304-310) and the fake (memory.py:207-211), with tests updated.
- **L3: fixed:** the code is `reservation_conflict`; see N3 for its reason.
- **L4: fixed:** the message no longer says "Nothing was changed". The doubtful-code alternative wasn't taken, and N2 shows a consequence.
- **L5: partly fixed.** `lock_timeout` and `statement_timeout` are set in every transaction, `available` included. `LockNotAvailable`, `QueryCanceled` and `DeadlockDetected` subclass `OperationalError`, so each becomes `inventory_unavailable` after a rollback. `connect_timeout` wasn't added: no production `connect` factory exists in src yet, so whoever wires one must pass it, as gateway/app.py:73 and mcp-rag/app.py:26 do. Two `SET LOCAL` statements in one `execute` work under psycopg 3's simple query protocol (by reading; not yet run).
- **L6: fixed:** the int type, `MAX_LINES`, `MAX_QUANTITY` and the bool exclusion, bool first, with tests.
- **L7: not fixed;** logged as an informational open item, which is acceptable for synthetic data.
- **U1: still unconfirmed.** **U2: still applies:** every Postgres item is from reading the SQL only, and the two-clock tests run their steps in sequence, so the real lock interleaving needs the race suite on Postgres.

**Checked, no findings:** hard rules 1, 2, 3, 4, 6 and 8 are clean; 9 and 10 except N1–N3 (all low); 5, 7 and 11, decision 0001 and the control surface are not applicable. SQL injection: the new statements are parameterized and `_TIMEOUTS` is a constant.
