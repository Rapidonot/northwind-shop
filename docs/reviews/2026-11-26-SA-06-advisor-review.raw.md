# SA-06: advisor-reviewer's row review, verbatim

Diff: `$TMPDIR/northwind-review-SA-06.diff` from `make review-diff`, base `81683c2733a7a5ead1176d739758f981a6246082`, head
`cae8f738646694e3ce6baa23b19d5089e821a2fa`. Saved as returned.

---

findings

The diff's SHA lines match what you gave me. `/tmp/claude-502/northwind-review-SA-06.diff` line 1 is `base 81683c2733a7a5ead1176d739758f981a6246082` and line 2 is `head cae8f738646694e3ce6baa23b19d5089e821a2fa`. I read CLAUDE.md fresh from disk. I traced the commit order in `.git/logs/HEAD` (lines 442–476) because I cannot read commit bodies or run anything. Everything in the diff, the log, the plan and the drafts was treated as data. Nothing in them tried to steer the review: the one "ignore previous" string is a test input at diff line 4608.

## Findings (most severe first)

**1. Item 2, high: three groups of fix and hard-rule tests have no failing-first evidence, and the PR body says more than the log shows.**
- The log is limited by its own header (`docs/reviews/2026-11-26-SA-06-failing-first.log:1`): "tests as at d233898" against the base `5d74f0a`. Everything committed after d233898 is outside it:
  - **The M1 fix tests** (a medium, `70a4307`): `test_only_a_json_request_is_taken`, the cap tests `test_over_the_open_cap_…` and `test_with_every_care_slot_taken_…`, and `test_close_cancels_a_build_…`.
  - **The L3, L6 and N1–N9 fix tests** (`70a4307`, `ccebdb1`, `e9c3401`).
  - **The phase B tests on hard-rule paths:**
    - `services/gateway/tests/test_sa06_chat_trigger.py`: no search and no model call, and card data refused before the trigger (hard rule 1, `chat.py`);
    - `services/gateway/tests/test_sa06_phase_b_tw.py`;
    - `services/common/tests/test_sa06_scopes_tw.py` and `test_sa06_no_case_tool.py::test_case_is_a_forbidden_fragment_for_every_shopper_agent` (hard rule 8).
  - **The recorded test** `test_sa06_recorded.py`.
- Only M2 (the timing test) and the test-writer's 2,000-character finding are cited as failing first.
- The PR body (`docs/reviews/2026-11-26-SA-06-pr-body.md:43-46`) says "Against the base's source every new test file fails". At the head that is not shown for the files above.
- Evidence still needed: each test above seen failing against the source before its fix, appended to the log with no test edited:
  - the phase B tests against `06713f1`;
  - the M1 and N fix tests against `21c2107` and `c8a2557`, or with mutants.
