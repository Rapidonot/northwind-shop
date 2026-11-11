# Row #20 (BE-08 + BE-02): advisor-reviewer, row review at `0035e0b`, verbatim

Diff: `/tmp/claude-502/northwind-review.diff` from `make review-diff`, `base d240edc941e2d84d50f42a69ddf176c303a9f0bc`,
`head 0035e0b39dab5f73894659b3ce2da21d49fa7b27`. Saved before any fix. The fixes and their evidence are in
`docs/reviews/2026-11-10-BE-08-evidence.md`, "Row review at 0035e0b".

---

findings

Row review of row #20 (BE-08 + BE-02), branch `req/BE-08-audit`. The diff is `/tmp/claude-502/northwind-review.diff`. Its first two lines are `base d240edc941e2d84d50f42a69ddf176c303a9f0bc` and `head 0035e0b39dab5f73894659b3ce2da21d49fa7b27`, and both match the SHAs you gave. As CLAUDE.md says, that match catches accidents, not tampering.

I found no critical or high finding. There are 5 medium and 3 low. R1 and S1 hold for the cases the tests cover. Each has an untested hole in `reconstruct.py` (F1, F2), and S1's "one comparison rule" was never written as code (F3).

## R1 and S1 against the code

**R1, an uncertain capture is never reported as absent: holds for the tested cases, broken in two untested ones.**
- What holds:
  - `test_an_uncertain_capture_is_never_reported_as_absent` (1 and 3 attempts), the JSONL reload test and hang case 1 all get `uncertain`, `settled == "open"`, the amount, `captured_minor == 0` and `payment_label == "authorized (payment.capture uncertain)"`.
  - Hang cases 2 to 4 settle to `done`.
  - A timed-out read is not uncertain.
- The two counterexamples are F1 and F2.

**S1, part by part:**
- **Settlement by key, whatever the event order: holds.** `reconstruct.py:229` uses `any(_is_ok(e) for e in group)` over the `(action, idempotency_key)` group. `test_a_same_key_ok_settles_it_whatever_the_order_and_counts_once` tests it with the outcomes swapped, and hang case 3 has the `Ok` written before the late `TIMEOUT`. The dry-run hole is F2.
- **No double count: holds.** Payment fields are overwritten from the latest `Ok` intent outcome (`reconstruct.py:177-182`), never added up. Hang cases 2 to 4 and the reorder test assert `captured_minor` equals the amount exactly once.
- **One comparison rule in every reconstruction test: not met** (F3).

## Findings (most severe first)

**F1. Item 6 (R1 as decided), medium. `services/backend/src/northwind_backend/reconstruct.py:238-240, 270, 285`.**
- **What's wrong:** `_settle_by_read` is passed `first = doubtful[0]` and looks only at reads after that first doubtful event. Take this sequence on one key:
  1. A capture times out before its effect.
  2. A `payment.status` read shows nothing captured.
  3. A same-key retry takes effect but its outcome is also `TIMEOUT`.
  4. No later read.
- **Result:** the group settles as `not_done`, from the read in step 2. `payment_status` reads `authorized`, `captured_minor` is 0, and `payment_label` has no "uncertain" marker, because only `open` adds it. A capture that happened is reported as absent. Retrying after a status check is the natural pattern, and the fakes can produce it with two `inject` calls.
- `_refunded_before(event, …)` has the same "first event" anchoring.
- The PR body's claim "never reported as absent" (pr-body.md, "Any order can be rebuilt") is too strong until this is fixed.
- **Fix:** settle by reads after the last doubtful event in the group, and add a test for exactly this sequence for capture, void and refund.

**F2. Item 6 (R1/S1), medium. `reconstruct.py:224, 229`.**
- **What's wrong:** groups are keyed by `(action, idempotency_key)` and ignore `dry_run`, and `outcome_known` counts a dry-run `Ok`. The state loop skips dry runs (`reconstruct.py:167`). A dry-run fake call skips the idempotency store (`fakes/_base.py:172-173`), so it proves nothing about the real call.
- **Result:** a dry-run preview under the same key as a real call that timed out settles that call as `done`. Two outcomes follow:
  - If the real call had no effect, it is counted as done. R1 says it must never be.
  - If it did take effect, the record shows `settled="done"` but `authorized` with 0 captured and no marker, so it reads as absent.
