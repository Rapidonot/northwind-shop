# BE-05 advisor-reviewer row review at `df3d24b` (verbatim)

findings

Row #21 (BE-05 partial + CHK-05) at head `df3d24b`. There are no critical or high findings: 4 medium, 9 low, and a few items I could not confirm (listed below).

**Diff identity.** `/tmp/claude-502/northwind-review-BE-05.diff` starts with `base 201c97a275e1068494034d96787e37c1de913f05` and `head df3d24bf8b7bfe9cd99114bf2b8a30f7770814b1`, which match the SHAs you gave me. The diff has 6,022 files:
- 5,962 cassettes, all added. There are no modified, deleted or renamed files under `cassettes/`.
- 60 non-cassette files, not 58: 33 modified and 27 added.

I read `CLAUDE.md` from disk first. I also used `.git/logs/HEAD` for the commit order.

## Findings, most severe first

**1. Medium (item 2): several security fixes were never seen failing first.**
- **Fixes in `d6a5d7e`** ("re-review lows N1-N4 and F3's price variant"): none of their tests is in `docs/reviews/2026-11-11-BE-05-failing-first.log`.
  - `services/backend/tests/orders_place/test_be05_security_fixes.py`, lines 217, 233, 254, 262 and 289: `test_a_failed_escalation_still_reports_context`, `test_a_failed_store_read_does_not_stop_the_code_escalation`, `test_no_repr_of_an_order_shows_its_token_or_postal_code`, `test_after_a_doubtful_escalation_the_model_is_told_to_stop_not_to_escalate`, `test_capture_follows_a_later_validation_that_moved_the_total`.
  - The re-review N4 strengthening at `test_be05_place_scripted.py:324-326`.
- **Fixes in `7d4d085`** (claim-fix re-review N1 and N3) have no test at all:
  - the `"source": "claim_report"` label at `ports.py:213`;
  - the `"unreported"` default at `fakes/orders.py:210` and `:282`.
  - A grep of `services/backend/tests` for either string finds nothing.
- **P1–P3:** the only "failing first" entry is a collection error at `dd8a16f`. So none of the 29 prerequisite tests was seen failing on its own assertion. That includes the cross-instance and scope tests at `test_be05_prerequisites.py:270-347`.
- **#20's U2 and U3 fixes** (WRITE_POLICY, and the TIMEOUT/UNEXPECTED text) have no failing-first entry either.

**2. Medium (item 3): two tests were rewritten after failures but are not in the PR body's "Tests and checks changed" list.**
- `test_two_interleaved_duplicates_use_the_same_keys_and_write_once`:
  - At `f11f5e3` it lost `order.confirm` from its exact key comparison.
  - At `8ed9890` it was replaced by `test_two_duplicates_with_the_same_keys_never_both_get_the_claim` (`test_chk05_idempotent_placement.py:155`).
  - The cross-run key check there is now a subset, not exact equality (`:186-189`). The claim review's C5 found the first version vacuous.
- `test_a_duplicate_message_with_a_fresh_confirmation_charges_nothing_more` (`:249`) no longer exercises the port's `idempotency_conflict` on a fresh reference. That case is still covered at `test_be05_prerequisites.py:287`.

The security re-review judged neither weakened, and your F1 rules make the old expectations impossible. Still, the PR body's list is incomplete, and I found no record that you approved these two changes.

