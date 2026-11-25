# Row #27 advisor-reviewer, row review at 0c0db58 (raw, verbatim; trailing spaces stripped)

findings

Row #27 PR 1 (CHK-06). The diff's first two lines match the SHAs you gave: base `1ac39622dfe65404abaae22a2d12e8c15583d00c`, head `0c0db58b43dcdf1a8185066138c41445e98d89b6`. I am read-only with no git and no shell, so every test count is as logged, not re-run. No critical or high. 2 medium, 5 low. No text in the diff, log, plan or fixtures tried to steer the review.

## Findings

### M-1 (checklist 4, medium): the records overstate what the output check blocks, and the row's own cassettes show it
- **Where:**
  - `docs/decisions/0015-output-control.md`, row #27 note ("the confirmation class blocks those claims on every turn").
  - `docs/decisions/0023-demo-signin-and-signed-in-orders.md` §6.
  - `docs/governance/threat-model.md`, the row #27 Tampering row and its Owners line ("other than confirmed / placed").
  - PR body lines 26-27 and 101-102.
- **What is wrong:** the confirmation pattern (`services/assistant/src/northwind_assistant/output_check.py:150, 350-354`) needs `order` directly before the verb. An order number in between takes the claim past it.
- **Evidence (read, not run):** three recorded answers carry that shape and were shown unchanged, because the journey test asserts no fixed sentence was substituted:
  - "Order O000418 was placed on 2026-10-26" (an `on_hold` order, `cassettes/assistant-orders/ff4efcb7…`)
  - "Order O000406 was placed on 2026-10-14" (`b040ca89…`)
  - "Order O000928 was placed on 2026-10-13" (`489ccd9a…`)

  Those three are truthful: they state `placed_on`. But by the same regex "Order O000418 has been confirmed." also passes for an on-hold order, and nothing in the row tests that form. `test_b_…`, `test_an_order_on_hold_…` and the `tw_gaps` cases all put the number before a separate "your order", so they do not reach it.
- **Why it matters:** `ORDERS_RULES` asks for the order number with the status, so this is the likely phrasing, not an edge. The check is main's and the gap is older, but this row adds order turns and states the control as complete (hard rule 2). The user chose option B on the basis that these claims stay blocked.
- **What to do:** correct 0015, 0023 §6, the threat-model Open and Owners lines and the PR body, so they say "order … placed/confirmed with the number between the noun and the verb is also held by the prompt only". Tell the user in the gate report, because it bears on the option B decision. Do not add an exemption, and do not change `output_check.py` in this row without a stop: it needs the user's decision, plus the rescore and promote sequence.

### M-2 (checklist 2, medium): no failing-first evidence for the feature's own security tests
- **Where:** `docs/reviews/2026-11-24-27-failing-first.log`. Plan line 122: "All are written failing first".
- **What is there:** logged failing first are M18, H10, H5; the security fixes M1, L1, L2, L3, N1, N2 and the flag-off 404; option B; `model_text`; BE-05 (throwaway file); the prompt placeholders; the O012345 example; and the underscore change.
- **What is missing:** any failing run for the initial tests of:
  - own-orders-only and same-bytes `not_found`, the allow-list, card-data refusal and live reads (`services/mcp-orders/tests/test_chk06_get_order.py`)
  - the `shp` claim (`services/common/tests/test_chk06_shopper_claim.py`, logged only as "28 passed")
  - sign-in binding and the forged cookie (`services/gateway/tests/test_chk06_signin.py`, whose first 20 tests appear only as passing)
  - test-writer's `test_chk06_tw_gaps.py` and `test_chk06_tw_edges.py`

  These are NFR-01, PAY-05 and hard-rule tests. L1 of re-review 3 already found four of them passing for the wrong reason, which is what failing-first catches.
- **What to do:** supply the log if it exists. Otherwise record mutant runs: remove the owner check, the allow-list, the card check, the `shp`/`srv` check and the session in the shopper mac, one at a time, in a scratch copy, and log each test failing. Say in the PR body that this was done afterwards.