- `test_dry_runs_never_change_the_reconstructed_order` uses separate keys (`dry-cap`), so it misses this.
- Related low: a key that fails the reference pattern is written as type and length, so `_str` returns `None`. All such keys for one action then collapse into one group, and an `Ok` for one settles the others.
- **Fix:** leave dry-run events out of the uncertain groups and out of `outcome_known`. Add a same-key dry-run test.

**F3. Item 6 (S1, "one comparison rule for every test"), medium. `services/backend/tests/backend_audit/test_be08_reconstruct.py:3-6, 60-91`.**
- **What's wrong:** the rule is only in the module docstring.
  - The one shared helper, `assert_matches_truth`, requires `record.uncertain == ()` and `record.unknown == ()`. Only the fault-free 1,000-order test uses it.
  - Every fault-path, R1 and hang test asserts a few fields by hand: `test_be08_reconstruct.py:202-399`, `test_be02_recovery.py:303-366` and `test_be08_be02_criteria.py:142-279`.
  - The plan's R1 test row says "the ground-truth comparison accepts `uncertain` only for that action", but no comparison does that. The R1 test never compares the confirmed fields (fraud, shipment, held, currency) with the fakes' truth.
- **Fix:** one helper that applies the S1 rule (confirmed fields equal the truth; each `uncertain` entry is listed with its amount, never counted, and its truth is "happened" or "not happened"; never absent when it happened), used in every reconstruction test.

**F4. Item 2, medium. `docs/reviews/2026-11-10-BE-08-evidence.md:88, 156-163`.**
- **What's wrong:** no failing-first evidence, and no mutant, for these fixes:
  - N1 (`audit_incomplete_run`);
  - N2 (`test_the_event_append_returns_is_a_copy`);
  - N3 (`test_a_context_label_cannot_be_swapped_after_it_is_checked`);
  - L6's module-attribute match in the pins (`module.AuditedPaymentPort(...)`).
- The second mutation run (36/36) came before N1 to N3 and has no L6 mutant. The other security fixes (M1, L1 to L5) do have a killed mutant each, and that counts as failing-first.
- **Fix:** run a mutant or a reverted-fix run for each of these and add the log.

**F5. Item 6 (N3, "one mutant per route"), medium. `evidence.md:52-55, 115-118`.**
- **What's wrong:** the plan (line 223) and the user's N3 decision name six routes: direct call, `partial`, `getattr` with a string, bound-method alias, widened allow-list and an import of `_driver`. The mutation logs show only four: widened view, getattr, partial and import driver. The direct-call and bound-method-alias mutants are missing.
- **Fix:** plant and kill those two mutants, and log them.

**F6. Item 3, low. `tests/governance/test_tool_register.py:215-216`.**
- **What's wrong:** this existing test was changed after it failed on the new tools. Its name capture went from `[^"]+` to `[a-z_]+`, so a tool name with a digit, an uppercase letter or a hyphen drops out of the register check. The `@tool(` count at line 218 still catches most such cases. The scan now covers all of `services/`, so it is wider in files but narrower in the names it matches. The evidence calls this "stricter, not weaker", which overstates it.
- **Fix:** keep `[^"]+` with the wider file scan.

