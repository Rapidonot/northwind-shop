findings

The SHA lines match in both diff files: base 7f0f8d9b84bb46b169b9d43e7f159e5072e563e1, head 9027b444ae73822e2a3d6b75e33f958685700bd7. I ran nothing. Every result below is read from the diff, the committed files, `.git/logs/refs/heads/req/30-clarify-intents` and the local `.llm-proxy-log/sa04.json` and `sa03.json`. Those two are gitignored and come from the latest local run. Root of all paths: `/Users/rapido/Projects/northwind-shop/`.

## Findings

### High

**F1. (7, 1) A control-surface file was changed outside the approved list and is not in the PR body.**
- File: `services/rag/tests/test_frozen_vectors.py` (code diff 7313).
- `RUNNER_SYNC_PATHS` (`Makefile:195`) holds all of `services/rag`, so this file is control surface.
- The plan (`docs/reviews/2026-11-25-30-plan.md:69-71`) lists the approved surface and ends "Anything else is a stop". `services/rag` is not in it. I found no recorded approval for this file; the stop 5 decision names `test_rag04_post_review_tw.py`.
- The edit only adds coverage (the 70 messages and their part texts).
- `docs/reviews/2026-11-25-30-pr-body.md:61-63` must list it, and the user must be told and approve it (stop 8).

### Medium

**F2. (1, accuracy) "Output check (only tightens, ...)" is wrong.** `pr-body.md:15`.
- `docs/decisions/0015-output-control.md`, row #30 note, says otherwise. M2 shows a sentence that used to be dropped. `_negated` newly shows true negated claims at zero, negated negative phrases, text where the old offset made a false "not", and "never out of stock". Security L-2, read-3 L-A and L-B say the same.
- It should say: H3, the offer class and M-4 only tighten; M2 and `_negated` also show text that was blocked.
- M-4 and `_negated` both change the check and are missing from "What this does".

**F3. (Limits missing) The PR body hides the segment and item regressions.** `pr-body.md:31-32, 50-54`.
- "behaviour_ok 0 change" nets 5 new failures (EV-179, 204, 208, 214, 245) against 5 fixed (EV-044, 196, 206, 209, 220). EV-179 is a false "none under 320 g".
- Segments that fell:
  - `intent_filter`: citation_ok 0.946 to 0.892, false_refusal 0 to 0.054
  - `policy`: behaviour_ok 1.0 to 0.975, relevance 1.0 to 0.988
  - `variant`: relevance 0.812 to 0.781
  - `ambiguous`: citation_ok 1.0 to 0.95
  - `adversarial`: relevance 0.938 to 0.900 (EV-102, 302, 304)
- These are in the W6 report and the baseline diff. Add them to "Limits"; they are what a person promoting should see.

**F4. (Accuracy) EV-033 is also a new citation failure.** `docs/reviews/2026-11-25-30-w6-eval-answers.md:39` says "EV-258 and EV-200 are new".
- The baseline diff shows EV-033 `citation_ok` 1.0 to 0.0.
- It should say "EV-033, EV-200 and EV-258 are new; EV-194 and EV-198 stay; EV-050, 091, 112 and 212 are fixed".

**F5. (Stale) The SA-04 tail count is from the superseded first W1.** `pr-body.md:116-117` says six of 20.
- `.llm-proxy-log/sa04.json` (19/20, CL-019 failing, so the fix-round run) shows ten: CL-002, 006, 009, 011, 013, 014, 017, 018, 019, 020. CL-016 ("Your budget would also help") is borderline.
- CL-006 ("I can match you to a size"), CL-009, CL-017 and CL-019 ("I can then narrow down") say what the assistant will do with the reply. The new prompt line forbids that (`prompt.py`, "Never say what you will or could do with the shopper's reply").
- CL-020 and CL-006 come close to a lookup offer, so "none offers a price, stock or lookup" needs a re-read.
- `docs/reviews/2026-11-25-30-w1-intents.md`, "W1 again", does not recount them. State the count for the committed recording.

