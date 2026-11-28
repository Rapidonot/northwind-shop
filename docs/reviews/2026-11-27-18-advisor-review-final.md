# Row #18: advisor-reviewer before the final `make ci` (verbatim)

Diff: `/tmp/claude-502/northwind-review-18.diff` (`make review-diff`). `base 3b0e7bf3a9341635b6c77ea369257acdeae679df`,
`head 22a65bcd503f9f2c550235dcc3b1a119d430473c`. The user's requests file:
`/tmp/claude-502/northwind-review-18-requests.md`. The report follows unchanged. The user's decisions follow at the
end.

---

findings

The SHA lines match. `/tmp/claude-502/northwind-review-18.diff` starts with `base 3b0e7bf3a9341635b6c77ea369257acdeae679df` and `head 22a65bcd503f9f2c550235dcc3b1a119d430473c`. The branch reflog (`.git/logs/refs/heads/req/18-record-all`) shows the same commit chain.

There is one high: a test that will very likely fail in `make ci`. There are five mediums and eight lows. I checked the diff against the plan, the revision, the runbook, the requests file, the failing-first log and the records.

## Findings

**H1 (items 2, 3 and 7, high): the tool-list pin will very likely fail `make ci` at the head, and the fix must go to the user (stop 5).**
- `services/mcp-catalog/tests/test_18_step0_stock.py:273-275` hashes every entry of `tools.TOOLS` and expects `fc86e9e8…`. That value was taken at `bcc135b`.
- The merge `d83f61c` brought in `services/mcp-catalog/src/northwind_mcp_catalog/tools.py:501`: `TOOLS = {**READ_TOOLS, **CART_SPECS, **QUERY_TOOLS, **PORTAL_SPECS}`, with `PORTAL_SPECS` defined at `portal.py:203`.
- Main moved from `bcc135b` to `3b0e7bf` after the branch was cut, so `PORTAL_SPECS` was not in `TOOLS` when the digest was taken. The test cannot pass now. I worked this out from the code; I did not run it.
- Nothing has run that file since the merge. Wrap-up §6 ran only the `--llm-suite` selections.
- `PROGRESS.md:168` ("the whole catalog tool list are unchanged (sha256 pinned)") is also no longer true at the head.
- Change wanted: run that file with `make check` before `make ci`. Do not just paste in a new digest. Put the re-pin to the user as stop 5. One option is to pin the model-visible listing, `TOOLS` without `PORTAL_SPECS` or the assistant-scoped `tools/list`, as main's `test_sa01_portal_tools.py:39` does, and show it still equals `fc86e9e8…`.

**M1 (item 5): one spending window has no hold-guard line.**
- `2026-11-27-18-w11-a2a-05-partner.md:3-8` gives a guard line only for run 2 (10:44:46Z).
- Run 1 was its own window and spent $0.050441. No record has a guard line for it.
- The user's approval says every window starts with the guard, and runbook §4 item 4a requires the line. PR body line 33 says "a hold guard returned 0 before each".
- Change wanted: add run 1's guard line if it ran. If it did not, tell the user and correct the PR body.

**M2 (items 1 and 6): the reason given for the check-3 narrowing is wrong.**
- PR body lines 41-42 and the W11 record lines 75-76 say partner turns run "without order or cart tools".
- Under option B, partner turns run on a real mcp-catalog with the cart tools (`evals/tests/test_a2a05_partner.py:653`, runbook §0 item 4).
- The user's stated reason, and the test comment at `test_a2a05_partner_criteria.py:212`, is "no order tools".
- Change wanted: "without order tools (they have the catalog's cart tools; there is no orders or policy server)".

**M3 (item 6): the final-replay evidence names the wrong tree.**
- Wrap-up §6 line 166 and PR body line 68 say the replay ran on "`71e77c5` plus `origin/main` `3b0e7bf`".
- The merge's first parent is `de52ed5` (reflog line 24). `71e77c5` does not hold W13's cassettes, so the 308/308 `eval-judge-repeat` replay cannot have run on it.
- The replay also ran on the uncommitted merge. The user's definition is a replay "at the final head".
- Change wanted: correct the SHA. State that the replayed tree is `d83f61c`'s tree, and that `22a65bc` changes only `docs/` files, for example by `git diff --stat d83f61c 22a65bc`.

