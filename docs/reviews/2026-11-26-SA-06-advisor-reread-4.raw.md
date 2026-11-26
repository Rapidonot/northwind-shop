# SA-06: advisor-reviewer's fourth re-read at `19f192e`, verbatim

Diffs: `$TMPDIR/northwind-review-SA-06.diff` (base `81683c2`, head `19f192e`) and `$TMPDIR/sa06-ci-fix.diff`. Inputs: the
failing-first log, the plan and the list of requested changes. Saved as returned.

---

findings

Only two lows, both in the records. Nothing is open at medium or above, the fix is right, and the pin is unloosened. One part of item 1 is unconfirmed: whether the full diff overlaps the other window's paths.

**SHA lines.** `/tmp/claude-502/northwind-review-SA-06.diff` starts with `base 81683c2733a7a5ead1176d739758f981a6246082` and `head 19f192e8d1ebd2b898ef9e0e1fc3014c10622dfd`. Both match the SHAs you gave. The session supplies both the refs and the SHAs, so a match catches accidents only. It does not prove which commit I read.

**Your questions**

1. **Is the fix right, and is the pin unloosened?** Yes to both.
   - `services/backend/tests/backend_audit/test_be08_views.py` is not in the full diff's file list, so the pin is unchanged.
   - The only `AuditedOrderStore(..., allowed=CARE_READ)` outside `run_care` is the new one at `services/backend/src/northwind_backend/agents/care.py:714`, inside `read_order` (lines 708-716), in the module the pin admits.
   - `services/backend/src/northwind_backend/handoff.py:569` now calls `read_order(self._care.orders, run, found.group(), self._policy)`. Nothing in `northwind_backend` outside `agents/` builds a `CARE_READ` view or imports `AuditedOrderStore` or `call_with_recovery` any more.
   - Behaviour is unchanged: the same store, audit context and policy, and one audited `get`. The ownership check (`Ok` and `shopper_id == identity.ref`, else `None`) is the same.
   - No tool list refers to `read_order`, so no model gains anything.
   - The fix-only diff agrees with the working tree.
2. **Is the failing-first evidence adequate?** Yes. `docs/reviews/2026-11-26-SA-06-failing-first.log:206-226` has:
   - The raw lines of the failed `make ci`, with their run-log line numbers: `FAILED ...test_be08_views.py::test_every_view_built_under_src_uses_a_read_only_constant`, `1 failed, 10274 passed`, `make ci exit 2`.
   - The pin failing with only 2781f9d's `handoff.py` put back over the head (`1 failed, 25 passed`). That isolates the cause to the file the fix changed.
   - The pin passing at 7aa7787 (`26 passed`).

   The test existed before the row and was not edited, so this is a true failing-first for a fix. Two limits, neither a finding: the full run log is not committed, only an excerpt; and the session wrote the log, as it does for all of the row's evidence.
3. **Is anything open at medium or above?** Nothing I found. Every high and medium in the triage history is recorded as closed: M1, M2, the advisor's A, B and C, and finding 2. My scope was the fix, the two records commits, the control surface and the test changes, not a full re-review of the 8,600-line diff.
4. **May `make ci` run at `19f192e`?** Nothing I read blocks it: no high or critical is open, and stop 10 does not apply. The two lows below are records only, so you may fix them before or after. If you fix them before, the head moves, and `make ci` has to run at the new head.

