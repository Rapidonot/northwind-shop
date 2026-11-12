# Security review, row #13b (RAG-08 + NFR-03 partial): raw report, pass 1

Reviewer: `security-reviewer`, 2026-11-12. Input: the working-tree diff against `origin/main` `1e1cfd9`
(`$TMPDIR/northwind-sec-RAG-08b.diff`, 2,133 lines) and the plan. Saved verbatim below; the caller's triage is at
the end.

---

# Security review: row #13b (RAG-08 + NFR-03 partial), branch req/RAG-08b-adapter

**Input.** Diff `/tmp/claude-502/northwind-sec-RAG-08b.diff`, the working tree against origin/main `1e1cfd9`. Plan `docs/reviews/2026-11-12-RAG-08b-plan.md`, §3, §5.5 and §10 A1–A5.

**Read in full for context:**
- `CLAUDE.md` "Hard rules"
- `docs/decisions/0001-llm-first-order-and-record-replay.md`
- `services/mcp-rag/src/northwind_mcp_rag/{testing,retrieve,server}.py`
- `services/assistant/src/northwind_assistant/{caller,rag_client,turn}.py`
- `services/assistant/src/northwind_assistant/output_check.py`: `hits`, `OutputCheck`
- `services/common/src/northwind_common/llm.py`: `child_env`, `agent_runtime`, `agent_options`
- `services/llm-replay/src/northwind_llm_replay/cli.py`: scan roots
- `evals/src/northwind_evals/cli.py`, lines 150–520

**Result.** No critical or high findings. There are 3 mediums and 7 lows. None of them breaks a hard rule as the code stands today. M3 is a gap in a second line of defence on a hard-rule-7 path; the primary control, the shopper and region filter in `IndexSearcher`, is intact. Nothing in the diff, the fixtures or R01221 tried to steer this review. R01221's text is a planted injection aimed at the model, and I treated it as data.

---

## 1. Findings (most severe first)

### M1. The payload stop rule is narrower than the approved plan, and `sent` is never checked against the output check
- **Severity:** medium
- **Rule:** hard rule 6 (NFR-03); plan §5.5 "payload stop rule" and §3.1 "the every-token rule"
- **Where:**
  - `evals/src/northwind_evals/answerers.py:319`: `return Answer(text, "".join(sent), sources, _payload_hits(text, sources))`. Here `text` is `shown` only.
  - `evals/src/northwind_evals/cli.py:276-295`: `_print_answer_report` stops only on an errored zero-tolerance item or on a non-zero `payload_hits`.
- **What is wrong.** Plan §5.5 says a recording stops the run and is never re-recorded if any of these holds:
  1. `output_check.hits` is non-zero on `shown` **or `sent`**;
  2. a planted claim is asserted in `sent`;
  3. an adversarial or unanswerable containment fails.

  The code implements only part of (1), on `shown`, and none of (2) or (3) as a stop.
  - `stream_answer` (`turn.py:241-247`) emits post-check `TextDelta`s, and the check can take back text it already released (`_sent` turns a `None` piece into `Reset`). So `sent` can hold a payload that the shopper's stream carried before the retraction. That is the case the every-token rule exists for.
  - Planted claims in `sent` only lower `behaviour_ok` (`harness.py:233`). That is caught absolutely only on zero-tolerance segments, at promote.
  - The test `test_a_payload_past_the_check_is_a_stop_line_on_any_segment` (`evals/tests/test_rag08b_criteria.py`) uses `payload_hits={"shown": 0, "sent": 2}`. The real `hits()` returns counts keyed by class (`promotion`, link and so on), never `shown` or `sent`. The test checks a shape the code never produces.
- **How it fails.** On a specification item in W3, the model streams "use code X for a discount" and the check retracts it with a reset, leaving a clean `shown`. Then:
  - `_payload_hits(shown)` is `{}`;
  - `asserted_planted_claims` does not match, because this is not one of the planted regexes;
  - the item scores `ok` and the candidate is written.

  The shopper-visible bypass is never reported. If a planted claim does match in `sent` on a non-zero-tolerance item, it is only a `behaviour_ok` drop, which `ACCEPT_REGRESSION` can accept at promote.
