# BE-07 advisor-reviewer scoped re-read of 40632d9..39aec00 (verbatim)

`advisor-reviewer` on the scoped diff (`base 40632d9`, `head 39aec00`) and `make review-diff` (`base 6e46b8a…`,
`head 39aec00…`), 2026-11-24, after the user's merge of `origin/main` `6e46b8a` as `9f44b28`.

---

findings

There are two low findings, both about wording in the records. There is no medium, high or critical. The test fix, the merge resolution and the evidence hold up.

SHA lines: the scoped diff `/tmp/claude-502/northwind-review-BE-07-scoped.diff` reads `base 40632d9 …` and `head 39aec00f3e520d18b33c61610999b5cfa9fb259f`. The `make review-diff` file `/tmp/claude-502/northwind-review-BE-07.diff` reads `base 6e46b8a3204fc8e26cd60ba006b024664c193078` and `head 39aec00f3e520d18b33c61610999b5cfa9fb259f`. Both match the SHAs you gave. As always, a match catches accidents, not a forged ref.

## Findings

1. **Low (checklist 1, record accuracy).** `docs/reviews/2026-11-24-27-plan-draft.md:3` says "**Draft, uncommitted.** Written on `req/BE-07-care` while window A ships #17". Both halves are now untrue: `39aec00` commits the file, and #17 has shipped (PR #51, merged as `6e46b8a`). The BE-07 records describe it correctly: plan "After the merge" says it is row #27's `/req-start` input and not part of this row, and the PR body says "Not part of BE-07". Only the draft's own header is stale. Suggested wording: "Draft, committed on `req/BE-07-care` only so the tree is clean; row #27's `/req-start` input." I did not review its design.
2. **Low (question (a), a BE-07 record made stale by the merge).** `docs/reviews/2026-11-24-BE-07-pr-body.md:3` says "base `origin/main` `7296c04`". After the merge, the PR's base is `6e46b8a`, and `make review-diff` reads against that. The PROGRESS row's "from `7296c04`" is still true, because it is where the branch started. The PR body's "base" is not. Suggested wording: "from `7296c04`, with `origin/main` `6e46b8a` merged as `9f44b28`."

Neither finding needs a code change, and neither blocks `make ci`. Both are docs-only. If you fix them before `make ci`, the head moves, so `make ci` must run at the new head. Per CLAUDE.md, a narrow re-read of that docs commit comes first.

## Answers to (a) to (e)

**(a) Merge resolution.** These are fine.
- **CC-16:** the merged line is main's line byte for byte, with BE-07's sentence ("2026-11-24 (BE-07, #26): implement → …") added once at the end. Nothing is lost or duplicated.
- **CC-14, CC-18 and CC-19:** the merge took main's lines, which add the #17 entries. BE-07 had changed none of these lines.
- **PROGRESS.md and data-flow.md:** against `6e46b8a`, the merged files differ only by BE-07's lines: row #26, the new Care data-flow row and the "Since #26" sentence. In the full row diff, threat-model.md and tool-register.md only gain BE-07 rows, and nothing of main's is removed.
- **Effect of #17:** #17's PR body touches the assistant, its prompt and its suites, not `services/backend`, `cards.py` or `scripted.py`. No BE-07 statement depends on the assistant prompt or the re-recorded suites. BE-07's records cite only `backend-care` and its own window spend ($0.001872, ledger $26.751155 → $26.753027), which is still true as history. The only stale line is finding 2.

**(b) The test fix** (`services/backend/tests/orchestrator/test_be01_scripted.py`).
- **Not weakened (stop 5).** The old version asserted that each subagent's first request came before the other's last, plus `COMPLETE`. The new version keeps `COMPLETE` and asserts three things:
  - `timed_out == []`;
  - both first requests arrived before either was released;
  - the exact event order `arrived, arrived, released, released`.

  "Both first calls in flight at once" is a stronger claim of overlap than the old ordering check. Against a sequential run it fails twice over, on `timed_out` and on the event order.
