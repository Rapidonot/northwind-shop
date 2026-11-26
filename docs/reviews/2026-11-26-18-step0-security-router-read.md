# Row #18 step 0: security-reviewer, scoped read of the router test (verbatim)

Diff: `/tmp/claude-502/northwind-review-18-router.diff` (`services/rag/tests/test_rag07_tw.py`, working tree against `3666dd8`),
asked for by advisor R1. The report follows unchanged; the triage is at the end of
`docs/reviews/2026-11-26-18-step0-triage.md`.

---

Scoped re-review, row #18 step 0: services/rag/tests/test_rag07_tw.py::test_huge_input_is_fast (L3). Working tree compared with 3666dd8, using /tmp/claude-502/northwind-review-18-router.diff and the "advisor R1" block in /Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-26-18-step0-failing-first.log (lines 138-157).

**Answer.** The current version is at least as strong as the one I closed L3 on. Nothing in it weakens L3, and stride-8 survival is acceptable as the stated limit of a ratio test. L3 stays closed.

- **Same limits as the L3 fix.** Line 203 is `assert slow <= 6 * max(quick, 0.02)`, with the same 6x and the same 0.02 s floor. Line 204 is `assert slow < 30.0`, the same 30 s CPU backstop. Both sizes are unchanged (50,000 and 200,000), all six units are unchanged, and the `found` / `found <= ALL_INDEXES` checks still run on every reading (lines 198-199).
- **Median of 3 does not loosen anything.** Lines 192-200 take the middle of three readings. A real quadratic gives a stable ratio of about 16, so the median cannot hide one. The median also drops a one-off slow reading. In the old version, a slow first call at 50K (warm-up or cache fill) made `quick` larger and loosened the 6x bound. The median removes that case, so the new version is slightly stronger on the `quick` side.
- **Stride 8 is the limit I already stated.** When I closed L3, I noted that a C-speed quadratic part smaller than about a fifth of the linear cost at 50K still passes, as any ratio test allows. The stride-8 `str.find` mutant is that case: the quadratic term is cut 8x and runs at C speed. The fix for it is not a looser or different ratio. If it ever matters, the fix is a hard input-length cap in the router, which is a design matter outside this row.
- **Evidence (log lines 139-145, 150-156).** 3 quiet runs and 3 runs under 10 busy-loop processes all pass. The stride-1 mutant fails 5 of 6 units at 6x.

**Findings**

1. Low, unconfirmed, services/rag/tests/test_rag07_tw.py:182 (unit `"ignore "`). The stride-1 mutant fails 5 of 6 units, so the `"ignore "` unit passed under a stride-1 quadratic mutant. One possible cause is that the router decides that input early, before the mutated pass reaches the full text. I did not read the router or the mutant, so I cannot confirm why. The test is still stronger than the version L3 closed on, since five units fail the mutant. The gap: if a future quadratic regression hits only the path `"ignore "` takes, this unit will not catch it. Suggested follow-up: record in the log why `"ignore "` survives. If it is an early exit, either accept it as a property of that unit or add a unit that sends the injection-marker path through the whole input. Do not drop or relax the unit.

No critical, high or medium findings.

**Checked, no findings**
- L3 (6x ratio, 30 s CPU backstop, 0.02 s floor, both sizes, all units): kept with the same values, with the median of 3 added.
- No instruction in the diff, docstring or log tries to steer the review.
- Hard rules 1-11: not applicable. This is a change to one test's timing method and does not touch cards, prices or stock, data, secrets, sources, scoping, idempotency, error envelopes or AI disclosure.
- Decision 0001 items (placeholder key only in child processes, Makefile not exporting the key, no key in CI secrets and CI in replay, key file not copied into a worktree, budget caps unchanged, cassettes still scanned): not applicable. The change makes no LLM call and touches no key, cap, Makefile, CI or cassette path.
