# Row #27 PR 2 (CHK-07): advisor-reviewer, raw report

Verbatim, as returned on 2026-11-26. Diff from `make review-diff REVIEW_DIFF=$TMPDIR/northwind-review-27b.diff` (base
`2c40d88dcac6f556c9d517644efc25585be19dd5`, head `8d4eacb17fcb0c2a376ec575a1c85372504313e4`). The 39 cassette names it listed are
kept in the diff, not repeated here.

---

findings

The SHA lines match. /tmp/claude-502/northwind-review-27b.diff starts with `base 2c40d88dcac6f556c9d517644efc25585be19dd5` and `head 8d4eacb17fcb0c2a376ec575a1c85372504313e4`. Those refs can be written by the session, so the match catches accidents only. I read CLAUDE.md fresh, then the plan, the step 0 file, the failing-first log, the label review, the raw security report, the PR body, the records draft, and the diff's non-cassette parts that bear on process. I also read the 2026-11-26 `assistant-orders` entries in the ledger (read-only). I listed the cassettes and grepped them, but did not read them in full. Nothing in the diff or the docs tried to steer this review. The planted "Note to the assistant" review is a deliberate test fixture.

## Findings (most severe first)

**M1. Item 3, medium.** File: `services/assistant/tests/test_orders_journey_e2e.py:229` (`run_turn`).
- The harness change that makes it honour `Reset` was made during the window. It narrows what PR 1's six scenario checks and the five return checks read: the text before a reset is no longer checked.
- This file is not on D8's pin list. The user's decisions do not cover the change, nothing was seen failing first, and no test pins the new reading.
- The committed `return_false_policy` recording (`cassettes/assistant-orders/2e24962474a4fd5a48eff6995f11bcb6a380a25a61ed2f5291176790bfe9cd92.json`) holds the model delta " doesn't have a 90". If any of that reached the old concatenated answer, `"90" not in _DENIED_90.sub(...)` would have failed. The change would then have turned a failing scenario green during a window, which the plan's spend rule forbids ("never a test, check or label change"). That would be stop 5.
- The log only says "11 of 11 scenarios" and "read twice", so I cannot tell whether any verdict flipped.
- What to change:
  - At $0, replay all 11 committed recordings with the old reading and with the new one, and record both verdicts in the failing-first log.
  - If any verdict differs, put the change to the user under stop 5. Treat it as high until the user answers.
  - Add a scripted test that pins the reset reading.
  - List the file under "Tests widened beyond D8's list" in the PR body (lines 53–54).

**M2. Item 2, medium.** File: `docs/reviews/2026-11-25-27b-failing-first.log:62-93`.
- These tests were written with the code and never seen failing first:
  - `shp` scoping
  - the assistant's resource scope
  - the resource region and scope checks
  - the card check and unknown-key refusal on `evaluate_return_eligibility`
  - the sign-in requirement
  - the desk's key and conflict handling
- Instead there are 26 mutants run after the fact, all caught. That is decent evidence, but it replaces the agreed process, and nothing records the user accepting the substitute.
- The PR body discloses it (line 27–28). The gate report should list it as an open finding for the user, not as met.

**L1. Item 4/7 (records), low.** File: `docs/governance/tool-register.md:37`.
- `request_return` moved from `reversible write` to `irreversible or monetary` (security L2).
- The approver given is "a person at BE-06", which is a later row. Today an irreversible write has no approver in place.
- D6 was approved on the premise that this is a non-monetary write needing no confirmation. Whether an irreversible write still needs none is the user's call.
- The change tightens the label and loosens nothing. Put it in the gate report as a decision for the user to acknowledge, not only as a PR body line.

**L2. Item 5 / claims, low.** Files: PR body lines 31–36, plan line 139, log line 218.
- The ledger shows two runner run_ids (`runner-cndddpoffool`, `runner-nfcpgcfekglo`), 28 calls each. The PR body says "two windows". The plan said one window, with the fix round in the same window. The log says "the window, two rounds".
- The total, $0.172318 (ledger $32.499314 to $32.671632, and the arithmetic checks), stays inside the stated $0.30.
- Two fixes:
  - Add "two windows instead of one" to Deviations, and make the log say the same.
  - PR body line 34–36 says both matcher readings were decided "after the window". By the log (line 209, at b1b4360, before round 1 at 54a866b) and by the user's decision 4(a), the "arrived damaged" reading came before the window. Correct the timing.

**L3. Item 3 / claims, low.** File: PR body lines 50–61; source in `docs/reviews/2026-11-25-27b-step0.md:103-107` and log line 176–178.
- These changes were made after $0 runs failed and before any recording:
  - two scenario checks corrected ("please tell me the condition" counts as asking; "not 90" counts as a denial)
  - the `return_damaged` message changed
  - `returns_policy.py`'s explanation reworded so that the matcher's false positive on "arrived damaged" was not hit
- They are disclosed only in step0.md and the log. Add them to the PR body's Deviations so the user sees them where they decide.

