# CC-10 (row #10) gate report — STOPPED, 2026-11-10

**Where:** after `make ci` (exit 0 at `83c2acd`) and `advisor-reviewer`, whose verdict is `unconfirmed`: the shared
`make review-diff` output was overwritten by another worktree mid-review. Its provisional findings 2, 3, 5, 6 and 7
are fixed in a new commit; `make ci` has not run at that head.

**Why stopped:** two findings and the re-review need the user (evidence, "advisor-reviewer (2026-11-10)"):
1. **R00712 xfail (finding 1, medium):** the strict xfail also stops the order-confirmed and channel checks
   (`test_smoke_e2e.py` lines after the forbidden-word check) and would count a `pytest.fail` as expected. Option:
   split it into an unmarked test (everything but `assert "discount" not in answer`) and a strict-xfail test holding
   that one assertion. Stricter than now; the assertions and forbidden words unchanged; it is a test change after an
   approved one, so the user's call (stop 5).
2. **`make review-diff`'s shared path (finding 4, medium):** `Makefile:227` writes one `$TMPDIR` path for every
   worktree. Changing it is control surface (stop 8).
3. **The re-review input:** run `make review-diff` with `REVIEW_DIFF=<a path only this session uses>`, or wait for
   the Makefile fix; whether the override still counts as the fixed command is the user's call.

**Waiting on the answer:** the split (or not), then one `make ci` at the new head, `make review-diff`, the advisor's
re-review, and the full gate report. Spend: `make llm-spend` $2.587194 (row #10: +$0.204007, two windows).

**What the user must do:** answer 1–3 above.