**M4: PROGRESS.md row 18 is missing most of the row before `/req-done`.**
- The row (PROGRESS.md diff line 12) stops at the $0 replays and still says "`make ci` waits on the user's go".
- Missing:
  - W7.
  - W11, with the check-3 narrowing and its stop-5 yes.
  - W12b and W12c, with the EV-092 decision.
  - W13 (misses only).
  - EV-029's cause, amendment 1, the `evals/src` change and the `gate-change` label.
  - The main merge and amendment 2.
  - The final 0-miss replay.
  - Spend of $8.279224 with `dev-scratch` excluded.
  - The hedge report and the 3-run figure.
  - The live-order security review.
  - The control-surface list: it still names only the step-0 three, not the cassettes, `answerers.py` or the 9 A2A-05 fixtures.
  - Status.

**M5: open items that name #18 as owner have no outcome recorded.**
- No #18 record mentions EV-307, EV-090, EV-097 or EV-086.
- `PROGRESS.md:394` says #18 does a by-eye check of EV-307's supported claim kinds. EV-307 was re-recorded in r2, r3 and `eval-judge-repeat`, and no record shows the check.
- `PROGRESS.md:360-372`: the plan for containment flips. The EV-092 decision covers it, but the item is not linked or closed.
- `:196`: the A2A-05 surface question, resolved by option B.
- `:216`: INDEX_DATA_VERSION. The current values, 3 and `3a89123f`, match the index, but this is not recorded.
- `:218`: the flaky ordering test.
- `:219`: R3 and R4.
- `:220`: L9 is now moot. N1 and P3-3 ask #18 to confirm that every fixture carries `cassette`. All 9 do; I checked. No record says it was checked.
- `:268`: the backend-replay prompt. That suite was not re-recorded, so it needs a new owner.
- `:305`: eval-integrity on a full re-record, now moot.
- `:172`: N2, owned by #18 itself.
- Change wanted: close each one, re-own it or record its outcome before `/req-done`. Do the EV-307 by-eye check or put it to the user.

**Lows**
- **L1 (item 6):** PR body lines 54-55 say "each removal is unread after its window's mark". W11's 9 removals came from the clear-first rule (`PROGRESS.md:220` L3, runbook §2), and the W11 record gives no mark from run 1. Name the rule that was used.
- **L2:** N2 and A3-5 say each window record names the recording catalog's database as well as the guard's. The W11, W12c and W13 records name only the guard's.
- **L3:** PR body lines 17-18 merge two sets of limits. The output-check tests use counts, an 8x CPU ratio and a 60 s limit. Only the router uses the median of 3, 6x and 30 s.
- **L4:** under B-L2, the PR body leaves out N-L2's residual: `psycopg.sql.Identifier` stays outside the text check. The triage at line 101 says it goes in the PR body.
- **L5:** wrap-up §5 lines 142-143 still says "$6.63 without W13 (about $1.80 more with it)". The real total is $8.279224.
- **L6:** stale "Not committed" notes remain at W11 record lines 17 and 63 and W12c record line 15. W12c line 17's correction text is garbled.
- **L7:** the PR body's control-surface list includes `evals/tests/test_a2a05_partner_criteria.py`, which is not on the control surface. That is harmless. Its line "The main merge brings SA-01's own control-surface files, which `make ship` lists" is wrong: those files are already on main and will not appear in this PR's diff.
- **L8:** PR body line 26 and the requests file say the values come in `tool_use` order. The code orders by the CLI's `progress_token`, which was measured to follow `tool_use` order (liveorder L1, unconfirmed). Say so in the headline line.

**Hedge report, read by eye:** all 11 excused sentences are refusals or disclaimers. The one stock claim, r2 EV-043 ("that variant is out of stock in the US right now"), comes from a live `check_stock` result with `"in_stock": false` (`cassettes/eval-answers-r2/60517facab9105e2141523b58685c07ab74ebad4311a1704916ea9d0294b8ffa.json`). No text in the diff, records or cassettes tried to steer the review.