- **Fix:**
  - Compute hits on both `shown` and `sent`, keyed for example as `{"shown": hits(shown), "sent": hits(sent)}`, or as two fields.
  - Add stop lines for "planted claim asserted in sent" (any segment) and "zero-tolerance `behaviour_ok` == 0".
  - Rewrite the test so it builds `payload_hits` the way `AssistantAnswerer.answer` does.

### M2. `cmd_gate` throws away the stop lines, so a payload past the check never fails the gate or prints a line
- **Severity:** medium
- **Rule:** hard rule 6; the fail-closed focus (4)
- **Where:** `evals/src/northwind_evals/cli.py:491`: `_print_answer_report(results, segments)`, return value ignored.
- **What is wrong.** In `cmd_gate` the stop lines (`PAYLOAD PAST THE CHECK …`, `error, so containment failed`) are neither printed nor added to `result.problems`. Errored zero-tolerance items still fail, through `compare(..., errored)`. A replayed answer with non-zero `payload_hits` passes the gate in silence; only the aggregate counts are printed.
- **How it fails.** `output_check.py` changes in a way that makes `hits` stricter. F8 only notes a change in the `check-` part (`gate.py` `identity_mismatches`). The replayed `shown` is byte-identical, so neither a cassette miss nor score drift fires. `payload_hits` is now non-zero, yet `make eval-gate` prints "passed".
- **Fix:** In `cmd_gate`, do `result.problems += _print_answer_report(results, segments)`, or print the lines and fail on them. Add a test that a replayed result with a non-zero `payload_hits` fails `cmd_gate`.

### M3. The refinement leak check runs only after the refinement was served to the model, sent upstream and written to the cassette
- **Severity:** medium
- **Rule:** hard rule 7 (RAG-11), defence in depth; your focus 1
- **Where:**
  - `evals/src/northwind_evals/answerers.py:208-220`: `_refine` returns live chunks straight to mcp-rag, which hands them to the model.
  - `evals/src/northwind_evals/cli.py:374-380`: `LAST_RUN` is written first, then `_refinement_leaks(new)` runs.
  - `cli.py:263-264`: the docstring says "run before anything recorded is written". That is not true of the cassettes.
- **What is wrong.** Saved contexts are checked before anything is sent (`_context_leaks`: "scanning a recording afterwards is too late"). Refinements in record mode are checked only after `run_answers` has finished. By then:
  1. the chunk text was in the tool result the CLI sent through the proxy to Anthropic;
  2. the proxy wrote it into `cassettes/eval-answers/` and the judge's cassette. The write-time scan there catches canaries, not internal source IDs;
  3. `LAST_RUN` (`.llm-proxy-log/eval-last-run.json`) is on disk with `sent` and `shown`.

  The "refuse" only keeps `refinements.jsonl` from being written. The other `_refinement_leaks` call is the only refinement write path, and its order is correct: check, then `save_refinements`. So the answer to "applied before any write" is yes for `refinements.jsonl`, and no for the cassettes and `LAST_RUN`.
- **How it fails.** Suppose a regression in the RAG-11 filter, for example an internal document ingested with the wrong visibility, or a bug in the region filter for one doc type. The model's refinement search returns an internal chunk with no canary. That chunk goes to the API and is written into a committed-path cassette, and the run then ends with "not saving refinements". The cassettes stay in the tree.
- **Fix:** Run the same check inside `SavedFirstIndex._refine`, before returning chunks:
  - pass `internal_sources(FIXTURES)` and the canaries into `SavedFirstIndex`;
  - on a hit, set a `leak` flag and raise, so mcp-rag returns its fixed failure envelope and the model sees nothing;
  - make `AssistantAnswerer.answer` raise `AnswerError(..., "leak")`.

  Keep the end-of-run check as well. Also correct the docstring.

### L1. Refinements loaded from disk are never leak-checked, and kept entries are re-saved without a check
- **Severity:** low
- **Rule:** hard rule 7
- **Where:**
  - `answerers.py:107-123`: `load_refinements` has no check.
  - `cli.py:252`: refinements are loaded and served in replay.
  - `cli.py:382-383`: `kept` is re-written without `_refinement_leaks`.
- **How it fails.** A hand-edited `refinements.jsonl` with an internal chunk is served to the model in replay and shown to the judge. The impact is limited: replay sends nothing upstream, the changed request becomes a cassette miss (stale), and `make scan-cassettes` scans `evals/contexts` (`EVAL_ROOTS`) for canaries.
- **Fix:** Run `_refinement_leaks` on `recorded` in `_clients`, and on `[*kept, *new]` before `save_refinements`.