**F6. (Limits missing) Fixed sentences on screen in the committed SA-03 recording are not all reported.**
- IC-042 shows "I couldn't match that policy term to Northwind's policy for your region." in a cited answer about the 45-day Canada window. It passes SA-03. It is in none of `pr-body.md`, the W1 report or `PROGRESS.md`.
- I could not see the blocked sentence, so I cannot say whether blocking it was right. It must be read.
- IC-029 (stock sentence in a list) is in `PROGRESS.md:175` but not in `pr-body.md:102-110`.

**F7. (1) An unrelated file is in the row.** `docs/reviews/2026-11-25-RAG-10-plan-draft.md` (commit 9027b44, the reviewed head).
- It is row #31's plan. It is not in this row's plan or in the PR body, and the commit subject is not row 30's.
- It says "uncommitted", and "19 of 308 eval items refine", which is now 17 (F10).
- Either the user accepts it and the PR body names it, or it leaves this branch.

**F8. (Accuracy) A label re-pin is missing.** `pr-body.md:35` lists `b5f08e9` and `45c47b5`.
- The third is `859ae28` (IC-039 and IC-040 `absent` gains "is in stock"). The user's decisions name three.
- It should say "re-pinned at `b5f08e9`, `45c47b5` and `859ae28`".
- The labelled limits (L-5) should say the IC-039/040 part was fixed. The log order (868d134 claimed a scorer edit that landed in 83142c6) is disclosed only in the commits.

**F9. (Security coverage not stated) Part of the row had no security read.**
- Read 3 covered only `output_check.py` and two test files (its own opening says so).
- The fix-round prompt (`a5e5519`), the frozen-answer change to the red-team and smoke tests (`38df1d8`) and the `de4add1` guard fixes had none. `pr-body.md:145` discloses only the last.
- `services/assistant/**` is a hard-rule path. The PR body must say that the fix-round prompt and the red-team test change were not read by `security-reviewer`.

### Low

**F10.** `pr-body.md:51` "10 refinements replaced": the diff removes 10 lines and adds 8, so 19 become 17. Say "10 removed, 8 added".

**F11. (Stale)**
- `pr-body.md:105`: IC-040's quote is the first W1's. The committed one is "I can't say it's in stock" (per `w1-intents.md` and `PROGRESS.md:175`).
- `pr-body.md:106`: "These alternatives are available:" is superseded. The fix round wrote "These alternatives are in stock:".
- `pr-body.md:107`: RT-004, RT-008 and RT-015 "New with this prompt" belongs to the first W4. The fix-round W4 has 0 (`windows-2-5.md`). Mark it as fixed.
- `pr-body.md:97-98` (N-9): "today's recordings" and "any new containment failure at W6 is read by eye" predate W6. The W6 report says none.
- `pr-body.md:88-89` (L-8): still "to read in W1 and W3". W1 says the budget case did not occur; the cart half needs its result.
- `pr-body.md:111-113`: `fabricated_ok` is headed "slice of 80". At W6 it fails three: EV-028, EV-029 and EV-212.

**F12.** `pr-body.md:24-25` gives SA-04 before and after the fix round, but not SA-03. It was 44 (first W1), 43 (fix round, check `d67da9f8`), 44 (after `_negated`, check `05379e63`).

**F13.** `pr-body.md` gives no row total. The windows sum to $5.077362, which agrees with the reports' ledger figures ($27.421951 to $32.499314).

**F14. (2)** The failing-first log does not hold the test-writer H4 failures ("3 found a real hole", `pr-body.md:137`; fixed in `3f4fafb`). The evidence is prose only, in `step0.md`, Addendum. The M1 Python tests failed first as a collection error, not test by test.

**F15. (Owner)** `PROGRESS.md:169` and `:173` and the PR body name row "18a". No table row 18a exists at this head. Confirm it exists on window B's branch, or add it, so the two mediums have a real owner.

**F16.** `docs/reviews/2026-11-25-30-w1-intents.md` ("1 of 585 recorded calls") and decision 0015 and `step0.md` Addendum 5 ("one of 522") differ. Say why.

