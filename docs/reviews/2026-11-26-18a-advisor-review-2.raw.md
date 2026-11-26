# Row 18a: advisor-reviewer's second read, verbatim (base b5e2d50, head f1e0205)

Saved as returned.

---

findings

I read CLAUDE.md first. The diff's SHA lines match the SHAs you gave me: `base b5e2d508e9855721a234b2d2cd2d308727e3d784` and `head f1e02053cff0caf762e411eb4a92255d11617ff9` (/tmp/claude-502/northwind-review-18a-2.diff:1-2). As CLAUDE.md says, a match catches accidents, not tampering. I have no git tool, so I compared base against head only. The tree was clean at the start, so the files on disk are the head's. There are no critical or high findings: 2 medium and 3 low. The window, the 5 cassettes and the baseline change are what you described. What is wrong is that the records do not say any of it happened.

## Findings (most severe first)

1. **Items 5 and 6, medium: no committed record shows the window, the spend or the promote, and three records now say the opposite.**
   - `docs/reviews/2026-11-26-18a-pr-body.md:63-64` says "Spend: $0, and no window was opened", with "after: ____".
   - `pr-body.md:65-66` still describes "a $0 `make eval-rescore`". It does not give the outcome: the rescore refused at cc9cf4c on 5 judge misses, then the `eval-answers` window with RUN_BUDGET=0.15, then `make eval-live EVAL_SLICE=308`, 5 cassettes, $0.031810, and answerer_id moving from `check-05379e63` to `check-ba8577ea`. It also does not list the 5 added cassettes, which are on the control surface.
   - `PROGRESS.md:79` (row 18a) says "Built (all at $0, no window)" and "Next: the user's $0 rescore and promote (D3)".
   - The plan's "Spend: $0, no window" and D3's "$0 rescore" (`decisions.md:41-53`) were overtaken by a spending window. No D7 or other entry records that change, the user's consent to it, or the suite, model input and expected spend stated before the recording.
   - No promote hash is recorded. Earlier rows record it, for example `PROGRESS.md:518` "The user promoted `6e9f4ae9`".

   Everything I know about the window comes from your brief, not from a file I could read.