### L-1 (checklist 1 and 7, low): `CLAUDE.md`'s `make dev` line is now stale
- `CLAUDE.md:57` still says "mcp-rag + mcp-catalog … + gateway + portal", but `make dev` now also starts mcp-orders and turns the demo sign-in on (`Makefile:92-96`). CLAUDE.md says to keep that list current.
- It is control surface and was not in D4/D4+, so it needs the user's yes before any edit. List it as a question.

### L-2 (checklist 6, low): Addendum C omits part of change 8
- The user's condition was "byte-identical to the candidate … its only differences from main's are answerer_id and the promotion record".
- What I found: the baseline differs from main in one line (`answerer_id`), it carries row 32's override, and no promotion record was added. The candidate on disk (`.llm-proxy-log/baseline-candidate.json`) has `overrides: []`, so the file is **not** byte-identical to it.
- `promotion.py:260-261, 434-484` explains both: a candidate may hold no overrides, main's are carried over, and the overrides appended there are for a bootstrap or a reason. So I do not question the promote.
- But the stated condition was not met literally, and neither the plan (Addendum C item 8) nor the PR body says so. State it in the gate report for the user to confirm.

### L-3 (checklist 6, low): some requested changes are not in any plan addendum
- The original BE-07/BE-01 flake fix appears only in the PR body and log; Addendum C covers `model_text`, BE-02 and BE-05.
- Change 7's "guarded" window is not in the plan.
- Change 9 (no checkout or reset, no sandbox merge) is not in the plan, although Addendum C item 1 records the failed sandbox merge.
- Everything else in your list is in the plan or its addenda.

### L-4 (checklist 6, low): revised plans and `advisor-reviewer`
- CLAUDE.md sends any revised plan to `advisor-reviewer` before the user sees it. The plan itself says (lines 23-25) that the advisor had not re-read it after its 1 high and 2 medium were folded in, and I found no record of a review of Addenda A and B, which put departures and stop-5 questions to the user. It was disclosed to the user, so it is a process note. State it in the gate report.

### L-5 (checklist 4, low): stale docstring in a runner-sync file
- `services/common/src/northwind_common/order_tool.py:4-5` says "the assistant reads the order's status back with the same code". Since option B nothing in the assistant does, as the same docstring says further down. `unlabel` is used in `src` only by `northwind_mcp_orders/testing.py:20, 91`, and `CONFIRMED_STATUSES` is used only by tests.

## Not a finding, for the gate report
- A token with both `qf` and `shp` is refused by the code, but no test sends one. A pin would be cheap.
- `security-reviewer` has read nothing after `57635e2`, which includes the merge resolution in hard-rule 6/7/8 files and the `model_text` Medium's fix. The PR body says so. The user waived the post-merge advisor pass, and you did not report a security re-review waiver.

## The three checks from change 1(a), on the resolved files
- **`shp` is set only by sign-in: yes.** The only `src` mint with `shopper=` outside `testing.py` is `orders_client.py:34`, from `Caller.shopper_id`. `Caller.shopper_id` is set only in `settings.py:44`, from middleware state (`session.py:181`), from a cookie only `signin.py:59` issues, with the flag checked in all three places.
- **A token with both claims: refused everywhere.** `_claims` requires `qf` to be on northwind-rag and `shp` on northwind-orders (`mcp_token.py`, the `qf` and `_shopper_ok` lines), and `srv` is one value.
- **`get_order` in no other agent's scope: yes** (`tool_scopes.py:57-72`), pinned in `test_tool_scopes.py`, `test_chk06_tw_gaps.py` and `test_chk01_criteria.py`.
- Against base `1ac3962` the edits to `mcp_token.py`, `tool_scopes.py`, `turn.py` and `chat.py` are additive and drop none of #11's lines. A conflict-marker search of `services/` found none.

