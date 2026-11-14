findings

The SHA lines match. `/tmp/claude-502/northwind-review-10b.diff` lines 1–2 read `base 286680f6e1c87f5a753e41d633852aed759a45c2` and `head f6f744817c23510a7327c30d66008783ee4d8727`, the SHAs you gave me. Nothing is high or critical. There are 2 medium and 7 low findings, plus some items I could not confirm. I changed nothing.

## Findings (most severe first)

**1. Medium, item 2 (evidence for Q1).**
- **Where:** `docs/reviews/2026-11-13-10b-pr-body.md:43`, `docs/reviews/2026-11-13-10b-plan.md:228,276-279`.
- **What is wrong:** both say the Q1 proof (`assistant-smoke` and `assistant-redteam` replay 100% with stale 0) comes from `make ci`. It does not. The unit run in `/tmp/claude-502/northwind-10b-ci2.log:15` deselects `e2e`, and the integration run is `-m integration` only.
- **The separate run is thin:** `/tmp/claude-502/northwind-10b-e2e.log` has only "43 passed, 24 deselected" and `exit=0`. It shows no command, no SHA and no stale or hit count.
- **Not recorded:** the run is not in `docs/reviews/2026-11-13-10b-evidence.md`.
- **Effect:** "stale 0" and "at `f6f7448`" are unconfirmed, and the PR body names the wrong source for the proof.

**2. Medium, item 2 (fixes and security tests not seen failing first).** The mutant log backs M1–M10. These fixes and guards have no failing-first evidence:
- `os.set_blocking(fd, True)` at `data/src/northwind_data/ingest_guard.py:93`. No test pins it, and removing it fails nothing. No reviewer read it either (pass-3 fix).
- The re-sign refusal, `services/rag/tests/test_fixture_signing.py::test_the_repository_fixtures_are_never_re_signed`. The user asked for this guard, and it was never run against a broken `rehash`.
- The L5/N2 log test (`services/mcp-catalog/tests/test_logs.py`) and the N5 `_jsonl` test (`services/mcp-catalog/tests/test_live.py::test_a_line_separator_inside_a_value_does_not_split_a_record`). For N5, the reviewer only reasoned about a mutant; none was run.
- **M4's label overstates it.** The mutant (`/tmp/claude-502/mutants-10b.py:13-14`) only disables the `_prices` write. The `set_stock` path was never broken, though the label says "set_price/set_stock".
- **P2's M8 re-run** ("1 failed, 25 passed") is in no file: you said so, and `docs/reviews/2026-11-13-10b-evidence.md:88` claims it.

