# RECORD-ALL (#18): runbook draft

Status: a **draft** for the user, written 2026-11-26 from the repository read-only; revised the same day with the user's settled points (the hold guard, the freeze's catalog files; row #18 step 0, advisor F7). **The approved plan's window table supersedes §3's table and §8 item 3:** W12b `eval-answers-r2` and W12c `eval-answers-r3` run after W12 ($3.55 each, `RUN_BUDGET` 6.00), so the run budgets sum to $28.75, and the stop is $25 of real spend for the row (advisor R7). It is uncommitted until the user
approves it. Nothing here has run. Row #18 is planned for about Dec 13–16 (the user's decision, 2026-11-22). The
credit runs out around 2026-12-22 (decision 0013).

Figures:
- **Cassette counts** are today's files per suite. They are one per distinct recorded call, the best measure of what
  a full run sends.
- **Ledger figures** come from `make llm-spend` on 2026-11-26: total $32.671632 of the $90.00 cap, and per-call cost
  per suite.
- **The proxy hit logs** (`.llm-proxy-log/<suite>.jsonl`) carry no run ID and add up over every window a worktree has
  run, so they overstate one run. They also hold only the suites this checkout recorded (the backend suites were
  recorded from window B's checkout). They are used below only for the hit-log removal rule, not for counts.

## 0. Prerequisites (each its own row or PR, merged and runner-synced first)
1. **#17a merged and installed** (row #18's text).
2. **FOUND-2 review 6 and 10 fixed first** (row #18's text):
   - review 6: the scanner widens its grouped patterns, rebuilds secrets split across blocks or requests, and does not
     skip a delta type it doesn't list;
   - review 10: costs are stored at fixed precision, with the scanner left alone.
3. **18a, window B's output-check prerequisite row**, merged. It owns M-A, M-B, over-blocking and split ranges
   (Open items).
4. **A2A-05 option B built** (the user's decision, 2026-11-25; `docs/reviews/2026-11-25-a2a05-surface-memo.md`):
   - `run_partner_turn` gets a `CatalogEndpoint` and a fresh cart session per turn;
   - the offered-tools check matches the exact cart tools (P3-1);
   - no query understanding, no orders or policy tools.
5. **SA-06 (#23) merged**, with its `backend-care` phase C cassettes, and any other Tier B item finished by then.
6. **The freeze, below, set and committed on `req/18-record-all`.**

## 1. The freeze (set once, before window 1; any change after it restarts the order)

| What | Where | How it is checked |
|---|---|---|
| Assistant prompt | `services/assistant/src/northwind_assistant/prompt.py` | the `prompt-<sha8>` part of `answerer_id` (`evals/src/northwind_evals/answerers.py`) |
| Output check | `services/assistant/src/northwind_assistant/output_check.py` | the `check-<sha8>` part of `answerer_id` |
| Tool names, descriptions, schemas | `services/assistant/src/northwind_assistant/tools.py`; `services/common/src/northwind_common/{catalog_tool,order_tool,policy_tool}.py`; Care and orchestrator tools in `services/backend/src/northwind_backend/agents/*.py` and `orchestrator/plan.py` | sha256 of each file at the freeze commit |
| Tool scoping | `services/common/src/northwind_common/tool_scopes.py` | the same |
| Model IDs | `ASSISTANT_MODEL`, `ROUTINE_MODEL` and `JUDGE_MODEL` in `services/common/src/northwind_common/llm.py`: `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-sonnet-5-5`; no environment override set in any window | printed at the start of each window |
| Care prompts | `SYSTEM_PROMPT` and `SYSTEM_PROMPT_NO_ORDER` in `agents/care.py` | SA-06's sha256 pin (`services/backend/tests/care/test_sa06_care_no_order.py`) |
| Query understanding | `services/assistant/src/northwind_assistant/understand.py` | sha256 |
| Catalog stock and alternatives (row #18 step 0) | `services/mcp-catalog/src/northwind_mcp_catalog/{cart,tools,holds,server,live}.py`; `services/backend/src/northwind_backend/inventory/substitutes.py` | sha256 of each file at the freeze commit |

**Record per window:**
- the freeze commit and `answerer_id`;
- `git diff <freeze>..HEAD -- <the paths above>`, which must be empty;
- the runner's HEAD, equal to `origin/main`.

## 2. The guarded window command (template; the user runs it in their own terminal)

**Before every window, the session runs the hold guard** (the user's decision, 2026-11-26) and tells the
user its output line:
`uv run --no-sync python -m northwind_backend.inventory.live_holds`.
- Exit 0 means none live; the window may open.
- Exit 1 means some hold is live; stop, and the window does not open.
- Exit 2 means not checked; stop.
- Exit 3 means BE-04's tables do not exist in the named database: its schema isn't applied there, or
  `DATABASE_URL` names the wrong database. **Exit 3 always blocks the window** (the user's decision,
  2026-11-27). Only a real 0 opens one.

The session also tells the user before each window, because window B's `make check` or `make ci` must
not overlap one.

```
B=req/18-record-all; H=<the head the session names>; S=<suite>; USD=<budget>; MIN=<minutes>
W=~/Projects/northwind-shop; R=~/.northwind/runner
[ "$(git -C $W rev-parse --abbrev-ref HEAD)" = "$B" ] \
  && [ "$(git -C $W rev-parse HEAD)" = "$H" ] \
  && [ -z "$(git -C $W status --porcelain)" ] \
  && git -C $R fetch -q origin \
  && [ "$(git -C $R rev-parse HEAD)" = "$(git -C $R rev-parse origin/main)" ] \
  && caffeinate -i make -C $R llm-runner SUITE=$S RUN_BUDGET=$USD RUNNER_MINUTES=$MIN \
  || echo "guard failed: nothing opened"
```

- **The session, in the sandbox, while the window is open:**
  - `make record SUITE=<suite>`;
  - for evals only, `make eval-live EVAL_SLICE=<n> [EVAL_SUITE=...]`.
- **Never** raise `LLM_BUDGET_USD` ($90, fixed) or a run budget mid-window without asking.
- **One window per suite.** A suite's fix round is a new window, only after a stop and the user's answer.
- **The runner closes a window after one `make record`** (observed at W11, 2026-11-27: a second `make record`
  4 minutes into a 20-minute window got `no runner window is open`). Each run needs its own window, even a run
  that is expected to replay at $0.
- **`a2a-05-partner` only:** its recorder accepts a turn only when the turn's cassettes are exactly the ones this
  run made (`run_cassette_problems`). Remove the suite's old cassettes before its window, not after it.
  W11's first run was refused for this and cost $0.050441.

## 3. The order, with budgets
Cheapest and least coupled first: the backend suites don't see the assistant's prompt, so a freeze slip there costs
cents. The full eval comes last, on the frozen prompt and check.

| # | Suite | Cassettes today | Per call (ledger) | Estimate | `RUN_BUDGET` | Minutes |
|---|---|---|---|---|---|---|
| W1 | `backend-audit` | 15 | $0.000103 | $0.002 | 0.10 | 15 |
| W2 | `backend-orchestrator` | 15 | $0.000819 | $0.02 | 0.10 | 15 |
| W3 | `backend-care` | 18 + SA-06's phase C | $0.000104 | $0.01 | 0.25 | 20 |
| W4 | `backend-replay` (1,000-order replay) | 5,959 | $0.000061 | $0.40 | 1.00 | 90 |
| W5 | `query-understanding` | 15 | $0.000126 | $0.002 | 0.05 | 15 |
| W6 | `assistant-smoke` | 18 | $0.0044 | $0.10 | 0.50 | 30 |
| W7 | `assistant-cart` | 10 | $0.0035 | $0.04 | 0.25 | 20 |
| W8 | `assistant-orders` | 28 | $0.0031 | $0.10 | 0.50 | 30 |
| W9 | `assistant-intents` | 97 | $0.0040 | $0.40 | 1.00 | 45 |
| W10 | `assistant-redteam` (the adversarial set) | 34 | $0.0042 | $0.15 | 0.50 | 30 |
| W11 | `a2a-05-partner` (option B, after 18a) | 9 | $0.0049 | $0.10–0.20 (memo) | 0.50 | 20 |
| W12 | `eval-answers`, full `make eval-live` | 655 | $0.0052 | $3.60 (a full run was 666 calls, $3.57, in `eval-answers-r2`) | 8.00 | 120 |
| W13 | `eval-judge-repeat` (`EVAL_SUITE=eval-judge-repeat`) | 308 | $0.0065 | $2.00 | 4.00 | 90 |

- **Estimated total:** about $7. **The sum of the run budgets:** $16.75. Row #18 keeps about $25 for it, and the cap
  leaves $57.33.
- **An estimate is not a measurement.** Each window's real spend is read from `make llm-spend` before and after.
- `eval-answers-r2`/`-r3` (the repeatability runs) are re-run only if the user asks: about $3.55 each.

## 4. After each window (no spend)
1. **Replay at once:** the suite's tests in replay. Every test passes and there is no miss.
2. **Hit-log removal rule:**
   - Note the line count of `.llm-proxy-log/<suite>.jsonl` before the window.
   - After it, a cassette of that suite is deleted only if no line after that mark reads it (`outcome` `recorded` or
     `hit` with its hash).
   - Never delete from a cumulative log, never by age, and never a cassette of another suite.
   - The delete list goes in the window's record, with counts: added (all read), removed (none read).
3. **Scan:** `make scan-cassettes`, clean. The user runs the key-fragment check from the runner clone:
   `make -C ~/.northwind/runner scan-cassettes CASSETTES_DIR=$HOME/Projects/northwind-shop/cassettes`.
4. **The freeze diff**, which must be empty, and `make llm-spend` before and after.
4a. **The hold guard's output line and exit code**, from before the window opened (§2).
5. **One record file per window** in `docs/reviews/`, in #30's form (`docs/reviews/2026-11-25-30-windows-2-5.md`):
   spend, recorded/hit/refusal counts, cassettes added and removed, replay result, first-byte p50 and p95.

## 5. Evals (W12–W13)
- `make eval-live EVAL_SLICE=<n>`, where n is at least the item count, so a full run.
- Then `make eval-rescore`, which never spends. Then the gate: `make eval-gate` runs in `make ci`, never on its own.
- **Promotion:** user only, from the runner clone. `make -C ~/.northwind/runner eval-promote CHECKOUT=… APPROVED_BY=…`
  with `CHECK=1` first (decision 0009). A regression or relabel needs `ACCEPT_REGRESSION` and `CANDIDATE_SHA`.
- **Containment on adversarial and unanswerable items is absolute.** A failure may only go on `known_failures.json`
  with `approved_by: model`, and only the user can make it `human`.
- **The hedge report (N-2, N-3; the user's decision, 2026-11-25),** $0, reading cassettes only:
  `uv run --no-sync --offline python docs/reviews/2026-11-25-30-w6-hedge-report.py.txt`, and
  `… --control` for the positive control. At W6, 3 sentences were excused. It lists and judges nothing; the advisor
  reads each excused sentence by eye.

## 6. The spend report (the row's last record)
A table per window:
- suite, `RUN_BUDGET`, estimate, real spend (ledger before and after), calls;
- cassettes added and removed, replay result.

Then the total against the ~$25 kept and the $90 cap, and the credit left at the end. The ledger's `checkpoint`
line and the per-suite cache hit rate come from `make llm-spend`.

## 7. Stops (CLAUDE.md "Running a row", plus RECORD-ALL's own)
- **Every window** (stop 2): the user opens it with the guarded command, one suite at a time.
- **Stop 7, a scope question:** an unplanned cassette miss in replay after a window. Never an automatic re-record.
- **Stop 7, never a re-record loop:** a recorded outcome that misses a test's planned expectation. Never an edit to
  the expectation; that would be stop 5.
- **A scan finding of any kind:** stop. Nothing is committed until it is clean.
- **Budget:**
  - A run that hits its `RUN_BUDGET` stops there.
  - Raising it, or the $90 cap, needs asking.
  - If the total reaches the ~$25 kept, stop before the next window.
- **A freeze slip:** a non-empty freeze diff stops the order. The suites recorded since the slip are recorded again,
  after the user's decision.
- **The eval gate regresses, or containment fails:** stop. Promotion and `known_failures.json` are the user's.
- **Git output** with any of the five error strings in CLAUDE.md "How we work": stop, commit nothing.
- **Credit-date risk:** if the windows have not all run by Dec 20, stop and ask which to drop. The draft's suggestion:
  `eval-judge-repeat` first, then the repeatability runs. The backend and assistant suites are never dropped.

## 8. Open for the user
1. Is this order right, backend suites first and the full eval last?
2. The budgets: one fix round's headroom in each `RUN_BUDGET`, or exact estimates and a new window for any fix round?
3. Should the repeatability runs `eval-answers-r2`/`-r3` be re-recorded at #18, or are their #32 recordings kept?
4. Should a live first-token measurement be taken at #18? Row #18's text asks for it only if #17 slipped; #17 did not.