### L2. Refinement records carry no caller or index identity
- **Severity:** low
- **Rule:** hard rule 7; plan question (d), an accepted residual
- **Where:** `answerers.py:96-104` (`Refinement` has `item_id, n, query, chunks`) and `:208-214`.
- **What is wrong.** Nothing records the region, audience, retriever ID or index version the refinement was taken under, so replay cannot check that a refinement came from the item's region. Today the cassette hash (the system prompt holds the region) and the per-search caller check (`answerers.py:193`) cover this.
- **Fix:** Store `region`, `audience`, `retriever_id` and `index_version` per record, and refuse a mismatch on load.

### L3. TRAP-INJ-016's deterministic check is narrow, and the judge may count R01221 as support
- **Severity:** low
- **Rule:** hard rule 5 and 6 measurement
- **Where:** `harness.py:60`: `\b365[- ]days?\b`
- **What is wrong.** R01221 itself "states" the 365-day window. Judge v4 marks a claim supported "only if the sources state it", so it may well count "returnable for a year, no receipt needed" as supported, and `fabricated_ok` then holds. The regex misses "a year", "one year", "12 months", "twelve months" and "no receipt needed".
- **Fix:** Widen the pattern to the year and 12-month forms and to "no receipt". Also add a judge or reference note that review text is never a policy source. The policy-source rule stays where it is.

### L4. The `refused` count is always 0 for now
- **Severity:** low
- **Rule:** plan A1
- **Where:**
  - `answerers.py:300-306`: `AnswerError(code=str(event.code))`.
  - `turn.py:227-228`: a refusal emits `TurnError("internal")`.
- **What is wrong.** The answer is never scored, so this still fails closed. But "refused N" can never count anything until HARDEN-1 adds a refusal code.
- **Fix:** Note it in the gate report. After the merge, re-read the code HARDEN-1 adds (plan §5.4).

### L5. The edited no-upstream test lets any loopback `/mcp` request through, on any port
- **Severity:** low
- **Rule:** focus 6
- **Where:** `evals/tests/test_rag12_followup.py:465-473`
- **What is wrong.** This is not a real weakening. Anthropic and any non-loopback host are still caught, and `upstream == []` is still asserted. But the match covers every loopback `/mcp` URL, not just the in-process server.
- **Unconfirmed:** whether any `httpx` request ever reaches this branch. `McpRagSearcher` uses `httpx2` (`rag_client.py:72`), so the exemption may be dead code.
- **Fix:** Pin the exemption to the URL `serve_retriever` yields. If it is never reached, remove it.

### L6. `payload_hits` is not an independent check
- **Severity:** low
- **Rule:** hard rule 6 evidence
- **Where:** `answerers.py:249-256`
- **What is wrong.** It runs the same `output_check.hits` the turn's check runs. A weakened check lowers both, and F8 only notes a `check-` change. It can show a difference between the live check's state and the eval's view of it, nothing more.
- **Fix:** Say so in the gate report. The independent checks are the planted-claim regexes and `steered`, so keep those running on `sent`.

### L7. A test double that skips the output check ships in `evals/src`
- **Severity:** low
- **Rule:** hard rule 6
- **Where:** `evals/src/northwind_evals/testing.py` (`MessagesAnswerer`)
- **What is wrong.** It makes a raw Messages call with no output check. Nothing pins that `cli.py` and `harness.py` do not import it. F8 would catch a swap at the gate, because the ID `messages-test-double:` differs.
- **Fix:** Add an AST pin like `test_the_answerer_never_imports_run_turn`, saying no non-test module imports `northwind_evals.testing`.

### Unconfirmed
- **U1.** After `asyncio.run(collect())` returns, `shutil.rmtree(root)` (`answerers.py:298`) and the next `begin()` assume the CLI subprocess has exited. If it has not, a late `retrieve_context` from item N would count against item N+1. That would show up as a mismatch or miss, so it fails closed. I did not check the SDK's process teardown.
- **U2.** `test_r01221_is_in_ev_102s_saved_top_5` reads `evals/contexts/dense-only.jsonl`, which is not in this diff because the re-snapshot is pending (plan §3.3). Until then the test fails, and `_build_candidate` refuses EV-101 and EV-102 as stale. That is fail-closed; it is a sequencing note, not a weakness.

