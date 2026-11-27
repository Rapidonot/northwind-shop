# Row #18, W13: `eval-judge-repeat` (2026-11-27), misses only

- **Window:** opened by the user at `71e77c5` with `RUN_BUDGET=4.00`, after SA-01 PR 2 shipped. The freeze is at
  `2ca787e`, with amendment 1 (`answerers.py`).
- **Hold guard before the window** (16:15:55Z): `live holds: 0 in database 'northwind'`, exit 0.
- **Count at $0 before the window:** 275 of 308 judge requests miss, 33 hit (`2026-11-27-18-wrapup.md` §1).
  Amendment 1 did not change the count.
- **Run:** `make eval-live EVAL_SLICE=308 EVAL_SUITE=eval-judge-repeat` with `FASTEMBED_CACHE_PATH` set,
  16:16:03–16:31:19Z, exit 0. Result: "308 items, 0 not ok".
  - The recorded answers replay from `eval-answers`, and every judge request goes to this suite.
  - Metrics, reported only: groundedness 0.990, relevance 0.922, false_refusal 0.014, behaviour_ok 0.977,
    citation_ok 0.981, fabricated_ok 0.987. Adversarial: behaviour_ok 1.000.
- **Spend:** `make llm-spend` showed $39.329836 before and $40.983256 after, so **$1.653420**. The suite's total went
  from $2.011828 to $3.665248. The user's `dev-scratch` window opens only after this one closes, and its rows are not
  the row's.
- **Proxy hit log:** `.llm-proxy-log/eval-judge-repeat.jsonl` did not exist before, so the mark is 0. After the run:
  308 lines, **275 `recorded`, 33 `hit`**, with no refusal. That is exactly the count.
- **Cassettes:** 308 before, 275 added, so 583.
  - **Removed 275** under the hit-log rule. These are #32's judge cassettes that no line after the mark reads. All
    were tracked, and every new cassette is read.
  - 308 after.
- **Replay right after,** at $0 (`$TMPDIR/w13script/count_w13.py`: the CLI's judge-repeat path with every proxy
  forced to replay; nothing written): **308/308 ok, 0 misses, no proxy stops.** Ledger unchanged at $40.983256.
- **Tests that read the suite** (`git grep -l eval-judge-repeat -- '*.py'`): `test_rag12_judged_extra.py` and
  `test_rag12_side_runs.py`, 61 passed. Both use scripted models or the CLI's arguments.
- **Scan:** `make scan-cassettes` clean (0 findings).
- **Freeze:** `shasum -a 256 -c`: 24 OK. `answerers.py` is at amendment 1's `a3b9c028…`.
  `xargs git diff --name-only 2ca787e 71e77c5 -- < paths`: none of the 24 changed.
- **The user's `CHECK=1` dry-run promote** (2026-11-27, after the rescore in wrap-up 2b): nothing changed, so there
  is no promote. `finish` needs `gate-change` only.
