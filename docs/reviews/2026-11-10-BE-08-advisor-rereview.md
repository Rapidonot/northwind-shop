# Row #20 (BE-08 + BE-02): advisor-reviewer, scoped re-review of F1–F8 at `5940383`, verbatim

Diff: `/tmp/claude-502/northwind-review-BE-08.diff` from `make review-diff REVIEW_DIFF=$TMPDIR/northwind-review-BE-08.diff`,
`base d240edc941e2d84d50f42a69ddf176c303a9f0bc`, `head 59403834158f6e8bbda2ded80ea126e32e8ee60f`. Not committed: saving it
in the branch would move the reviewed head. Its three lows are carried as open findings in the gate report.

---

findings

This is a scoped re-review of row #20 at `5940383`: whether F1 to F8 are resolved, and whether the fixes add anything new. All eight are resolved, and nothing medium or above is left. There are 3 low findings. The first is an older gap in `_settle_by_read` that I missed at `0035e0b`; the fixes didn't cause it.

**Diff:** `/tmp/claude-502/northwind-review-BE-08.diff`. Its first two lines are `base d240edc941e2d84d50f42a69ddf176c303a9f0bc` and `head 59403834158f6e8bbda2ded80ea126e32e8ee60f`, and both match the SHAs you gave. A match catches accidents, not tampering. The per-row path still comes from the one fixed command: `REVIEW_DIFF ?=` already exists at `Makefile:222`, and the Makefile is not in the diff.

**What changed since `0035e0b`.** I compared the blob hashes in the two diffs because I can't run git:
- The only `src` file that changed is `reconstruct.py` (`1fc7653` → `b05b304`).
- All 12 cassette blobs are identical in both diffs.
- `test_be08_views.py`, `test_be08_audit_log.py`, `_driver.py` and the governance docs are unchanged.

## R1 and S1 in the code at `5940383`
- **R1 holds.** An uncertain capture is never reported as absent and is never counted in the confirmed fields.
  - F1's sequence (time out before the effect, read, same-key retry that takes effect and times out) now stays `open`, for capture, void and refund.
  - A dry run can no longer settle anything.
  - The one exception is a wrong `settled` label on a refund in one case (low L1 below); the confirmed totals stay right even there.
- **S1 holds.**
  - Settlement by key, whatever the order: `reconstruct.py:243`. The reorder test now also runs `assert_reconciles`.
  - No double count: payment fields are still overwritten from `Ok` intents, never added up.
  - One comparison rule: `_reconcile.py::assert_reconciles` runs after every reconstruction that has fakes in scope, except one (L3). The strict `assert_matches_truth` is kept for the 1,000-order run (`test_be08_reconstruct.py:113-114`). The per-test assertions I spot-checked in `test_be08_reconstruct.py` and `test_be02_recovery.py` are all still there, so nothing was weakened.

## F1 to F8
- **F1, resolved.**
  - `reconstruct.py:289` reads only after `last`, the group's last doubtful event. The refund baseline is still taken before `first`, which is right for one key.
  - Failing-first: `evidence.md:213-215`. The F1 mutant is killed (`evidence.md:231`).
  - `test_a_read_after_the_last_attempt_still_settles_it` checks that a read after the last attempt still settles it.
- **F2, resolved.**
  - `reconstruct.py:232-238`: dry-run events never join a group, and a key the log could only write as type and length is grouped alone, by its action ID.
  - Failing-first: `evidence.md:216-217`. Both mutants are killed.
- **F3, resolved,** apart from the one missed call in L3.
- **F4, resolved.**
  - The third mutation run kills the mutants for N1b, N2, N3 and L6 (`evidence.md:221-233`).
  - The surviving N1 mutant is plausibly equivalent. `agents/runtime.py:159` maps only `audit_incomplete_run`, and both run-level call sites (`runtime.py:278`, `:294`) pass that code, so an added `audit_incomplete` entry would never be looked up. I did not see the mutant's text.