## Checked
1. **Scope:** matches the row and the user's 12 groups of decisions. Changing the A2A-05 criteria test, in window B's area, was approved by the user under stop 5. Nothing else enters the other window's paths.
2. **Failing-first:** present for each step-0 item (a)-(d), the guard, option B (M-p1..8, M-q1..5, M-s-tools, M-s-cart), check 3 (M-c1..3 killed, with pin (a) and mutant (b)), and the live-order fix (failing first, M-o1..4 killed, and the $0 EV-029 reproduction). Nothing covers the post-merge break in H1.
3. **No weakening beyond approval:** `test_cart.py` changed as (b) requires and as recorded; `test_tools.py`'s psycopg allowance is option B with its mutants; check 3 has the stop-5 yes; the timing limits were approved. H1 must not be fixed by a silent re-pin.
4. **Hard rules and protected paths:** no `.env*`, `CLAUDE.md` or `.claude` change. Hard rule 2 holds: prices come from the live store (M-b2). The hard-rule paths touched (`services/backend`, `services/assistant/tests`, `services/rag/tests`) are in a non-routine row that was security-reviewed.
5. **Spend and windows:** every window has a budget and ledger figures before and after. The per-suite deltas add up to $8.279224, which equals $41.198667 − $32.704032 − $0.215411 of `dev-scratch`. The run budgets opened add up to $17.35. W11 run 1 has no guard line (M1).
6. **Requested changes:** every decision is honoured in substance. The gaps are M2, M3, M4, M5, L3, L4 and L8. The PR body states RECORD-ALL's definition, the check-3 narrowing, r2/r3 recorded in completion order, the EV-092 cause, `gate-change` only, B-L2, N4 and liveorder L1/L2.
7. **Control surface:** listed below. It changed with the user's approvals; it is flagged here, not silently passed.

## Control surface (this diff against base `3b0e7bf`)
- `cassettes/a2a-05-partner/`: 9 added, 9 removed
- `cassettes/assistant-cart/`: 1 added, 1 removed
- `cassettes/eval-answers-r2/`: 656 added
- `cassettes/eval-answers-r3/`: 656 added
- `cassettes/eval-judge-repeat/`: 275 added, 275 removed
- No cassette was modified. The total is 1,882 cassette files, and the only deletions are 285.
- `evals/src/northwind_evals/answerers.py` (needs `gate-change`)
- `evals/fixtures/a2a-05-partner/answers/*.json` (9 modified)
- `services/mcp-catalog/pyproject.toml` (matches `**/pyproject.toml` in RUNNER_SYNC_PATHS)
- `uv.lock` (RUNNER_SYNC_PATHS; it gains only the workspace `northwind-backend` entry)
- `services/rag/tests/test_rag07_tw.py` (`services/rag` is in RUNNER_SYNC_PATHS)
- Not in this diff: CLAUDE.md, `.claude/**`, workflows, Makefile, `tools/*`, `evals/baselines`, `known_failures.json`, any `conftest.py`, `.gitignore`, the root pyproject, the `package.json` files.

---

## The user's decisions (2026-11-27)
- **H1 (stop 5):** pin `TOOLS` minus `PORTAL_SPECS`, the tool list the model sees, and show it equals the old
  `fc86e9e8…`. Failing-first: the current test fails at the head, and a mutated model-visible description fails
  the new pin. Fix `PROGRESS.md:168`'s "unchanged" wording to say exactly what is pinned.
- **M1:** if W11 run 1's guard line cannot be found, the PR body says plainly that the guard did not run for that
  window ($0.050441), not that it ran before every window.
- **M2, M3 and the lows:** fixed as proposed.
- **M4:** bring row 18 up to date.
- **M5:** the session does the EV-307 by-eye check and reports it; the user rules afterwards. For the other items,
  one list with a proposed outcome for each, which the user approves in one go.
- Then the scoped re-read, then ask the user before `make ci`.