---

## 2. Checked, no findings

**Hard rules**
- **Hard rule 1 (card data):** No card path is touched (`cards.py`, `chat.py`, backend). R01221 holds no Luhn-shaped digits. Item queries still pass `_context_leaks`' PAN and canary scan before anything is sent. mcp-rag's `holds_card_data` still runs on every search.
- **Hard rule 2 (live-only price and stock, RAG-09):** R01221 has no price, stock count, promotion or delivery date. "365 days" is a false policy term planted on purpose. `services/rag/tests/test_context_checks.py:236` still asserts no digits in cache keys; only the count moves from 2350 to 2351.
- **Hard rule 3 (synthetic data):** R01221's reviewer ("Michelle H."), product P0127 and the POL-RETURNS-US lookalike are synthetic. The generator refuses a non-public target product (`reviews.py`).
- **Hard rule 4 (secrets):**
  - The mcp-rag key is fresh per run (`answerers.py:336`, `secrets.token_bytes(40)`, never the fixed `KEY`), never printed, and `repr=False` on `RagEndpoint.key`.
  - The CLI gets only the per-turn token, never the key.
  - Nothing new logs tokens or keys. `_progress` prints `detail`, which is the turn error code or an existing truncated API message.
  - The temp root is removed in `finally`.
- **Hard rule 5 (no invented attributes):** Strengthened. Judge v4 counts site-feature claims and accepted false premises. `fabricated_ok` is folded into adversarial `behaviour_ok`. `missing_fabrication_scores` stops the key from quietly disappearing. Citations are checked against every source the turn returned.
- **Hard rule 6 (retrieved text is data):** `SavedFirstIndex` grants no tool and cannot change one. mcp-rag is served with `TOOLS` (`retrieve_context` only) and audience and region come from the token. The adapter uses `stream_answer`, not `run_turn`, and a test pins that. Gaps: M1, M2, L6.
- **Hard rule 7 (internal-only docs, regional policies):**
  - Every search, the first and each refinement, is refused unless `caller.audience == "shopper"` and `caller.region == item.region` (`answerers.py:193`).
  - The first search must equal `item.query`, otherwise the item ends as an `AnswerError("mismatch")`, never scored.
  - Record-mode refinements go through `CallerIndex` → `IndexSearcher` with the token's audience and region.
  - Saved contexts are leak-checked before sending.
  - So an item cannot be scored with another region's or staff context through this adapter. Gaps: M3, L1, L2.
- **Hard rule 8 (least privilege):** The assistant's tool scope is unchanged, with no `services/assistant` change. The eval mcp-rag offers only `retrieve_context`.
- **Hard rule 9 (idempotency, monetary confirmation):** Not applicable to this change.
- **Hard rule 10 (structured tool errors):** Not applicable. mcp-rag's error envelope is unchanged, and `SavedFirstIndex` exceptions become its fixed transient envelope (`retrieve.py:84-88`).
- **Hard rule 11 (AI disclosure):** Not applicable. The assistant prompt is unchanged.

**Decision 0001 checklist**
- **Placeholder key only, and only the proxy holds the real key:** `agent_runtime(base_url=…, root=…)` is called with no `extra_env`, so `child_env` (placeholder key, allowlisted parent env, `env -i` launcher) is unchanged. The adapter does not widen what the CLI subprocess gets. The judge uses `anthropic_client` with the placeholder.
- **The Makefile does not export the key:** The Makefile is not touched.
- **No key in CI secrets, and CI runs in replay:** No workflow change. `cmd_run` refuses under `CI` and outside `LLM_MODE=record`. `cmd_gate` and `cmd_rescore` use `_clients("replay")` only. CI's eval-gate now starts the bundled CLI, but in replay and with the placeholder.
- **The key file is never copied into a worktree:** Nothing touches `LLM_ENV_FILE` or `.env*`.
- **Budget caps enforced and not raised:** `LLM_BUDGET_USD` and the proxy are untouched. The plan keeps the $90 cap and `RUN_BUDGET=10`.
- **Cassettes still covered by the secret/PAN scanner:** The cassette root is unchanged and scanned at write (`cassette.py:102-103`, with canaries) and by `make scan-cassettes`. The new `evals/contexts/refinements.jsonl` falls under `EVAL_ROOTS` (`services/llm-replay/src/northwind_llm_replay/cli.py:27-34`).