2. **Item 2, medium: the merge (cc9cf4c) left two decision files numbered 0025.** SA-06 brought `docs/decisions/0025-human-handoff.md` (cited as "decision 0025" at `PROGRESS.md:521` and in SA-06's PR body and reviews). This row adds `docs/decisions/0025-a2a05-partner-surface.md` (diff:624). "Decision 0025" is now ambiguous in `pr-body.md:43`, `PROGRESS.md:79` and the row's raw security reports. Main already holds 0025, so the row's file is the one to renumber. The verbatim raw reports will keep the old number, so a note is needed. No `.py` file cites 0025.

3. **Item 2, low: the cross-suite evidence for `cards.py` predates SA-06's `handoff.py`.** SA-06 added `services/gateway/src/northwind_gateway/handoff.py:50`, which imports `contains_card_data` from `cards.py`, a file this row changes. These were all measured before the merge:
   - the shared-`cards.py` unit run (`failing-first.log`, Part 2, "2,931 passed");
   - the 0-differ card check over every recorded message and tool call;
   - step 0's 448 calls.

   SA-06's `backend-care` recordings and handoff tests were not in any of them. The change only makes detection stricter, so it is not unsafe. Only `make ci` will show it; no step-0 or card-check rerun on the merged tree is recorded.

4. **Item 1, low: EV-223's wording is wrong in the PR body.** `pr-body.md:21` groups EV-223 under "…so I can't confirm it's available." The recorded answer for EV-223 (diff:407, cassette `8a92673e…`) says "so I can't say it's available". "Can't confirm" is EV-042's wording (diff:289).

5. **Item 7, low: the PR body has no control-surface entry for this commit range.** The `baseline-change` label is mentioned at `pr-body.md:66`. The 5 cassettes under `cassettes/eval-answers/` are not mentioned anywhere. On a routine row an added `eval-answers` cassette would be disqualifying. This row is not routine, so it is not a breach, but it should be stated for `make ship`.

## Your four checks
1. **My seven findings in 324e7a1: all resolved.**
   - F1: the user's D6 answer is at `decisions.md:146-154`, and `pr-body.md:8` now says D1–D6. The F3 deviation is listed at `pr-body.md:103`, and the stop rule is at `decisions.md:147-149` and `pr-body.md:92`.
   - F2: PROGRESS row 18a was brought current at that point. It is stale again because of the window (finding 1).
   - F3: the baseline is now in the diff and was read here.
   - F4: five survivors at `pr-body.md:50-52`, matching `failing-first.log` (diff:1086-1089).
   - F5: `step0.md:16` and `:29-33` now cover D5 (a), D5 (d) and D6.
   - F6: `make check` at `e8568e4`, `pr-body.md:62`. The llm-spend "after" is still blank (finding 1).
   - F7: `pr-body.md:102` and `decisions.md:156-157`.
2. **Merge.**
   - `services/gateway`, `tool_scopes.py` and `uv.lock` have no hunk against base, so the branch matches main in all three.
   - SA-06's only `tool_scopes.py` change adds "case" to `FORBIDDEN_FRAGMENTS` (`tool_scopes.py:100-107`). No cart, order or `request_return` name contains "case", so the row's partner surface (`CATALOG_READ_TOOLS | CART_TOOLS`) is unaffected.
   - The conflicts are findings 2 and 3.
3. **Cassettes and baseline: as expected.**
   - Exactly 5 new files under `cassettes/eval-answers/` (diff:34, 152, 270, 388, 506). Each is `new file mode`, `suite` eval-answers, `test_id` eval-judge, model claude-sonnet-5-5, status 200, recorded 2026-11-26 14:13–14:16Z.
   - Their questions map to starter_set lines and IDs 42, 203, 210, 212 and 223, so one cassette per item.
   - cost_usd 0.005476 + 0.006444 + 0.006098 + 0.006316 + 0.007476 = 0.031810, equal to the ledger delta you gave.
   - No cassette is modified or deleted.
   - The baseline hunk (diff:2052-2125) changes only the answerer_id check part and the items just before EV-043, EV-204, EV-211 and EV-213, so EV-042, EV-203, EV-210 and EV-212. EV-223 is unchanged.
   - The aggregates change in `overall` (groundedness 0.986627 to 0.989855, relevance 0.918831 to 0.922078) and in `variant`, n=32, only.
   - The relevance arithmetic checks: +1.0/308 overall and +1.0/32 in `variant`.
   - Nothing else in the baseline moved.
4. **Records: they do not say what happened.** See findings 1, 4 and 5. The blank `make ci` line is fine, as you intended.

## Unconfirmed
- That the committed `evals/baselines/dense-only.json` is byte-identical to what the user's promote wrote. No hash is recorded.
- The $0 scan result and the 5 upstream calls: they are in your brief only, not in any file.
- That spend and window were stated before the recording (item 5). I cannot see it in any file.
- Edits inside the row between commits. The base-to-head diff cannot show them.

## Checked
1. **Scope:** matches plan parts 0–6, Addendum A and D1–D6, plus the D3 rescore and promote. The window was a change of plan with no committed record (finding 1). PR body wording: finding 4.
2. **Failing first:** unchanged since my last read; every part and fix has a log entry. No code changed after e8568e4 except through the merge. Cross-suite evidence predates the merge (finding 3).
3. **No test weakened:** no test hunk is new since e8568e4. The merge changed no row test file.
4. **Hard rules and protected paths:** cassettes were added through a user-opened window, none hand-edited, modified or deleted. The judge is Sonnet 5.5, as configured. The cassettes hold only synthetic catalog text. There are no secrets and no `.env` access.
5. **Spend and window:** the plan stated $0 and no window. A $0.031810 recording followed with no committed statement before it (finding 1, unconfirmed).
6. **Requested changes:** D1–D6 are present and recorded. The D3 outcome is not recorded (finding 1).
7. **Control surface:** listed below. `evals/baselines` and `cassettes/**` are now in the diff and need human attention.

## Control surface (flagged for human attention)
- `cassettes/eval-answers/229ac4ad3bb6bae84b447f0ca24fc7f65993f25b8d11edef6f6cadaf15730f09.json` (added, EV-212)
- `cassettes/eval-answers/4dae90167c77add65e73b0ef6589a7c6e886f54324f3404d40b5c4e860954dd3.json` (added, EV-203)
- `cassettes/eval-answers/60c22cfe17d87f9291239aaa745e3ea24587bdf71eb52fc8124c999c98406f8c.json` (added, EV-042)
- `cassettes/eval-answers/8a92673e6a4f1c21376c78e2d182c347eba2cfb8ce15b4974f1936bc2b6d0bbc.json` (added, EV-223)
- `cassettes/eval-answers/b7670efd4e015d782ca683be3613a287714298cf9fe9689b023bba151bbcc47f.json` (added, EV-210)
- `evals/baselines/dense-only.json` (modified by the user's promote; also in RUNNER_SYNC_PATHS)
- `.github/workflows/ci.yml` (`ci-change`)
- `services/llm-replay/src/northwind_llm_replay/cassette.py`, `scan.py` (RUNNER_SYNC_PATHS)
- `services/llm-replay/tests/test_18a_tw_scan_gaps.py`, `test_canary_and_costs.py`, `test_scan_18a.py`, `test_scan_embeddings.py`, `test_scan_paths_18a.py`, `test_tool_result_order.py` (RUNNER_SYNC_PATHS)
- `services/common/src/northwind_common/cards.py` (RUNNER_SYNC_PATHS; also a hard-rule-1 path)
- `services/common/tests/test_18a_cards.py`, `test_18a_tw_cvv_gaps.py` (RUNNER_SYNC_PATHS)

Not touched: `evals/src`, `evals/fixtures`, `evals/adversarial`, `evals/contexts`, `data/enrichment`, `data/embeddings`, `Makefile`, `tools/*`, `.claude/**`, `CLAUDE.md`, conftest files, `.gitignore`, any `pyproject.toml`, `uv.lock`, `package.json`.

Files read:
- /tmp/claude-502/northwind-review-18a-2.diff
- /Users/rapido/Projects/northwind-shop-b/CLAUDE.md
- /Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-26-18a-advisor-review.raw.md
- /Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-26-18a-decisions.md
- /Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-26-18a-pr-body.md
- the failing-first log and step0.md, read through the diff
- /Users/rapido/Projects/northwind-shop-b/PROGRESS.md (rows 79 and 516-521)
- /Users/rapido/Projects/northwind-shop-b/evals/baselines/dense-only.json:3745-3814
- /Users/rapido/Projects/northwind-shop-b/evals/fixtures/starter_set.jsonl (IDs)
- /Users/rapido/Projects/northwind-shop-b/services/common/src/northwind_common/tool_scopes.py
- /Users/rapido/Projects/northwind-shop-b/Makefile:199 (RUNNER_SYNC_PATHS)
- /Users/rapido/Projects/northwind-shop-b/docs/decisions/

The plan was read through the diff.