## Checked
1. **Scope: checked, with L-1.** It matches the row and the bundled items. No `evals/src`, `data/embeddings`, `evals/contexts`, `output_check.py`, `localenv.py`, `conftest.py` or workflow in the diff. #11's own new files are untouched. `services/backend/tests/**` edits are the user's bundle and are declared. The 27b draft is paper only and holds D9.
2. **Failing first: partial, M-2.** Bundled fixes and security fixes were seen failing. The BE-05 old-search failure and the M1 third-case check were in scratch files. The option B test's failing run used O000064, as the log notes.
3. **No weakened test: checked.** Pins stay exact. Post-failure changes are: the underscore change (user's stop 5, option A; the bans tightened, private values still on the exact answer; 11 cases seen failing); the `toolu_1111` fixture ID (approved, assertion unchanged); two cookies now refused (tighter); the output-check exemption tests removed with the exemption (option B). The `READ_ONLY_ORDER_TOOL` exemption is the one approved and has its own test. The scenario file's first version I could not see without git; its counts agree (36, then 50, then 51).
4. **Hard rules and protected paths: checked, M-1.** Cassettes: 11, all `seq` 0/1 as planned, `user_id` scrubbed; a search for S0188, S0103, `tok_`, postal, `Authorization`, `x-api-key` and `sk-ant` found only the scrubbed `user_id` lines. Cassettes are synthetic.
5. **Spend and window: checked.** Suite, estimate, `RUN_BUDGET=1` and the one window are in the plan before the recording; Addendum B names six scenarios. The 11 cassettes' `cost_usd` sums to $0.034618, matching the ledger figures stated. I did not read the ledger or the proxy log, and the runner's own limits are unconfirmed.
6. **Requested changes: checked, L-2, L-3, L-4.** All are in the code; the plan's gaps are above. The scenarios follow the cart test's pattern (fixed retriever, no understander). Placeholders and O012345 are in place and pinned; the anonymous prompt hashes are pinned, and `prompt.py`'s `SYSTEM_PROMPT` is unchanged against the base.
7. **Control surface: listed below; nothing else in the diff matches the list.** PR body lines 113-119 name every one.

Unconfirmed: that the failing-first runs happened at the commits named; that the user said what is quoted (including the stop-5, option A, promote and no-separate-post-merge-pass decisions); the read-only index check and the two replay runs; that `prompt-02210891` is the hash of the head's `prompt.py` (`make ci`'s gate will tell); `make ci`, which has not run.

## Control surface
Every file, expected or not. All need the user's attention at ship.
- `Makefile`: `dev-mcp-orders`, `make dev` sets `NORTHWIND_DEMO_SIGNIN=1`
- `pyproject.toml` (root)
- `uv.lock` (`RUNNER_SYNC_PATHS`)
- `services/mcp-orders/pyproject.toml` (new; `**/pyproject.toml` in `RUNNER_SYNC_PATHS`)
- `infra/docker-compose.yml` (`RUNNER_SYNC_PATHS`; M18)
- `evals/baselines/dense-only.json` (the user's promote; one line; `baseline-change` label needed)
- `services/common/src/northwind_common/mcp_token.py`
- `services/common/src/northwind_common/order_tool.py` (new)
- `services/common/src/northwind_common/tool_scopes.py`
- `services/common/tests/test_chk06_shopper_claim.py` (new)
- `services/common/tests/test_mcp_token.py`
- `services/common/tests/test_tool_scopes.py`
- `cassettes/assistant-orders/`, 11 added, none modified or deleted: `334613e0…`, `489ccd9a…`, `534b0e00…`, `5c8254d2…`, `6354fbeb…`, `a1969c24…`, `b040ca89…`, `c39cf32f…`, `d6e2cc97…`, `e717ce86…`, `ff4efcb7…`

No `gate-change` label is needed: `evals/src` is not in the diff.

Fixes for M-1, M-2, L-3 and L-5 are records, a docstring and test evidence. They move the head, so `make ci` comes after them, on the new SHA, and that SHA is the one for `make finish`.