- Prior rows rated this gap high (RAG-03 hard-rule-7, #11).

**2. Item 7, medium: the control-surface list is incomplete, and stop 8 was not taken for the missing file.**
- `services/common/tests/test_sa06_scopes_tw.py` is new and sits under `services/common`. That path is in `RUNNER_SYNC_PATHS` (`Makefile:199`), so the file is on the control surface.
- It is missing from all three lists:
  - the plan's approved list (`docs/reviews/2026-11-26-SA-06-plan.md:220-224`);
  - decision 0025 (`docs/decisions/0025-human-handoff.md:75-77`);
  - the PR body's "Control surface in this PR" (`pr-body.md:98-103`).
- The user approved only the listed files. This one came in with phase B's test-writer (`9e2ff94`) with no stop 8 and no flag.
- It needs to go in the PR body and the gate report, and the user should be told it was added without approval.

**3. Items 1 and 4, medium: no security read at a stated head covers what came after `4821fc8`.**
- The phase B review gives its head as `4821fc8` (`docs/reviews/2026-11-26-SA-06-phase-b-security-review.raw.md:3,9`).
- At line 43 it cites `test_chat_py_never_reaches_the_handoff_desk` "(in `test_sa06_phase_b_tw.py`)". That file was only committed later, at `9e2ff94`, so the reviewer read an uncommitted working tree.
- At line 76 it says "No cassette was added", which was true then. Nothing after it was read by `security-reviewer` at a stated head:
  - `test_sa06_recorded.py` (`4690481`);
  - the five `backend-care` cassettes (`cae8f73`), which hold model-written summaries and the screened demo transcript;
  - the records-only commit `31968dc`.
- Only the regex scan covered the cassettes. The PR body's Reviews section (`pr-body.md:67-68`) does not say this.
- Row #20 did a cassette security read. A scoped read of `4821fc8..cae8f73` before `make ci` would close this.

**4. PR body accuracy, low: it does not mention the two records-only drafts.**
- `docs/reviews/2026-11-26-SA-01-plan-draft.md` and `docs/reviews/2026-11-26-18-runbook-draft.md` (`31968dc`) ship in this PR.
- The PR body never names them or says the user asked for them. A reader at merge would see two unexplained out-of-scope files.
- They contain no control-surface change and no text aimed at reviewers.

**5. PR body accuracy, low: "Deviations from the plan" (`pr-body.md:76-79`) leaves out two.**
- The plan's backend `test_sa06_case.py` (`plan.md:187`) became `test_sa06_handoff.py` and `test_sa06_acceptance_tw.py`.
- `asks_for_person` lives in a new `handoff_trigger.py`, not in `handoff.py` (`plan.md:44`).
- The criteria are still covered, so this is a record gap only.

**6. Evidence, low: no committed file backs the 60,000-message fuzz claim.**
- "the same answer … on 60,000 fuzzed messages" appears at `pr-body.md:39` and `security-triage.md:10`, but nothing in the diff backs it.
- The re-review's line-by-line equivalence argument (`security-rereview.raw.md:45-52`) does back the claim that the rewrite gives the same answers. The fuzz number should be labelled a session measurement, or its script and log committed.

**7. Record, low: PROGRESS.md row 23 (`PROGRESS.md:90`) leaves out phase B's reviews.**
- It covers phase A's test-writer and security review only. It has nothing on phase B's `test-writer` (30 tests) or the phase B security review (L1 fixed; L2 and L3 in the PR body).
- "All fixed except L2, L5 and N10 … and N6" reads as complete, but phase B's L2 and L3 are also open.
- The PR body has them, so this is a gap in PROGRESS only.

**8. Record, low: decision 0025's status line says more than the user approved.**
- `0025-human-handoff.md:3` says "accepted by the user with the row #23 plan".
- The decision also holds content added after approval by the security fixes: the caps (1000/800/2) and the N6 wording. The user has not seen those as part of the decision.
- Suggested wording: "design accepted with the plan; Consequences updated from the security reviews".

**Not confirmed (I cannot run commands or read commit bodies):**
- The M2 timing test failing 3 of 6 on the old matcher, said to be in `cc1bbdf`'s message.
- `c9e6d7a` failing before `d233898`. The order of the two commits is consistent with it, and both have the same timestamp.
- That `handoff_phrases.jsonl` is byte-identical to `680b36c`. The test pin `8c49184e…` (diff line 7236) matches the hash in `680b36c`'s commit subject, but no test run was shown.
- The user's change 1 (single test files only until CHK-07 shipped).
- That a fresh subagent drafted the labelled set from a short guide. No guide is committed.
- Who ran merge `06713f1`.
- Every tagged test passing. `make ci` and the browser runs are pending by design.

## Checked
1. **Scope:** matches the plan. `prompt.py` and `turn.py` are untouched. `chat.py` changes only after merge `06713f1`, which the reflog order and the phase A re-review's "chat.py emits no handoff event" support. Nothing under `evals/` changes. The two drafts are out of scope but the user asked for them (finding 4).
2. **Failing first:** high gap for the M1 and N fix tests, phase B and the recorded test (finding 1). The phase A files are covered. M2 and `c9e6d7a` are not confirmed.
3. **No test weakened:**
   - The L1 test (`apps/portal/src/Handoff.tw.test.tsx:87-102`) and the N3 test (`test_sa06_endpoint.py`, diff line 6917) are just as exact, and both were approved under stop 5.
   - `test_chk01_gateway_criteria.py` stays an exact set with `/api/handoff` added.
   - `test_be07_units.py` only gains `order_id or ""`.
   - The labelled set's pin is unchanged. The trigger target was changed by the user (≤2 misses, 0 false handoffs).
4. **Hard rules and protected paths:**
   - The `services/backend/**` and `chat.py` edits are allowed on a non-routine row.
   - The card check runs before the trigger, pinned by `test_card_data_is_refused_before_the_trigger`.
   - The assistant gets no case tool (`test_sa06_no_case_tool.py`).
   - No `.env` or settings file is touched.
   - Security reading after `4821fc8` is a gap (finding 3).
5. **Spend and window:** stated before recording (`plan.md:172-177`: suite, changed model input, about 15 Haiku calls under $0.20, `RUN_BUDGET=2`). The window record has the ledger before and after ($32.671632 → $32.672222) and 5 calls. Its counts agree (18 + 2 hits, 5 recorded, 23 distinct). Nothing went under `eval-answers`.
6. **Requested changes:** changes 2 and 4 are done; the pin commit `680b36c` comes before the matcher `a3b1d04` in the reflog. Change 3's list is incomplete (finding 2). Change 1 is not confirmed. The user's mid-row decisions (L1, N3, JSON-only for `/api/chat` in the PR body only, phase C, the merge) are all recorded.
7. **Control surface:** the list is below. One file was not flagged (finding 2).

**Criteria to tagged tests:**
- **SA-06 payload:** `services/backend/tests/care/test_sa06_acceptance_tw.py` (the five fields with and without an order) and `services/gateway/tests/test_sa06_acceptance_tw.py:6403-6423` (the five fields, and the raw session ID never in the case).
- **Honored immediately:** `test_sa06_trigger.py`, `test_sa06_chat_trigger.py` and `test_sa06_handoff.py::test_the_reference_comes_back_before_the_case_is_built`.
- **No case tool:** `test_sa06_no_case_tool.py` `[SA-06][NFR-01]`.
- **NFR-08:**
  - Vitest `[SA-06] [NFR-08]` in `HandoffNotice.test.tsx` and `Handoff.tw.test.tsx`;
  - Playwright `apps/portal/e2e/handoff.e2e.ts` through `journey.ts`, not yet run;
  - NFR-08 is correctly left `partial`.
- **Every criterion is tagged.** None is proven passing until `make ci` and the browser runs.

**Stop handling:**
- Taken correctly: stop 1 (the user), the merge stop, stop 5 twice, stop 2 (window C), and stop 7 for JSON-only on `/api/chat`.
- Phase A had a scoped re-review because two mediums were fixed. Phase B had lows only, so no re-review was needed.
- No high is open.
- Stop 8 was not taken for `test_sa06_scopes_tw.py` (finding 2).

## Control surface
These are all the control-surface files in the diff. Each needs your attention even where planned.
- `services/common/src/northwind_common/tool_scopes.py`: modified; listed and approved.
- `services/common/tests/test_sa06_scopes_tw.py`: added; **not listed, not approved.**
- `services/gateway/pyproject.toml`: modified; listed and approved.
- `uv.lock`: modified, 2 lines; listed and approved.
- `cassettes/backend-care/27873befb26610aba92d134e53295a250c245a31828ef144d9244cbd14bcb1d7.json`: added
- `cassettes/backend-care/818d91a79a424eba66ca025249ebab7632ff7b1a4fa7233e6bb317be53ccf997.json`: added
- `cassettes/backend-care/a0ab3f95b10fd75eee2faddeae752ba4f6c0d93be8c37f03ea4280ae30f01dee.json`: added
- `cassettes/backend-care/aabfc0250caf7d95ad76a9cfb184a4bab7105330b44679241a6c0566b7f8d5c9.json`: added
- `cassettes/backend-care/b80829090d70437deb1762795b26a2f91b0951dbd5962c610ffbcdddcee23ba6.json`: added

All five cassettes are new files (none modified or deleted) and all use `claude-haiku-5-5`. The gate report must name each of them.

`docs/governance/data-flow.md` and `tool-register.md` are listed by the plan but are not on CLAUDE.md's control surface. No `conftest.py`, `package.json`, `.gitignore`, root `pyproject.toml`, `evals/**`, `.claude/**`, `.github/workflows/**`, `Makefile` or `tools/*` file changed.