- **F5, resolved.** The direct-call and bound-method-alias mutants are killed by `test_nothing_under_src_but_fakes_and_views_reaches_a_money_call` (`evidence.md:229-230`).
- **F6, resolved.** `tests/governance/test_tool_register.py:216-217` uses `[^"]+` again.
- **F7, resolved.** `PROGRESS.md` row #20 and its one log line now show the recording, the spend ($0.001207, $2.587194 → $2.588401) and the reviews. Status stays `in-progress` until `/req-done`, as Q2 says. Only row #20 and one log line changed.
- **F8, resolved.** The `OrderRecord` docstring (`reconstruct.py:67-72`) says the payment fields are confirmed state only and that `payment_label` is the field to show. The PR body (`pr-body.md:21-24`) now matches the code.

## Findings (low only)

**L1. Low, item 6 (R1, "never counts it as done"). `reconstruct.py:283-305` and `_reconcile.py:8-9, 34`.**
- **What's wrong:** `_settle_by_read` judges a doubtful refund by the intent's total in a later read: `refunded_minor > before`. Say a refund under key K times out before its effect, and then a refund under another key J succeeds. A later status read then marks K `done` although K never happened.
  - This existed before the fixes, and I missed it at `0035e0b`.
  - The confirmed `refunded_minor` stays right, so only K's `settled` label is wrong.
  - The same logic covers capture and void, but there the fake allows one capture or void per intent, so a second key changes nothing.
- **Why the rule doesn't catch it:** the `_reconcile.py` docstring says a `not_done` settlement must agree with the truth, but the code only checks that `settled` is a valid value (line 34). It never compares `done` or `not_done` with the fake's record for that key.
- **Fix:**
  - When another refund on the same intent succeeded or was doubtful after `first`, leave the doubtful refund `open` rather than settling it by read.
  - Have `assert_reconciles` check every `done` and `not_done` label against the fake's own record for that key.
  - Add a two-key refund test.

**L2. Low, item 2. `services/backend/tests/backend_audit/test_be08_row_review_fixes.py:99-106`.**
- **What's wrong:** `test_a_same_key_dry_run_after_an_effect_still_leaves_it_open` doesn't exercise its case. A dry-run capture on an intent that is already captured returns `ALREADY_CAPTURED` (`fakes/payment.py:154-155`), so no dry-run `Ok` ever joins the group. The test passed on the old code too: it is not in the failing-first list (`evidence.md:213-218`). The code fix itself is covered by the before-effect test and the killed F2 mutant.
- **Fix:** assert the preview is an `Ok`, and use refund, which allows a dry-run `Ok` after a real refund.

**L3. Low, item 6 (F3). `test_be08_row_review_fixes.py:127-128`.**
- **What's wrong:** `test_calls_whose_keys_are_unreadable_are_never_one_group` has fakes in scope but doesn't call `assert_reconciles`. So "every reconstruction that has fakes" (`evidence.md:203`) has this one exception, besides the empty-record test.
- **Fix:** call `assert_reconciles(record, fakes)` there.

## Unconfirmed
- I can't run commands. The `make ci` result at `5940383` and the mutation and failing-first logs are as reported in the evidence file.

## Checked (scoped to F1 to F8 and what the fixes touched)
1. **Scope:** the fixes stay in the row's own files: `reconstruct.py`, the backend tests, the governance test, PROGRESS row #20 and the review documents. None of #10's paths is touched.
2. **Failing-first:** F1 and F2 were seen failing (5 tests). The mutants for N1b, N2, N3, L6 and the two pin routes are killed. Gap in L2.
3. **No weakened tests:** the existing assertions I spot-checked are still there, with `assert_reconciles` added. F6 restores the wider capture.
4. **Hard rules and protected paths:** no new hard-rule or protected-path change. The only `src` change is `reconstruct.py`.
5. **Spend and window:** unchanged. No cassette changed, and no recording was made.
6. **Requested changes and decisions:** F1 to F8 are resolved. Gaps in L1 and L3.
7. **Control surface:** the same 12 cassettes, with blob hashes identical to `0035e0b`. Nothing else is on the control surface.

## Control surface
Added in this diff, none of them changed since `0035e0b`:
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

Nothing else in the diff is on the control surface. The new `services/backend/tests/backend_audit/_reconcile.py` and `docs/reviews/2026-11-10-BE-08-advisor-row.md` are not on it.