**3. Low, items 1 and 7 (plan's paths vs the diff).**
- `docs/reviews/2026-11-13-10b-plan.md:249-256` lists only `test_index.py` under `services/rag/tests`.
- **Not in the plan at all:** the diff adds `services/rag/tests/test_ingest_verify.py`. Plan §5 put Q4's ingest test in `test_index.py`.
- **Not annotated:** the stop-5 files are not added to the plan, and `plan.md:245` still says re-hashing is limited to `test_index.py`. The user's decision is recorded in `evidence.md:95-105`, `pr-body.md:51` and PROGRESS row #10b.
- **Not listed:** the PR body's "Changes not in the plan" (`pr-body.md:87-91`) leaves out `test_ingest_verify.py`. Its control-surface bullet `services/rag/tests/` does cover it.

**4. Low, item 1 (a planned log item was dropped).** `plan.md:288` logs "`CatalogTableFake` in `services/backend` replaced by mcp-catalog | FOUND-3 rest / #19". It is not in PROGRESS, the threat model, or the PR body's open items (`pr-body.md:95-103`).

**5. Low, docs disagree.**
- `PROGRESS.md:54` (row #10b) still says "L3 and the rest of L2 logged, 'needs the user'". The same note then gives their owners by the user's decision.
- `PROGRESS.md:303` (the log line) says "two scoped re-reviews (7 lows, fixed)". It leaves out pass 3's three lows, and P1 was logged, not fixed.

**6. Low, findings without a disposition.**
- `evidence.md:72-91` gives no disposition for pass 3's unconfirmed item 2: a possible flake on the `exc_info` assert in `test_logs.py` (`docs/reviews/2026-11-13-10b-security-rereview-2.raw.md:148`).
- It also skips P2's optional part: run the ingest sentinel test through `ingest_enriched` too (`security-rereview-2.raw.md:50`).

**7. Low, the "verbatim" pass-3 file is edited and its note is wrong.**
- `docs/reviews/2026-11-13-10b-security-rereview-2.raw.md:3` is a note the session added, which says the separators "are written here as escapes".
- Lines 3 and 58 still hold the raw U+2028 and U+2029 characters, and there is no `\u2028` text in the file. The note is false, and the file is not verbatim.

**8. Low, the PR body's test-change list.**
- `pr-body.md:45` says every edited test is "named in the plan unless marked".
- Two are in neither the plan's list (`plan.md:230-243`) nor marked:
  - `test_mcp_token.py` `good_body()` gains `srv` (`pr-body.md:55`);
  - `tests/mcp/test_contract.py`'s per-server tokens and `reached()` (`pr-body.md:60`).
- Both are stricter or needed, and neither is weakened.

**9. Low, owners the user did not name.**
- Row 12's text in PROGRESS (diff line 41) and the threat-model Owners table (`docs/governance/threat-model.md:451`) give P1 (make bare `verify` private) to #12.
- Your list of the user's owner decisions does not include P1. The security reviewer proposed it.
- L6's hand-off to #12 is in the approved plan (§4); P1's is not. The user should confirm that row #12 takes it on.

## Unconfirmed
- **The SHA of the second `make ci`.** `northwind-10b-ci2.log` has no SHA line, so "at `f6f7448`" is your claim. The counts fit the stop-5 commit: 5 more unit tests (`test_fixture_signing.py` has 5) and 296 integration tests (287 passed plus the 9 errors at `a1ac4af`).
- **The read-only check of the shared index before `make ci`.** No file I read records it. `ci2.log:371-381` shows that the gate's own ingest (`northwind_rag.cli --fixtures data/fixtures`, run against the shared index) found `389cae01996a2dd6`, `data_version` 2, and 2352 chunks unchanged. That fits, but it is after the fact.
- **Authorship of `6221106`.** I have no git access. The changed line in `.claude/agents/advisor-reviewer.md` matches `tools/advisor-reviewer.proposed.md`.
- **The spend total after the row.** It is not recorded yet; it belongs in the gate report.

## Read, no finding (the changes you named as unreviewed)
- `set_blocking` sits after the `S_ISREG` check, and `finally` closes the fd on any raise.
- `rehash` resolves symlinks and refuses `REPO_FIXTURES` and anything under it.
- `test_retrieve_context_isolation.py` changes only its return line.
- P2 now patches all four parsed files, and the P3 escapes are in place (no raw U+2028, U+2029 or U+0085 under `services/mcp-catalog`).
- The threat model now names `load_verified` (`threat-model.md:429`).

I am not the security reviewer, so these files still need your attention.

## Checked
1. **Scope:** it matches the start prompt and the approval. Present: the five read-only tools, C7 and C8 tests in every region, D1 to D4 as amended, L4 with the Starlette header-parity tests (missing, duplicate, mixed-case), Q4, Q3 out, and Q1 not attached. Nothing under `services/assistant/**`, `cassettes/**` or `a2a-05-partner` changed, and the `Makefile` is untouched. Gaps: findings 3 and 4.
2. **Failing first:** M1–M10 match `/tmp/claude-502/northwind-10b-mutants.log` and `mutants-10b.py`. M6 survived first, and the test added for it is disclosed. Gaps: findings 1 and 2.
3. **No test weakened.**
   - `test_index.py`: the version-change assertion is equivalent.
   - `test_contract.py`: `reached()` is the ledger length for rag.
   - `test_cc10_contract_errors.py` and `test_tool_register.py`: the sets are wider and still exact.
   - `test_manifest.py`: changed as planned.
   - The isolation test: assertions unchanged, approved at stop 5.
   - The bare `"4242"` needle was dropped on pass 2's flake warning (`security-rereview.raw.md:157`), with the full PAN still checked, and pass 3 accepted it. No failure was observed first.
4. **Hard rules and protected paths:** hard-rule 1 path `cards.py` (function moved unchanged) and hard-rule 6–8 path `services/rag` both changed. That is allowed on a row that is not routine, and both are in the plan.
   - Hard rule 2: live reads from the in-memory store, pinned against the index.
   - Hard rule 7: visibility filtered on every tool.
   - Hard rule 8: scope as D1.
   - Hard rule 10: structured errors.
   - No `.env` read by the session (`ci2.log:1` is make's own stat).
5. **Spend:** $4.801024 before (`evidence.md:8`), $0 planned, no window and no recording. The after total is pending.
6. **Requested changes in the plan:** the plan carries D1–D4 as amended, HR2 (a) with no file edit or re-hash, the re-hash limit and the parity tests. The stop-5 decision is recorded elsewhere, not in the plan (finding 3).
7. **Control surface:** listed below for human attention. `ship` will list these.

## Control surface (every file in the diff)
- `.claude/agents/advisor-reviewer.md` (the user's commit `6221106`)
- `pyproject.toml` (root)
- `uv.lock` (in `RUNNER_SYNC_PATHS`)
- `services/mcp-catalog/pyproject.toml` (`**/pyproject.toml`)
- `services/common/src/northwind_common/cards.py` (also a hard-rule 1 path)
- `services/common/src/northwind_common/localenv.py`
- `services/common/src/northwind_common/mcp_gate.py` (new)
- `services/common/src/northwind_common/mcp_token.py`
- `services/common/src/northwind_common/tool_scopes.py`
- `services/common/tests/test_mcp_gate.py` (new)
- `services/common/tests/test_mcp_token.py`
- `services/common/tests/test_tool_scopes.py`
- `services/rag/src/northwind_rag/ingest.py` (also a hard-rule 6–8 path)
- `services/rag/tests/fixture_signing.py` (new; no security review)
- `services/rag/tests/test_fixture_signing.py` (new; no security review)
- `services/rag/tests/test_index.py`
- `services/rag/tests/test_ingest_verify.py` (new; not in plan §6)
- `services/rag/tests/test_retrieve_context_isolation.py` (stop 5)
- `data/src/northwind_data/ingest_guard.py` (includes the unreviewed `set_blocking` at line 93)

Nothing changed in: `CLAUDE.md`, any other `.claude/**` file, `.github/workflows/**`, `Makefile`, `tools/**`, `evals/**`, `cassettes/**`, `data/enrichment/**`, `data/embeddings/**`, any `conftest.py`, `.gitignore`, or any `package.json`.

No text in the diff, logs, plan or reviews tried to steer this review.
