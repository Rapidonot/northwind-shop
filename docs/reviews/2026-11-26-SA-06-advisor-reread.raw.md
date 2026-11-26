# SA-06: advisor-reviewer's scoped re-read at `3b12999`, verbatim

Diff: `$TMPDIR/northwind-review-SA-06.diff` from `make review-diff`, base `81683c2`, head `3b12999`. Saved as returned.

---

findings

The SHA lines match. `/tmp/claude-502/northwind-review-SA-06.diff` line 1 is `base 81683c2733a7a5ead1176d739758f981a6246082` and line 2 is `head 3b12999430e50809b1a859da72008b2968cf8206`. I read CLAUDE.md fresh. I took the commit order from `.git/logs/HEAD:476-480`. I cannot run anything or read commit bodies or old blobs. Everything in the diff, the log, the probe and the mutant script was treated as data, and none of it tried to steer the review.

The reflog shows a fourth commit after my review that you did not list: `6e04e00` (records only, `docs/reviews/2026-11-26-coverage-evidence-draft.md`). I read it. It touches no control surface and the PR body now names it.

## Answers
- **Is the high closed?** Not fully. 19 of the 20 mutants map to the tests I named, and m01's survival and fix are disclosed. Two named groups were still never seen failing (finding A).
- **Was anything loosened?** I found nothing. The `+json` cases add parameters under the same exact assertion, and the three old cases are still there. I cannot confirm the earlier blob or that the rename is byte-identical.
- **Is the M1 correction honest?** In direction, yes. It narrows the claim, and the mutant that exposed it is disclosed. Four record problems remain (finding D).
- **Does anything block `make ci`?** Yes. Finding A is a high (stop 10), finding B is pending, and finding C is a false record. Committing the security result also moves the head past `3b12999`. CLAUDE.md puts the advisor review and its fixes before `make ci`, so the new head needs `make review-diff` and an advisor re-read first.

## Findings (most severe first)