**L4. Item 4 (triage), low.** Files: `docs/reviews/2026-11-25-27b-records-draft.md:27-30`, PR body lines 71–72.
- Security L4 is in this row's own new code (`services/mcp-orders/src/northwind_mcp_orders/tools.py` `_card_across`). It was logged to 18a, not fixed.
- The stated reason is that a fix conflicts with a pin. That is a design trade-off, so this is acceptable only with the user's acknowledgement.
- `docs/reviews/2026-11-25-18a-plan-draft.md` does not yet name L4. The ownership exists only in a draft that is applied at /req-done. Add L4 to the 18a draft's list and raise the deferral in the gate report.

**L5. Item 6, low.** File: `docs/reviews/2026-11-25-27b-plan.md`.
- The plan carries change 1 and Addendum A. It does not carry the user's later decisions: item 3 (the 18a draft and the A2A-05 memo), items 4(a)–(e), 5(1)–(2) and 6.
- Those decisions are spread across the log, the threat model, the 18a draft and the PR body. Each one does appear somewhere that I checked.
- Add an Addendum B that lists them in one place.

**L6. Records, low.**
- The plan's Verification line lists "0015 and 0023 notes". Decision 0023 is not in the diff, and the records draft has no 0023 note. Only 0024 says "Amends 0023".
- Either add the pointer note to 0023, or state that 0024 is the only record.

**L7. Item 3, low.** File: `services/mcp-orders/tests/test_chk06_get_order.py:132`.
- PR 1's exact whole-body pin is now `body == worded(expected(order_id))`. The expected side goes through the code under test.
- `worded` is a plain dict spread, and `test_chk07_request_return.py:136` checks each `status_text` separately, so the risk is small. A check that `worded` leaves every file value intact would close it.

## Unconfirmed (no evidence I could read)
- "Replay 11 of 11", and "each run's proxy log showed no old cassette read". There is no committed run output. The recorded scenarios carry the `e2e` marker, so `make ci` does not replay them. Only `make e2e`, run by the user, does.
- That the baseline equals the candidate plus main's one override. The diff is consistent with this: one line, only `answerer_id`'s prompt part changes, `prompt-19e67b6f` to `prompt-26740a49`, `check-05379e63` unchanged.
- That step 0 ran before the prompt edit, and the commit order in general. I had no git access.
- That the 28 cassettes are unedited `make record` output. They are all `new file`, none are modified, and the 11 removed are PR 1's.
- The state of `make check`, `make ci`, integration and browser runs: not run yet, as you said.

## Checked
1. Scope: matches CHK-07 and the plan with Addendum A. D10, D12, D13 and the rewording of PR 1's journey tests are absent, and `output_check.py` is unchanged. The 18a draft and A2A-05 memo are docs only, by the user's decision 3. The rule order in `returns_policy.py:248-269` matches Addendum A. I did not check against the other window's paths beyond this.
2. Failing first: shown for the evaluator, RC-061, the handler, the wording, the start-up check, the split-card fix, the stage B attach and both matcher changes. Not shown for the security tests in log §2 (M2) or for the harness Reset change (M1).
3. Weakening: the strict xfail on `return_damaged#2` and both matcher widenings are the user's, failing first. The labels are 68, all `reviewed_by: model`, sha-pinned; only RC-061 changed, by the user. `known_failures.json` and `evals/src` are untouched. Open: M1, L3, L7.
4. Hard rules and protected paths: the hard-rule paths changed (`services/assistant/**`, `chat.py`, `services/mcp-orders/**`, `playwright.config.ts`) are all named in the plan for a non-routine row. Security found nothing unsafe. Open: L1, L4.
5. Spend and window: stated in the plan before recording ($0.15, $0.30 with one fix round). Actual $0.172318, but in two windows instead of one (L2).
6. Revised plan: changes 1 and 2 are in it; later decisions are not (L5).
7. Control surface: every change below is within D8, or is the user's promote (`baseline-change`). None needs `gate-change`.

## Control surface (every file in the diff)
- `Makefile` (adds `dev-mcp-policy` and `-j6`)
- `pyproject.toml` (root)
- `uv.lock` (in RUNNER_SYNC_PATHS)
- `services/mcp-policy/pyproject.toml` (in RUNNER_SYNC_PATHS as `**/pyproject.toml`; new)
- `services/common/src/northwind_common/mcp_token.py`
- `services/common/src/northwind_common/tool_scopes.py`
- `services/common/src/northwind_common/policy_tool.py` (new)
- `services/common/src/northwind_common/returns_policy.py` (new)
- `services/common/tests/test_chk06_shopper_claim.py`
- `services/common/tests/test_mcp_token.py`
- `services/common/tests/test_tool_scopes.py`
- `services/common/tests/test_chk07_tw_policy.py` (new)
- `evals/baselines/dense-only.json` (one line, `answerer_id`)
- `cassettes/assistant-orders/`: 28 added, 11 deleted (PR 1's); modified: none. (Names listed in the report; see the diff.)
- Untouched: `.github/workflows/**`, `.claude/**`, `CLAUDE.md`, `tools/*`, `evals/src/**`, `evals/fixtures/**`, `data/enrichment/**`, `data/embeddings/**`, `evals/adversarial/**`, `evals/contexts/**`, any `conftest.py`, `.gitignore`, `package.json` files.
- Protected but not control surface, changed as planned: `apps/portal/playwright.config.ts`, `services/assistant/**`, `services/gateway/src/northwind_gateway/chat.py`, `services/mcp-orders/**`.

The PR body's control-surface section (lines 74–81) matches this list.