**Findings**
- **Item 2, low, `docs/reviews/2026-11-26-SA-06-pr-body.md:45-58` and `:73-106`.** The PR body records neither the failed `make ci` at 2781f9d (BE-08's pin), nor its fix in 7aa7787, nor the advisor's third re-read and this one. `PROGRESS.md` names none of `2781f9d`, `7aa7787` or `read_order` either. The failure and its fix are recorded only in the log and the triage. Add a bullet under "Failing first" and the reviews to "Reviews", so the PR's own record matches its history.
- **Item 7, low, my own third re-read (`docs/reviews/2026-11-26-SA-06-advisor-reread-3.raw.md:44-51`).** Its control surface list was incomplete. It named only the five cassettes and said `services/gateway/pyproject.toml` was not on the surface. But `RUNNER_SYNC_PATHS` (`Makefile:199`) includes `services/common`, `uv.lock` and `**/pyproject.toml`. So `tool_scopes.py`, the gateway's `pyproject.toml` and `uv.lock` are on the control surface too. The PR body (`:136-141`) and the plan's change 3 already list them correctly. Do not rely on that earlier list. Use the one below.

**Notes (not findings)**
- No `security-reviewer` read covers 2781f9d..7aa7787. CLAUDE.md does not require one, because this fixes a CI failure, not a medium security finding. I checked the fix myself above for any new access path in this hard-rule 1/9 path and found none.
- I could not inspect `064d159` and `19f192e` commit by commit, since I have no git. In the full diff, the only code hunks under `services/` that touch this fix match the fix-only diff. The `tool-register.md` row at diff line 1829 now names `read_order`, which closes the third re-read's low.
- From the third re-read, it still holds: exporting `read_order` in `__all__` means any module can import a `CARE_READ` reader without the pin noticing, because the pin checks where a view is built, not who calls it. That is the same limit as N4, and nothing here loosens it.

**Checked**
1. Scope: checked for the fix and the records commits. Both stay in the row's files, and the plan's phases and the user's changes cover the full file list. Overlap with the other window's paths: unconfirmed. I was not given window B's current paths.
2. Failing first: checked for this failure (log :206-226, as above). The earlier items rest on the log and the mutant record, which the second re-read accepted.
3. No test weakened: checked. `test_be08_views.py` is not in the diff. `test_chk01_gateway_criteria.py:7377-7388` stays exact and only adds `/api/handoff`. `test_be07_units.py:5364,5373` only handles an optional `order_id`. The two test changes the user approved under stop 5 (L1, N3) are recorded in the triage at :24 and :37 and are just as exact. `FORBIDDEN_FRAGMENTS` gains `case`, which tightens it.
4. Hard rules and protected paths: checked for the fix. No new `CARE_READ` path, no tool change, and no `evals/` change. The row is not routine (`services/backend/**`, `chat.py`), and the plan was approved by the user.
5. Spend and window: checked. Plan phase C states the suite, the changed model input, about 15 Haiku calls under $0.20 and `RUN_BUDGET=2` before any recording. The PR body records 5 calls, $0.000590, ledger $32.671632 to $32.672222.
6. Requested changes: checked from the records.
   - Change 1 (no `make check` until CHK-07, then only alone): stated. The process itself cannot be seen in a diff.
   - Change 2 (labelled set pinned before the matcher): `680b36c` before `a3b1d04`, and the user's target is used.
   - Change 3 (control surface listed): PR body :136-141.
   - Change 4 (demo handoff recorded): PR body :64-69.
   - Your other decisions: the CHK-07 merge stop (the base moved to `81683c2`); the two approved test changes; `/api/chat` JSON-only in the PR body only (:124-126); the window C record; `make ci` alongside window B and "go on" after stop 10. All are recorded. I could not see the user's own words for the last two.
7. Control surface: checked, all listed below. They are expected and the PR body already lists them, but they need human attention at `ship`.

**Control surface (full diff, base 81683c2 to head 19f192e)**
- `cassettes/backend-care/27873befb26610aba92d134e53295a250c245a31828ef144d9244cbd14bcb1d7.json` (added)
- `cassettes/backend-care/818d91a79a424eba66ca025249ebab7632ff7b1a4fa7233e6bb317be53ccf997.json` (added)
- `cassettes/backend-care/a0ab3f95b10fd75eee2faddeae752ba4f6c0d93be8c37f03ea4280ae30f01dee.json` (added)
- `cassettes/backend-care/aabfc0250caf7d95ad76a9cfb184a4bab7105330b44679241a6c0566b7f8d5c9.json` (added)
- `cassettes/backend-care/b80829090d70437deb1762795b26a2f91b0951dbd5962c610ffbcdddcee23ba6.json` (added)
- `services/common/src/northwind_common/tool_scopes.py` (modified; in `RUNNER_SYNC_PATHS` through `services/common`)
- `services/gateway/pyproject.toml` (modified; `**/pyproject.toml` in `RUNNER_SYNC_PATHS`)
- `uv.lock` (modified; in `RUNNER_SYNC_PATHS`)
- Not on CLAUDE.md's surface but on the user's list (change 3): `docs/governance/data-flow.md`, `docs/governance/tool-register.md`

The gate report must list all five added cassettes.