**F17.** `docs/reviews/2026-11-25-30-w6-eval-answers.md` says the hedge sentences are "To be read by eye", while `pr-body.md:93-95` reports them read. I confirmed only that EV-100's own query names STAFFALL; I did not read EV-302 or RT-022.

**F18.** `docs/governance/data-flow.md:50`: full stop missing after `c42fc7d3...json`. `docs/reviews/2026-11-25-30-merge-prep.md:32-37`: the "after" hashes are superseded by the fix round (the pinned test holds others); mark them so.

## Checked

1. **Scope.** The code matches the plan and the listed decisions, except F1 and F7. No change to `known_failures.json`, the Makefile, workflows or `.claude`. The plan's approved surface and stops were read.
2. **Failing first.** Log sections and SHAs agree with the commit order (security fixes at 55fd1b3, re-review at 567753e, M-4 at 16f3f9b, fix round at 37587eb, `_negated` at e26ce7b). Gaps: F14.
3. **Weakened tests.** `REPLACED_IN_RECORDING` emptied and RT-003's live proof moved to frozen deltas: recorded as the user's stop 5, check (h)'s assertions kept on the frozen answer, `frozen_answers.json` commits 5e46f2d and 2872dc2 are the pre-window heads. `len(new) <= 5` became an exemption of exactly the 75 keys, with tests. The anonymous prompt pins were re-pinned twice, as stated. 19 strict xfails: 12 + 1 + 1 + 4 + 1.
4. **Hard rules and protected paths.** No other violation found beyond F1. `_negated` and M2 show more text (F2). M-A and M-B are open on main and here, by the user's decision.
5. **Spend and windows.** The six windows and the estimate are in the plan. The actual $5.08 is under the $5.30 one-fix-round estimate. Window opening SHAs match the commit log.
6. **Requested changes.** Each change under "Changes at approval" and "After the security pass" is in the plan. I checked the main ones against the diff.
7. **Control surface.** Listed below; F1 is not in the PR body.

**Numbers that agree with the diff:**
- Cassettes: 1,440 diffs, 767 added and 673 deleted, none modified; 1,200 in `eval-answers` (599 + 601), 240 in the assistant suites. This fits the window table.
- `assistant-intents` holds 97 files.
- Embeddings: 75 added, 0 removed.
- Baseline identity `prompt-19e67b6f:check-05379e63`; the overall metrics equal the PR body; row 32's override is kept.
- SA-04 19/20 (CL-019). SA-03 44/50 with the six misses named.
- P0009's name in 8 cassettes, and all eight cited paths exist.
- 21 `human` items.

## Could not check
- Any test, replay, mutant run, scanner, `eval-integrity`, `eval-gate` or `make ci` result; sha256 of the label pins or the candidate; that the rebuild was byte for byte.
- The ledger and hit logs: I used only the reports' ledger figures. "Unread" and "0 refusals" rest on the reports.
- That the user made each decision and ran the promote. The evidence is the plan, the reports and commit messages.
- The D8 count. "10" was measured on #17's baseline (`prompt-95d4b694`), while this row replaces `prompt-02210891`. I did not re-run #32's method.
- The 22 `test-writer` failures and the mutant counts. The first-byte and token figures. Whether `origin/main` is still 7f0f8d9.
- All cassette bodies by eye: I read only `.llm-proxy-log/sa04.json` and `sa03.json`, plus a name search.

## Control surface (item 7; flagged for human attention)
- `evals/src/northwind_evals/cli.py`, `harness.py`, `intents.py` (new)
- `evals/fixtures/sa04_prompts.jsonl`, `evals/fixtures/sa03_conversations.jsonl` (new)
- `evals/baselines/dense-only.json` (the promote)
- `evals/contexts/refinements.ndjson`
- `data/embeddings/fastembed-BAAI-bge-small-en-v1.5-384.jsonl` (add-only, 75)
- `cassettes/**`: 1,440 files (assistant-intents, -smoke, -cart, -redteam, -orders, eval-answers)
- `services/rag/tests/test_frozen_vectors.py` (in `RUNNER_SYNC_PATHS`; **not in the PR body's list**, F1)

The other PR-body entries match. `gate-change` and `baseline-change` are stated as the user's.