- **Deterministic.** The result no longer depends on arrival order. The only timing left is the 60 s hold, which fails rather than passes when a request does not arrive. Orchestrator `DEFAULT_DEADLINE_SECONDS = 180.0` (`orchestrator/run.py:52`) is above the hold, as claimed. The planner's prompt contains neither marker (`_driver.py:37-39`), so the planner is never held.
- **Test-only.** The full row diff has no file under `services/llm-replay/` and no `orchestrator/run.py`. That confirms the mutant was reverted and that the shared `ScriptedLLM` is unchanged.
- **The `_handle` override keeps the parent's behaviour.**
  - `super().__init__()` registers `self._handle`, which resolves to the subclass method.
  - The override reads the body, which Starlette caches, so the parent's second `request.body()` gets the same bytes.
  - It then calls `super()._handle(request)` for every request. Each request is still recorded in `requests` under the lock, routed by `_queue_for`, and checked for the placeholder key.
  - One side effect: a held request is recorded when it is released, not when it arrived. Only this test uses `HeldFirstReplies`, and it does not read `llm.requests`.
  - The comment that the set needs no lock is correct: one uvicorn loop runs every handler.

**(c) Evidence** (`docs/reviews/2026-11-24-BE-07-failing-first.log`, last section). It covers what the user asked for:
- 20 separate serial runs, 20 of 20 passed;
- one `uv run pytest -n 4 -q services/backend/tests`, 890 passed in 57.32 s;
- a sequential mutant of `run_concerns` that fails the new test on `timed_out`, with `run.py` restored afterwards.

The serial-run evidence is a single summary line written by the session, not each run's output. That is enough for what was asked, but it is a claim, not a transcript. Lint on the new imports (`starlette.requests`, `starlette.responses`) is not shown. `make ci` will cover it.

**(d) Records.** Short and accurate, apart from findings 1 and 2.
- PROGRESS row #26 gains one sentence.
- The plan's "After the merge" has three bullets, and they match the diff.
- The PR body's "Bundled (decision 0021 rule 6)" section matches the code and the log.

**(e) Control surface in this scope.** None. Every file changed is outside CLAUDE.md's list: COVERAGE.md, PROGRESS.md, `docs/**` and `test_be01_scripted.py`, which is not a `conftest.py`. The merge brought in main's own #17 changes, including its re-recorded cassettes. Those were reviewed in PR #51 and do not appear in this row's diff against `6e46b8a`. Without git I could not list them.

**Security pass.** I agree that none is needed. The change is test-only. Its path is under `services/backend/**`, but it changes no source, card handling, tool or scope. It adds no code path a shopper or model can reach.

## Checked
1. Scope: matches the row plus the bundle the user and advisor approved under 0021 rule 6. The #27 draft is presented as outside the row, apart from its own stale header (L1). It touches no other window's path.
2. Failing first: the sequential mutant fails the new test, as the log records.
3. No test weakened: the old ordering asserts were replaced by stronger in-flight asserts, and `COMPLETE` is kept.
4. Hard rules and protected paths: no source, `llm-replay` or hard-rule source path changed in this scope.
5. Spend and window: does not apply. No recording in this scope.
6. Requested changes: all three of the user's instructions are met (a held first reply with a failing timeout; test-only, not `llm-replay`; 20 serial runs plus one `-n 4` run).
7. Control surface: nothing in this scope. The whole row's list is below.

## Control surface (the whole row, `6e46b8a..39aec00`, from the `make review-diff` file)
- `cassettes/backend-care/*.json`: 18 added files (052d72f9…, 195694aa…, 2c226492…, 4a916754…, 529fb6a0…, 52c76394…, 58b94ae5…, 6b55e340…, 731ab664…, 76af0668…, 942285ed…, 9bb58674…, 9c738474…, aa76fd36…, c8d033e0…, e651a589…, ea5be2d8…, f07cfe17…). They were added by `make record SUITE=backend-care` in the user's window, as previously reported.
- `services/backend/tests/backend_audit/test_be08_views.py`: not on CLAUDE.md's list. The row treats it as a pinned control (stop 5, approved by the user), so it is listed for the human.
- In this scope (`40632d9..39aec00`): none.

Files read:
- `/Users/rapido/Projects/northwind-shop-b/CLAUDE.md`
- `/tmp/claude-502/northwind-review-BE-07-scoped.diff`
- `/tmp/claude-502/northwind-review-BE-07.diff`
- `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-24-BE-07-pr-body.md`
- `/Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/scripted.py`
- `/Users/rapido/Projects/northwind-shop-b/services/backend/tests/orchestrator/test_be01_scripted.py`
- `/Users/rapido/Projects/northwind-shop-b/services/backend/tests/orchestrator/_driver.py`

Read through the diffs: the failing-first log's new section and the plan's "After the merge". I only searched PROGRESS.md and `docs/reviews/2026-11-24-17-pr-body.md`.
