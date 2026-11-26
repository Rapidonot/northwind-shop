# BE-04 (row #25) security-reviewer, first pass, verbatim

Diff: base b5e2d508e9855721a234b2d2cd2d308727e3d784, head 03589a2e079113e409f144d21188c47559a9de83.

Result: no critical or high findings. 2 medium, 7 low, 2 unconfirmed.

- **M1 (medium):** a release or commit can give back a hold that another call already swept, so `held` falls below the live holds and reserve can over-hold (postgres.py:275-295, memory.py:240-243, the clock reads at postgres.py:252 and memory.py:221).
  - `_settle_in` reads this reservation's holds, including `swept`, before it locks the stock rows, and decides live or expired from a `now` read before it connected.
  - The live branch subtracts every hold's quantity and ignores `swept`. `_SWEEP` locks only hold rows, never the reservation row, so a late-clock reserve can sweep this reservation's holds while this call waits for the stock lock, and the units are given back twice.
  - Example: on_hand=2, X holding 1 (expires at E) and Y holding 1, so held=2. T2 = `commit(X)` reads now=E-1ms and blocks. T1 = `reserve(Z)` with now=E+1ms sweeps X (held=1) and takes 1 (held=2). T2 then takes the live branch: held=1, on_hand=1. Y and Z are both live on 1 unit, and `on_hand - held` = 0 hides it. A later `reserve(W)` after Y commits is granted too. The last commit breaks `CHECK (on_hand >= 0)`, or a release breaks `held >= 0`, as a `CheckViolation`; the view turns it into `unexpected_error`, a doubtful write, and the run escalates as `uncertain_write` with money authorized.
  - Medium, not high: the CHECK stops physical oversell, and commit-before-capture stops a capture. The window needs a commit or release straddling the expiry, or clocks that differ between processes.
  - The fake's `commit` live branch has the same bug: its availability is computed from records, so it doesn't over-hold, but `row.held` drifts and its assertion can fire.
  - Fix: after `_lock`, read this key's holds again `FOR UPDATE`. If any is swept, treat the reservation as expired: re-take for a commit, give nothing back for a release. Only give back unswept holds, and skip `hold.swept` in the fake's commit. Add a two-clock race test.
- **M2 (medium):** when capture fails after the commit, the stock is gone and the escalation context doesn't say so (orders_place.py:688-691, 853-858, 864-896).
  - `_release_stock` returns early when `p.committed`, and there is no uncommit. Plan revision 3 says "the escalation context reports both", but `handoff()` has no inventory field, and reserve and commit are not `RunState` steps.
  - Staff then cancel or refund without knowing to restore stock; only the audit's `inventory.commit` outcome records it.
  - Fix: a code-built `"stock"` field in `handoff()`, a recommended action or open item for "money not captured, stock committed", and nothing in the model's tool results.
- **L1:** a repeated reserve returns `Ok` for a released, expired or committed reservation, and placement treats any `Ok` as a live hold (orders_place.py:828-830; the repeat paths in postgres.py and memory.py). A rerun more than 15 minutes later authorizes money for stock it doesn't hold; commit-before-capture catches it, but an authorization is left to settle. Fix: treat `Ok` with a status other than `held` (or `committed`) as no hold.
- **L2:** releasing an expired hold leaves its status `held`, so a later commit can take the units again (postgres.py:283-301; memory.py:206-210). Not reachable from placement today; it matters for #19's `cancel_order`. Fix: set `released` in the expired case too.
- **L3:** an inventory key conflict is reported as `confirmation_problem` (model.py:64 against orders_place.py:252-254, 968-969). Fix: its own code, such as `reservation_conflict`.
- **L4:** `inventory_unavailable` says "Nothing was changed", which isn't guaranteed: an `OperationalError` at COMMIT can follow a write that took effect, and it isn't in `DOUBTFUL` (model.py:68; postgres.py:144-176, 252-257). Same-key idempotency limits the impact. Fix: reword it, or map an error after the first write to a doubtful code.
- **L5:** no `connect_timeout`, `lock_timeout` or `statement_timeout` (postgres.py). `with_deadline` waits for the worker thread past the deadline (PAY-08's known limit), so a lock wait or an unreachable server stalls the run. Fix: `SET LOCAL lock_timeout` and `statement_timeout` per transaction.
- **L6:** M12 in `validate_order` checks below-1 and duplicates, but not the int type, `MAX_QUANTITY` or `MAX_LINES`, which `check_request` does (orders_place.py:489-494). Such an order passes validation and reserve refuses it after the claim as `invalid_lines`, which goes to the model instead of being escalated by code. Unlikely, since order-store data is code-built. Fix: the same checks in M12.
- **L7 (informational):** the stored fingerprint is an unsalted sha256 over `order_id`, `region`, the postal code and the lines, so a synthetic postal code like `US-1234` can be brute-forced from it. It is stored in the database only, and the data is synthetic. Note it before real data; an HMAC, or leaving the postal code out, would fix it.
- **U1:** `_AVAILABLE` uses `%(variants)s` twice (`::text[] IS NULL` and in `ANY(...)`) with `None` passed from `_substitutes`. It should type-check as one text[] parameter but hasn't run against Postgres.
- **U2:** none of the Postgres race or contract tests have run, so every Postgres-side "clean" item is from reading the SQL only.

**Checked, no findings:**
- Lock ordering and deadlocks: the reservation key or row first, then stock rows in one `ORDER BY variant_id, node FOR UPDATE` statement, and hold rows written only under their stock row's lock. `UPDATE … FROM inventory_reservations` locks no reservation rows, and no foreign-key columns change. No cycle found.
- Idempotency insert: `ON CONFLICT DO NOTHING` waits for an in-flight same-key insert, a rolled-back attempt leaves nothing under the key, and a mismatched fingerprint gives `idempotency_conflict`.
- The conditional `_TAKE` and the CHECKs hold the invariant, except for M1.
- SQL injection: every query in src is parameterized; the only f-string SQL is in tests, with uuid-hex schema names.
- Hard rules 1, 2, 3, 4, 6, 7, 8, 9 (with M2) and 10 (with L4): clean. Hard rules 5 and 11, decision 0001 and the control surface: not applicable or clean.
- `uv.lock` gains only the backend's `psycopg[binary]` lines.