**3. Medium (item 1, plan §7 step 7): the planned security read of the cassettes is missing.** The plan says: cassette commits, then "a scoped `security-reviewer` read of the cassettes".
- None of the four saved reports covers `cassettes/backend-audit` (`821007c`) or `cassettes/backend-replay` (`30927ea`, `222eb77`).
- The claim review says outright that `821007c` "is out of scope".
- Only the automated `check_cassettes` and `scan-cassettes` looked at the 5,962 files. In earlier rows (#9, #20) a security-reviewer read the cassettes.
- Related: the fix commits `d6a5d7e` and `7d4d085` have had no security read, which is allowed under "one scoped re-review". The PR body does not say so.

**4. Medium: PROGRESS.md is stale at the reviewed head.** It is imported into every session through `@PROGRESS.md`.
- `PROGRESS.md:76` (row #21) still says "**Stopped before `make ci`:** `make scan-cassettes` fails…". You moved the log aside, and `make ci` has since run.
- `PROGRESS.md:194` says "Awaiting the scoped security review of the fix". Both the claim review and its re-review are done.
- `PROGRESS.md:274`, the row's only log line, says "Stopped at stop 6 … No spend ($2.588401)". The row then spent $0.361778. Plan §H calls for one log line at the end of the row.
- Row #21 does not give Approval 2's second measurement (the projected total for 1,000 orders plus the 5×10 replays). "About 4 minutes of replay" does not say whether that is the slice's replay time or the projected full replay.

**5. Low: `docs/governance/data-flow.md:37` still says "in the `backend-replay` suite once recorded (#21)".** The suite is recorded at head. This is the same wording problem as #20's N2, which this row fixed for `backend-audit`.

**6. Low: plan §F's evidence file does not exist.** §F says "Agreement with the labels is reported per kind in the evidence file". No `docs/reviews/2026-11-11-BE-05-evidence*` exists. Window 2's four measurements and the `BACKEND_REPLAY_REPORT` JSON are not committed. The PR body gives the per-kind outcomes in prose only.

**7. Low: `services/backend/tests/orders_place/_replay.py:24` has `CONCURRENCY = 3`.** Plan §F says "4 orders at a time", and §5 allowed only 1 as the fallback. The change is not explained anywhere.

**8. Low (item 4, stop-5 pin 2): one pin is wider than the approved wording.** At `test_be08_views.py:389`, `CATALOG_READ` is in the global `read_only` set. So any `src` module may build `AuditedCatalogPort(allowed=CATALOG_READ)`. The approved text lists CATALOG_READ with the placement constants, "only in `orders_place.py`". It is read-only ({price, stock}), but you should confirm it.

**9. Low: the model sees less than the plan said.** Plan §C says the model sees "the total in minor units and its currency". The code sends none (`orders_place.py:35-38`, `:512-519`). This is stricter, and `data-flow.md` matches the code, but the deviation from the approved plan is not called out.

**10. Low: the recorded CHK-05 check does not assert "one order".** `test_be05_replay_recorded.py:59-73` passes if all ten runs of a clean order escalate. The PRD wording "yields one order" is pinned only by the scripted `test_ten_replays_in_a_row_yield_one_order` (`test_chk05_idempotent_placement.py:121-131`). This has been so since the first security pass; it was not weakened after a failure.

**11. Low: the U3 test named in the plan was not written.** Plan §4 names `backend_audit/test_be02_agent_scripted.py` for U3, and that file is unchanged. The `get_fraud_assessments` timeout text in `orders_check` has no unit test; only the `backend-audit` replay covers it implicitly. The placement tools' text is tested (`test_be05_criteria.py:679-718`, `test_be05_place_scripted.py:421-430`).

**12. Low (item 1): an owner was moved off this row.** At `docs/governance/threat-model.md:232`, the `update_delivery_address` / `cancel_order` owner item moved from #21 to #19. Plan §G does not include this, and you should see it.

**13. Low: the claim's design is not written down in a decision or the plan.** The payment claim is a stop-6 design change: a new `PAYING` status and a new store operation. It and your seven F1 rules appear only as paraphrase, in the PR body, PROGRESS and docstrings. Decision 0014 does not mention the claim, and the plan's "Approval" section stops at your three plan changes.

## Unconfirmed
- **`make ci` figures** (4,694 passed, 22 xfailed, 282 integration, 54 Vitest, gate equal to the baseline): no output file was given and none is committed.
- **Suite size and largest cassette** (79 MB, 17,961 bytes): I have no way to measure file sizes.
- **Unchanged assertions in two tests:** the strict-xfail test's assertions after the mark was removed, and the at-once test's after its rename. I have no commit history. For the first I rely on the claim review's statement that they are byte-identical.
- **Window 2 report and hard stop:** whether all four measurements were reported to you and the hard stop applied. No file has the projection. The reflog shows 42 minutes between the window-2 and window-3 commits.
- **Approval of the two test rewrites** in finding 2.
- **Window settings actually used:** `RUN_BUDGET` and `RUNNER_MINUTES` for each window.

## Checked
1. **Scope:** no file of #13's is touched (`services/assistant`, `rag`, `mcp-rag`, `evals`, `gateway`, `common`); nothing outside `services/backend`, `data/backend`, `docs/` and the two root .md files.
   - The first commit is N1 and N2 (`dd8a16f`).
   - R1–R5, P1–P3 and U1–U4 each have tagged tests: Approval 1's four sentences are at `test_be05_prerequisites.py:117-177`, and the §F checks 1–6 are in `_replay.py`.
   - Your F1 rules are in the code. The claim is a compare-and-set under the store lock and does not go through the idempotency store (`fakes/orders.py:219-259`). A non-owner and a rerun after a crash both escalate. An escalation of a paying order merges the payment views. No new tool was added.
   - F6 is narrowed and pinned exactly (`test_be08_views.py`, `test_the_placement_sets_are_exactly_these`).
   - The #24 low is logged (`PROGRESS.md:196`), check 1 stays at 0.90, and the proxy-log item is logged against #10's hardening entry (`PROGRESS.md:53`, `:197`).
   - Labels: 950 placed, 50 escalated, all `labelled_by: rule`; the manifest's three existing hashes are unchanged. Exceptions: findings 7, 9, 12.
2. **Seen failing first:** partly. N1, test-writer's bug, F1–F5, the claim tests and C1–C4 are in the log. Gaps are in finding 1.
3. **No test weakened:**
   - The UNEXPECTED pin is stricter.
   - The FOUND-3 output-file pins add `replay_labels.jsonl`.
   - The 100 or so confirmation call sites are mechanical.
   - The three concurrency tests follow your fail-closed rule.
   - Exception: finding 2.
4. **Hard rules and protected paths:**
   - No refund or void reaches the agent, and the money-call AST pin is exact.
   - Keys come from the task ID, and handlers are bound to it.
   - No second write follows a doubtful outcome.
   - The run identity is letters only.
   - Exception: finding 8.
5. **Spend and window stated before recording:** yes, plan §5. The ledger figures add up: $2.588401 + $0.000331 + $0.009394 + $0.352053 = $2.950179. The cassette counts add up: 136 + 5,823 = 5,959.
6. **Revised plans contain every requested change:** there is no revised plan document; see finding 13.
7. **Control surface:** listed below, nothing else on it changed.

## Control surface
- `cassettes/backend-audit/**`: 3 files added (`49ca6901…`, `6266660f…`, `ab899b67…`). The 3 they replace stay, unreferenced (Q4).
- `cassettes/backend-replay/**`: 5,959 files added, none modified or deleted.
- None of the other control-surface paths changed: no `Makefile`, `CLAUDE.md`, `.claude/**`, workflows, `tools/`, `RUNNER_SYNC_PATHS` file (no `pyproject.toml` or `uv.lock`), `evals/**`, `data/enrichment/**`, `data/embeddings/**`, `conftest.py`, `.gitignore` or `package.json`.