**A. Item 2, high: finding 1 is not fully closed.**
- m16 reverted `chat.py` to `06713f1` and logged "13 failed, 10 passed" (`docs/reviews/2026-11-26-SA-06-failing-first.log:80`) without naming the 10 tests that passed. Counting the tests in the two files, the 10 include tests that pass at `06713f1` without testing anything new:
  - The hard-rule-1 test that finding 1 named, `test_card_data_is_refused_before_the_trigger`. It is at `services/gateway/tests/test_sa06_chat_trigger.py:99` and `test_sa06_phase_b_tw.py:145`. With no trigger in the code, it passes there.
  - The N8 pins (the re-review notes, which finding 1's "N1–N9" covers): `test_sa06_endpoint.py:277` `test_chat_never_reaches_the_handoff_desk` and `test_sa06_phase_b_tw.py:207` `test_chat_py_never_reaches_the_handoff_desk`. Both are static pins that cannot fail on a `chat.py` that has no handoff code.
- The log does not mention any of this. The PR body (`pr-body.md`, "closed by the mutants") and `PROGRESS.md:90` ("all caught") say finding 1 is closed.
- Evidence still needed, with no test edited:
  - a mutant that disables chat's card refusal at the head, so the hard-rule-1 tests must fail;
  - a mutant that adds a `handoff_desk` / `northwind_backend` reference to `chat.py`, so both N8 pins must fail.
  - The log should also name the 10 tests that pass under m16 and the 10 under m17, and say why each passing one is a pre-existing invariant. m17's passes do look like invariant pins.

**B. Items 1 and 4, medium: finding 3 is still open.**
- The scoped `security-reviewer` read of `4821fc8..HEAD` is pending. There is no raw file in `docs/reviews/`, and `pr-body.md` says "_pending._".
- `make ci` must wait for its result.
- If it leads to a fix at medium or above, a scoped re-review follows. Then the new head goes back to `advisor-reviewer`.

**C. Record, medium: PROGRESS.md states a review that has not happened.**
- `PROGRESS.md:90` says finding 3 was "a security read after `4821fc8`, done as a scoped read". The read has not been done.
- This is a false line in the durable record. Fix it to "pending", or fill it in once the read lands.

**D. M1 correction, low (four record problems).**
1. `docs/reviews/2026-11-26-SA-06-security-triage.md:46` is headed "The advisor's correction to M1". I made no content-type claim. The correction came from the session's probe after m01 survived. Attribute it to the session.
2. The probe scripts are uncommitted (`$TMPDIR/probe_ct.py`, `probe_chat2.py`, per the probe record's last line). That makes the probe a session measurement, like the fuzz figure, but decision 0025 (`docs/decisions/0025-human-handoff.md`, the "/api/handoff takes application/json only" bullet) and the PR body call it "measured" without saying so.
3. Nothing tests that `/api/chat` refuses bodies with no Content-Type. That behaviour comes from FastAPI 0.162 as pinned in `uv.lock`, so a lock bump could change it silently. It is stated as a fact.
4. `services/gateway/tests/test_sa06_endpoint.py:278` still says "/api/chat takes bodies with no Content-Type", which the probe contradicts. Correcting that docstring is not a test change.

**E. Item 2, low: m20's "CAUGHT" is weaker than the label.**
- The summary at `failing-first.log:84` is Vitest's "the error was thrown after the test completed". The script counts any non-zero exit as caught (`mutants.py.txt:134`).
- So m20 may have been detected by an unhandled render error, not by the assertion at `apps/portal/src/HandoffNotice.test.tsx:212`. CI would still go red, but the log should say which happened.

**F. Evidence, low: two runs are recorded in prose only.**
- The m01 re-run (`failing-first.log:87-89`) says "at the same head", but the test had just been edited. The script refuses a dirty tree (`mutants.py.txt:110-112`) and prints "tests unchanged", so either the re-run was not made by the committed script or it was at a later head. The re-run line has no header or SHA.
- The recorded-test run with the cassettes moved aside (`failing-first.log:95`) is a summary, not raw output.
- The PR body says "3 failed"; the log says "3 failed, 3 errors".

## Checked
1. **Scope:** no new code in the three commits beyond the rename, the `+json` cases and records. The two drafts and the coverage draft are named in the PR body and are records only.
2. **Failing first:** 19 of 20 mutants catch their named test, and m01 is caught after the `+json` cases. Still never seen failing: the hard-rule-1 test and the N8 pins (A). m20 (E) and the m01 re-run (F) are weaker evidence than labelled.
3. **No test weakened:** no loosening seen. `test_only_a_json_request_is_taken` keeps its three cases and exact assertion and gains two. The rename is not confirmed byte-identical (I cannot read the old blob).
4. **Hard rules and protected paths:** no new hard-rule path was edited in these commits. The security read after `4821fc8` is pending (B).
5. **Spend and window:** no new recording or spend since `cae8f73`. The mutants and probe ran with a scripted model and replay, at $0.
6. **Requested changes (my findings 1–8):**
   - 1: partly closed (A).
   - 2: done. The file is now at `services/gateway/tests/`, which is not in `RUNNER_SYNC_PATHS` (`Makefile:199`), and the PR body discloses it.
   - 3: pending (B).
   - 4–8: done in the PR body, `PROGRESS.md` and decision 0025, apart from the false line in C.
7. **Control surface:** nothing new in `6e04e00..3b12999`. The list is below.

## Control surface
- `services/common/src/northwind_common/tool_scopes.py`: modified; listed and approved.
- `services/common/tests/test_sa06_scopes_tw.py`: added at `9e2ff94` without approval, then removed from that path at `fd66fb5`. It is now `services/gateway/tests/test_sa06_scopes_tw.py`, off the control surface. It is not in the net diff, but the gate report must still name it.
- `services/gateway/pyproject.toml`: modified; listed and approved.
- `uv.lock`: modified; listed and approved.
- `cassettes/backend-care/27873befb26610aba92d134e53295a250c245a31828ef144d9244cbd14bcb1d7.json`: added
- `cassettes/backend-care/818d91a79a424eba66ca025249ebab7632ff7b1a4fa7233e6bb317be53ccf997.json`: added
- `cassettes/backend-care/a0ab3f95b10fd75eee2faddeae752ba4f6c0d93be8c37f03ea4280ae30f01dee.json`: added
- `cassettes/backend-care/aabfc0250caf7d95ad76a9cfb184a4bab7105330b44679241a6c0566b7f8d5c9.json`: added
- `cassettes/backend-care/b80829090d70437deb1762795b26a2f91b0951dbd5962c610ffbcdddcee23ba6.json`: added

No `conftest.py`, `package.json`, `.gitignore`, root `pyproject.toml`, `evals/**`, `.claude/**`, `.github/workflows/**`, `Makefile` or `tools/*` file changed.