**F7. Item 6, low. `PROGRESS.md:74` (row #20) and `:266` (log line).**
- **What's wrong:** at head `0035e0b`, both still say "Waiting on the `backend-audit` window (stop 2)" and status `in-progress`, though the cassettes were recorded in `86817e0`. The row spend ($0.001207, ledger $2.587194 to $2.588401) is not in PROGRESS. The row-and-one-line limit (instruction 4) itself is respected.
- **Fix:** update row #20 and its one log line to the recorded state.

**F8. Item 6 (plan section A), low. `reconstruct.py:242` and `test_be08_reconstruct.py:277`.**
- **What's wrong:** the plan says the payment status is "never plain `authorized`" while a capture is uncertain. `OrderRecord.payment_status` stays plain `authorized`, and the R1 test pins that. Only `payment_label` and `uncertain` carry the doubt, so a consumer reading `payment_status` and `captured_minor` sees "authorized, not captured".
- **Fix:** document that `payment_label` is the field to show, or rename the fields so the raw ones can't be mistaken for the whole state.

Fixing any of these after `make ci` moves the head, so it needs a new commit and another `make ci` before the next review.

## Unconfirmed
- I can't run commands. The `make ci` result (exit 0, the counts, the gate equal to the baseline), the ledger figures and that the window was open when you said so are as you reported them; they match the evidence file and the PR body.
- I did not re-run the code-security review of `agents/runtime.py`, `agents/orders_check.py` or `recovery.py`. That is `security-reviewer`'s job, and its three verbatim files exist.

## Checked
1. **Scope:** findings F1 to F3 (decided changes not fully met in code). The diff touches none of #10's paths: `services/assistant`, `services/rag`, `services/common`, `gateway/.../chat.py`, `Makefile` and `CLAUDE.md` are absent, and `northwind_common` is only imported. Both IDs are `partial`, with the rest listed. The `test_tool_register.py` change was not in the plan, but the evidence file states it.
2. **Failing-first:** the initial collection failure of all six modules is logged, and there are two mutation runs. Gaps in F4.
3. **No weakened tests:** three tests changed after a fix are each disclosed; the in-row changes read stricter. One narrowing (F6). I could see only the final versions of the in-row test files, not their earlier states.
4. **Hard rules and protected paths:** no protected path or other-window path is touched, and `services/backend/pyproject.toml` and `uv.lock` are unchanged. The Orders subagent's tools are status and assessments only. The cassettes hold no `sk-ant`, `x-api-key`, `Bearer`, email, `4111`, `tok_sandbox` or `corr-` (grep: 0 matches), and `metadata.user_id` is scrubbed. The code-level security read is left to `security-reviewer`.
5. **Spend and window:** stated before recording. The plan gives Haiku only, suite `backend-audit`, about $0.03–$0.05, `make -C ~/.northwind/runner llm-runner SUITE=backend-audit RUN_BUDGET=3` and the $90 cap unchanged. The evidence gives the user-stated $2.587194 before. $2.588401 − $2.587194 = $0.001207, which matches. The lower-than-estimated call count is explained.
6. **Requested changes:**
   - Met: 1, 2, 3, 5 and 7.
   - Request 4: the scope limit holds (only row #20 and one log line changed), but their content is stale (F7).
   - Request 6: Q1 to Q4, L1, L2, N2, N7, M6 and L8 are reflected. N3's mutant set is short (F5). R1 and S1 have gaps (F1 to F3, F8).
   - Request 8: not triggered, because `origin/main` is still `d240edc`.
7. **Control surface:** flagged in the plan and the PR body. The list is below.

## Control surface
Changed in this diff (all added, none modified or deleted):
- `cassettes/backend-audit/1719ac8bb41c5f67e619da1fe5ac305961416207890b00eb51b4d274ff0d16dc.json`
- `cassettes/backend-audit/24a17edd8decd3a60bedcf09246ea625e4bcef1cded354b2f41200bb0e0526b3.json`
- `cassettes/backend-audit/2a40b4649839bbc25aa03f0697b5f4c8df9f46cdeb72110752b840793e260bfd.json`
- `cassettes/backend-audit/601306376ae0d0bcf60950c3c894de1bb4da837bea96958c94b68d6af812c757.json`
- `cassettes/backend-audit/6ee9742d6d0027a3e11a39cb04a437eb8bdbfc6deb9bc8f5e4fa837793caf217.json`
- `cassettes/backend-audit/8e59fe1d2ef232353fd74cbbe082b23c57c81698646d9d5339bbd782b44b5d13.json`
- `cassettes/backend-audit/945f7393c28c96aac9fc3c61fc161e16b4f537ed11d9a5a29f30af9601b677df.json`
- `cassettes/backend-audit/978cbbf9880d807aa49f101ff946488774876579bfb0e8f298e21284c866fdc3.json`
- `cassettes/backend-audit/b2857e42104f7220ee73334642516922c206109579a11a14877112c89028843d.json`
- `cassettes/backend-audit/c87d609096f97cf6e34d5fa8bd5d9127cf75781fe2c51edfe5edd4001378dec9.json`
- `cassettes/backend-audit/cf79d9093b65fd1814bfe4df93217a5b5c7f2823f94bdc68ed74136e472e74d6.json`
- `cassettes/backend-audit/f43703933b6d442c528ed26c7de9a9d7a327260310a552e093cc4cd11ed0652a.json`

Nothing else in the diff is on the control surface. I checked against `RUNNER_SYNC_PATHS` in `/Users/rapido/Projects/northwind-shop/Makefile:174`. `tests/governance/test_tool_register.py`, `docs/governance/*`, `docs/reviews/*` and `services/backend/tests/backend_audit/__init__.py` are not on it, and there is no `conftest.py`, `pyproject.toml`, `.gitignore` or workflow change. `make ship` will ask for the PR number because cassettes changed.