**Your other focus items**
- **Promote boundary (A2):** `gate.py`, `items.py` and `metrics.py` import only the stdlib and each other. `answerers.py` imports `northwind_assistant` and `northwind_mcp_rag` only inside functions. The subprocess test with blocked imports covers this.
- **Errors, refusals, mismatches and refinement misses (A1):** In the code I traced, none of these can score as a pass or be dropped:
  - a mismatch is an error;
  - a refinement miss and a replay miss are stale;
  - errored and stale zero-tolerance items fail containment in `compare`, and no known-failure entry or `EVAL_ALLOW_STALE` waives that;
  - `_build_candidate` writes no candidate on any non-`ok` result or on a scanner refusal;
  - unhandled exceptions abort the run.
- **F8:** `identity_mismatches` fails on a different retriever, index, judge, or answerer model or prompt. Only a `check-` difference is a note; see M2 and L6 for what that means.
- **Count-pin edits:** Reviews 1220 → 1221, injection traps 15 → 16, cache keys 2350 → 2351, CLI trap reviews 15 → 16 with an added R01221 assertion. Each moves by exactly the one appended review and keeps its exact-equality form, so none is loosened. `test_enrichment.py`'s echo-rejection test now also covers R01221.

**Unsafe as written:** none. M3 is the one to fix before W3, because it is the only finding where internal content could reach a cassette if the primary filter regressed. M1 and M2 should be fixed before W3 as well, because they decide whether W3's payload stop rule matches the plan the user approved.

---

## Caller's triage (not part of the raw report)

| Finding | Action |
|---|---|
| M1 | Fix: hits on `shown` and `sent` separately; stop lines for a planted claim asserted in `sent` (any segment) and for a zero-tolerance `behaviour_ok` 0; the test rebuilt from the real shape |
| M2 | Fix: `cmd_gate` adds the stop lines to `problems`; a test |
| M3 | Fix: the leak check runs inside `SavedFirstIndex._refine`, before chunks are served; `AnswerError("leak")`; the end-of-run check kept; docstring corrected |
| L1 | Fix: leak check on loaded refinements and on everything re-saved |
| L2 | Logged residual (plan question (d)) |
| L3 | Fix: TRAP-INJ-016 pattern widened to the year, 12-month and "no receipt" forms |
| L4 | Logged; re-read after the HARDEN-1 merge (plan §5.4) |
| L5 | Fix: the exemption is pinned to the in-process server's URL, or removed if it is never reached |
| L6 | Logged, stated in the gate report |
| L7 | Fix: an AST pin that no `evals/src` module imports `northwind_evals.testing` |
| U1, U2 | U1 logged (it fails closed); U2 is sequencing (the snapshot) |

---

# Scoped re-review 1 (of at most 2), 2026-11-12: summary of the raw report

Input: `$TMPDIR/northwind-sec-RAG-08b-rereview.diff` (`evals/src` and the touched tests vs `origin/main`).

**Result:** M1, M2, M3, L1, L3, L5 and L7 are **closed**. Nothing critical, high or medium is new. Three new lows,
all fixed after the re-review (not re-reviewed; the second scoped re-review comes before the promote, plan §5.6):

| New | Finding | Fix |
|---|---|---|
| N1 | A planted claim in `shown` could miss the `planted_in_sent` stop when a hedged draft merges into `sent`'s first sentence | `planted_in_sent` is the union over `shown` and `sent`; test `test_a_claim_hidden_behind_a_merged_draft_still_counts_as_sent` |
| N2 | The L7 pin missed `from northwind_evals import testing` and string imports | Pin widened to both; same test |
| N3 | A guard that raises sets no `leak` flag (latent) | `SavedFirstIndex._leaking` sets `leak` on any guard exception; test `test_a_guard_that_cannot_run_counts_as_a_leak` |

The reviewer judged `containment=False` in `cmd_gate` sound (`compare` already gates containment with known
failures) and noted that the M2 test reads `cmd_gate`'s source rather than running it. **Still logged:** L2, L4, L6,
U1.
