# Row #15 security review, pass 1 (verbatim report of `security-reviewer`, 2026-11-13)

Diff reviewed: `/tmp/claude-502/northwind-security-15.diff` (uncommitted worktree against base `286680f`).

---

## Security review: row #15 "A2A-05 prep" (diff /tmp/claude-502/northwind-security-15.diff, base 286680f)

I read CLAUDE.md "Hard rules" and decision 0001 again for this review. Where the diff did not show enough context, I read the code this row calls: `SavedFirstIndex`, `contexts.leaks`, `cli._chunk_guard`, the proxy's `ProxyState`/`handle`/`_record`, the `llm_proxy` fixture, `CachedEmbedder`, `CallerIndex`/`serve_retriever` and `agent_runtime`.

**Scope verified.** The diff headers and the session's git status match. The row adds new files only, under `cassettes/a2a-05-partner/` (9), `evals/fixtures/a2a-05-partner/` (script, digest, 9 answers), `evals/tests/` (2 files) and `docs/reviews/` (3). It also edits text in `PROGRESS.md` and `COVERAGE.md`. Nothing under `services/**`, `evals/src/**`, the `Makefile`, `.github/**`, `.claude/**` or `CLAUDE.md` changes, and no file is untracked.

**Overall: no critical, high or medium findings. There are 9 low findings.** The fail-closed order in the record branch is sound: the index check, then the first-search guard, then the turn, then the upstream check, then checks 1–8 on a staged copy in `tmp_path`. Only after all of that is anything written to `answers/`.

---

## 1. Findings (most severe first; all low)

**L1. The fixture is tied to its cassettes only by test_id, so leftover cassettes are picked up and stale fixtures are not tied to anything.** Files: `evals/tests/test_a2a05_partner.py:295-304` (`recorded_calls`), `:565` and `:583`. Rule: hard rule 5 (where the answer came from), plus the cassette hygiene in decision 0001 §3.
- `recorded_calls(name)` reads every cassette in the suite whose `test_id` matches. It does not check that the cassette was made by this run.
- **How it fails, case 1:** a record attempt fails check 2 (for example, the model emitted a promotion). The cassette that holds that text is already written to `cassettes/a2a-05-partner/`, because the proxy writes it during the turn. The fixture is not written. If the model input then changes and the turn is recorded again, both cassettes match. The leftover text is then:
  - joined into `model_text` and into the fixture's `upstream_calls`;
  - committed as an added cassette that no fixture uses, holding text the output check blocked. The scanner only looks for keys, PANs and canaries, so it does not catch this.
- **How it fails, case 2:** once `assistant_id` goes stale (expected after #12 or #16), nothing that always runs links `answer.shown`/`sent` to the cassettes, apart from the disclosure flags. A hand-edited answer that still uses valid citations and contains no money or "done" wording passes every remaining check.
- Today it is clean: 9 cassettes for 9 fixtures, one call each.
- **Fix:**
  - store each call's cassette hash in `upstream_calls`. `state.calls` entries already carry `hash`; the full hash is the cassette file name.
  - have `recorded_calls` select by those hashes;
  - add an always-on check that each listed cassette exists, and that the cassettes for this test_id are exactly the listed ones;
  - in record mode, fail if cassettes for this test_id exist that this run did not make.

**L2. `upstream_problems` accepts a stale (sequence-matched) response.** File: `test_a2a05_partner.py:495`. Rule: decision 0001 §3, matching ("marks results stale").
- The list that must be empty is `refusals`, `fallback_attempts`, `scan_failures` and `misses`. It leaves out `state.stale`.
- **How it fails:** with `LLM_REPLAY_MATCH=sequence` set during `make record`, the proxy's `store.get(h)` misses. It then serves an older cassette by test_id and order, with outcome `"hit"`, and `upstream_problems` accepts it. A fixture is written whose answer came from different model input than its saved `context`.
- The replay branch passes the same way, with only a warning.
- **Fix:** add `"stale"` to that list. Also refuse a record or replay run when `LLM_REPLAY_MATCH` is not `hash`.

**L3. The upstream check fails the turn after it finishes; it does not stop the first unexpected request.** Files: `test_a2a05_partner.py:477-498` and `:562-564`. Rule: decision 0001 spend guard; plan §3.3 step 4.
- The check runs after `run_partner_turn` returns. By then:
  - an unexpected `/v1/messages` call (for example a non-assistant model) has already gone upstream and been billed;
  - it has also been recorded as a cassette.
- What does stop at the request:
  - paths other than `/v1/messages` and `count_tokens`, refused by the proxy (`proxy.py:456`);
  - the budget caps.
- The fixture is never written, so the fixture side fails closed. The cassette side does not.
- Unconfirmed: whether the runner's gate refuses other models for this suite.
- **Fix:**
  - describe the behaviour accurately in the docstring and plan ("the turn fails; no fixture is written");
  - with L1's fix, fail when the run produced cassettes beyond what was expected.

**L4. A refinement that fails for a reason other than the leak guard raises no flag, so the fixture is still written.** Files: `test_a2a05_partner.py:472` (flags) and `:563`; behaviour in `answerers.py:221-227`. Rules: hard rules 5 and 9 (fail-closed).
- If `live.search` raises inside a refinement (a DB error, a read-only violation, an embedder error):
  - `leak`, `mismatch` and `miss` all stay False;
  - the model gets the fixed tool failure and still answers;
  - the fixture is written without that refinement.
- The e2e replay later catches it as a miss, but the always-on checks do not, and stale fixtures skip the replay.
- **Fix:** in the recorder, count tool failures. For example, wrap `live` so any exception sets a flag that `TurnResult.flags` reports, and fail on it.

**L5. The checks for hard rule 2 do not cover chunk text and only find currency symbols.** Files: `test_a2a05_partner.py:68-70` (`MONEY`), `:228-235` (`check_money`), and fixture `context`/`refinements`. Rule: hard rule 2 (RAG-09).
- Check 6 scans only `answer.shown` and `answer.sent`. The stored chunk texts, which are copies of the index and go into model context on replay, are not checked for exact prices or stock counts.
- The plan relies on "the index holds none (RAG-09)", but #12 RAG-09 is still `todo`.
- When stale, check 2 (the output-check engine) skips. Check 6 is then the only rule-2 check left, and it misses:
  - "129 dollars";
  - stock counts ("3 left");
  - delivery dates.
- Today it is clean: I searched every context and answer for currency, stock counts and delivery wording and found none.
- **Fix:** run check 6 over `chunks_in(fixture)` as well. Add stock-count and date patterns until #12's money class replaces them.

**L6. The fixture loader does not enforce the two rules A2A-05 must follow.** File: `test_a2a05_partner.py:111-119` (`partner_fixture`). Rules: hard rules 8 and 9 and NFR-03; decision 0002 notes (2) and (3).
- `partner_fixture("oos-01-refund")` returns the `never_served` fixture.
- It returns a stale `assistant_id` without complaint.
- The safe name format (`NAME`) is enforced only by a test (`:640`), not by the loader.
- The loader is meant to move into the A2A package, and none of these protections move with it. A later `answer_for(name)` built on it would serve `oos-01` or stale text unless A2A-05 adds the checks again.
- Nothing is served today.
- **Fix:** add a serving entry point, say `served_fixture(name, current_id)`. It should:
  - check `NAME.fullmatch`;
  - refuse `a2a05_use == "never_served"`;
  - refuse a non-current `assistant_id`;
  - return `narrative_only` text only together with an order artifact.

  Keep `partner_fixture` for tests only.

**L7. The AI disclosure for A2A (hard rule 11) is deferred, and nothing will fail if A2A-05 forgets it.** Files: `test_a2a05_partner.py:257`; `pq-02` to `pq-07`, where `disclosure` is all false. Rule: hard rule 11 (NFR-08).
- 6 of the 7 `answer` fixtures carry no disclosure. That is by design: the prompt says to disclose "if asked".
- Check 8 requires disclosure only on `NAMES[0]`.
- Check 8 decides on the whole text: any "AI" anywhere in `shown` satisfies it. The plan said a disclosing sentence must survive to `shown`, which is a per-sentence test.
- Disclosure at the protocol level is recorded in PROGRESS for A2A-05, but no test fails if it is left out.
- **Fix:** add a pending A2A-05 acceptance item: the agent card and message metadata state that the agent is an AI, tagged NFR-08. Make check 8 per sentence, as the plan describes.

**L8. The index check and the search are not one atomic step, and read-only is only a session default.** Files: `test_a2a05_partner.py:339-348` and `:552-557`. Rule: change 1 (no writes to the shared Postgres).
- `index_problems()` runs before the first search. Refinements later in the turn read `is_current` again on the shared DB. Window A, which owns `services/rag/**` and `data/**`, could publish a new index between the check and the search.
- `default_transaction_read_only=on` can be overridden by `SET`/`BEGIN READ WRITE` in the same session. No code in the search path does that; I found writes only in the ingest code in `store.py`.
- **Fix:** run `index_problems()` again after the turn, and fail if the manifest changed. Optionally connect as a role with SELECT rights only.

**L9. One phrase in the `narrative_only` order fixture could be read as a stock claim.** File: `evals/fixtures/a2a-05-partner/answers/order-01-breakwater.json:10-11`. Rules: hard rules 2 and 9.
- The text says "The option you described is available in the sources". A partner agent parsing the text could read "available" as in stock.
- Otherwise the fixture is safe: "I can't place orders", it sends the shopper to the live page for price, stock and delivery, and it makes no order claim. Check 7 passes.
- **Fix:** none needed now, since A2A-05 takes the order outcome only from tools. When RECORD-ALL re-records it, make sure check 7 or the output check covers "available" next to a variant.

---

## 2. Checked, no findings

### Hard rules
- **HR1 (card data):** no PAN or CVV in the script, fixtures, cassettes or evidence. The only card-shaped string is the sandbox test number `4242 4242 4242 4242` inside a mutant in the test file (`MUTANTS["5-card-number"]`). It is written only to `tmp_path`. Check 5 (`scan_text`) runs on every fixture file, and `make scan-cassettes` covers `cassettes` and `evals/fixtures` (per the evidence).
- **HR2:** no exact price, stock, promotion or delivery date in any answer or chunk. Answers send the shopper to the live page. (See L5 for the coverage gap.)
- **HR3:** synthetic data only. `NW-100245` is a synthetic order ID the user approved (Q4). Every product is from `data/fixtures`.
- **HR4 (secrets):** no key shapes, `x-api-key`, `Authorization` or `.env` reads. Cassettes store only `anthropic-version` and `anthropic-beta` headers, and `metadata.user_id` is scrubbed.
- **HR5:** every citation in all 9 answers is a source of that turn (check 3 and the criteria test). The claims I spot-checked appear in their sources.
- **HR6:**
  - the cassettes show one tool only, `mcp__northwind-rag__retrieve_context`;
  - the system prompt labels the sources;
  - there are no refinements, so no retrieved text granted any tool;
  - there was no prompt-injection text in the diff, the fixtures or the cassettes.
- **HR7:** every context source is public, and regionally correct for US.
  - Documents: `POL-RETURNS-US` (US), `FAQ-007`, `FAQ-016` and `SG-BEDDING` (ALL).
  - All 31 products in the contexts: public and sold in the US.
  - The first-search guard (`:559`) runs before anything else. The guard on refinements (`answerers.py:207`, with a leaking result not added to `new` at `:225`) fails closed. The guard is the same as `cli._chunk_guard`.
  - The replayed contexts are covered by checks 4 and 5.
- **HR8:** the assistant has no refund, payment or order tool (cassette `tools`). `oos-01-refund` declines ("I can't issue refunds"). Its "Refunds are issued within 5 business days" is quoted policy with a citation, not a claim that a refund was made.
- **HR9:** this row adds no state-changing tool. The `never_served` and `narrative_only` fixtures make no claim that an order or refund was completed (check 7). (See L6 and L9 for what A2A-05 needs.)
- **HR10:** not applicable to this change. No tool was added or changed.
- **HR11:** `pq-01` (asked), `oos-01` and `order-01` disclose in `shown`. No turn had a disclosure removed by the output check. (See L7 for what is deferred.)

### Areas you asked about
- **`skip_if_stale`:** a missing or malformed id fails, and never skips (`:129-136`). Committed ids must match the full format (`test_every_recorded_assistant_id_is_well_formed_and_shared`). The AST check limits the skip to the two intended tests, and checks 1 and 3–8 run without a skip in `make test`. (See L1 for the stale-binding gap.)
- **`partner_fixture`:** a name not in the script is refused before any path is built. `../script`, uppercase and partial names are tested. A file whose content names another turn is refused. (See L6 for what the loader should enforce when it moves.)
- **Recorder DB access:** `index_problems` checks `data_version` 2, the `389cae01` prefix and the dataset digest, failing closed. `CachedEmbedder` only reads `data/embeddings`. (See L8.)

### Decision 0001, key handling and spend
- **Placeholder key only in child processes; only the proxy holds the real key:** the recorder uses `northwind_common.llm.agent_runtime` (the `child_env` factory). No direct `anthropic.Anthropic` or `ClaudeAgentOptions` is built.
- **Makefile does not export the key:** the Makefile is unchanged.
- **No key in CI secrets; CI runs in replay:** `.github/**` is unchanged. The plugin forces replay under `CI`, and the e2e replay is set by `LLM_MODE=replay` in `make e2e`.
- **Key file never copied into a worktree:** nothing in the diff touches `.env*` or `LLM_ENV_FILE`.
- **Budget caps enforced and not raised:** `RUN_BUDGET=3` was used for each of the two windows the user opened, with no change to `LLM_BUDGET_USD` ($90). The suite cost $0.046485 (ledger $4.801024 → $4.847509).
- **Cassettes still covered by the secret/PAN scanner:** the new suite is under `cassettes/`, which `make scan-cassettes` scans in `make ci`, and check 5 runs `scan_text` on each fixture.

### Relevant files
- /Users/rapido/Projects/northwind-shop-b/evals/tests/test_a2a05_partner.py
- /Users/rapido/Projects/northwind-shop-b/evals/tests/test_a2a05_partner_criteria.py
- /Users/rapido/Projects/northwind-shop-b/evals/fixtures/a2a-05-partner/answers/order-01-breakwater.json
- /Users/rapido/Projects/northwind-shop-b/evals/fixtures/a2a-05-partner/answers/oos-01-refund.json
- /Users/rapido/Projects/northwind-shop-b/evals/src/northwind_evals/answerers.py (lines 147-246, read only)
- /Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/proxy.py (lines 150-160 and 417-505, read only)

---

# Pass 2, scoped re-review (verbatim report of `security-reviewer`, 2026-11-13)

Diff reviewed: `/tmp/claude-502/northwind-security-15-pass2.diff`.

## Security re-review, pass 2: row #15 "A2A-05 prep"

**Input:** diff `/tmp/claude-502/northwind-security-15-pass2.diff`, base `286680f`, uncommitted worktree.

**What I read:** I re-read CLAUDE.md "Hard rules" and decision 0001 for this pass. To check each fix I also read the code it depends on:
- `proxy.py` `handle`/`_record`: hash prefixes, `stale`, outcomes, `FREE_PATHS`;
- `pytest_plugin.llm_proxy`: function-scoped, reads `LLM_REPLAY_MATCH` the same way the test does;
- `answerers.SavedFirstIndex`;
- `mcp_rag.retrieve.retrieve_context`;
- `CallerIndex`;
- `integrity.py`.

I ran nothing; I have no shell. Line numbers below are in the worktree file and match the diff.

**Overall:** no critical, high or medium findings. Six of the nine fixes close their finding: L2, L3, L5, L7 and L9 fully, L8 with one gap (N4). L1, L4 and L6 are only partly closed. There are 5 new low findings, N1 to N5. Nothing in the diff, fixtures or plan tries to steer the review.

---

## 1. Findings (all low)

**N1. Check 9 links a fixture to its cassettes by count, model and stop reason, not by cassette identity. So L1 case 1 survives in the always-on checks.** Rule: hard rule 5 and decision 0001 §3 (cassette hygiene).
- Where: `evals/tests/test_a2a05_partner.py:358-366` (`check_cassettes`) and `PROGRESS.md:156` (the L3 residual's "clears `cassettes/a2a-05-partner/`").
- Plan §9 says check 9 "binds each fixture to exactly its cassettes". It does not: it compares `{model, stop_reason}` per call.
- **How it fails:**
  1. RECORD-ALL follows the logged residual and clears the suite. `integrity.py:6` allows deleting cassettes.
  2. It re-records `pq-02`. The turn writes cassette C2, then fails check 2 (say, a promotion). The fixture is not rewritten, so the old F1 stays.
  3. Now F1 sits beside C2: one call, the same model, `end_turn`. Check 9 passes, and `orphan_cassettes` passes because C2 has the right test_id.
  4. F1's `assistant_id` is stale, so the e2e replay and check 2 skip.
  5. The suite is green while C2, holding text the output check blocked, is committed as an added cassette that no fixture was made from. The scanner does not catch it: it is not a key or a PAN.
- `run_cassette_problems` closes this only inside the recorder's own run, not in `make test`/CI.
- **Fix:**
  - at RECORD-ALL, have the recorder write each call's full cassette hash into `upstream_calls`, and have check 9 compare hashes;
  - log this as a residual until then.

  Committed fixtures cannot be hand-edited to add the hashes, so this waits for a re-record.

**N2. `served_fixture` takes `a2a05_use` from the fixture file, not from the frozen script, and checks neither digest.** Rules: hard rules 8 and 9, NFR-03; decision 0002.
- Where: `test_a2a05_partner.py:149-156`.
- **How it fails:** someone sets `oos-01-refund.json`'s `a2a05_use` to `"answer"`. Its `assistant_id` is still current, so `served_fixture("oos-01-refund", current)` returns the refusal text, which a `never_served` fixture must never do.
- In this repo, check 1 (`check_identity`) catches it in `make test`. But `served_fixture` is the piece PROGRESS (`PROGRESS.md:129`, note 1) says moves into the A2A package, and check 1 does not move with it. That is the gap pass 1 L6 described.
- **Fix:** inside `served_fixture`:
  - read the use from `turn_named(name)["a2a05_use"]`;
  - require `check_identity(fixture, turn, script_sha256()) == []`;
  - require `script_sha256()` to equal `script.sha256`.

**N3. Serving the order narrative needs only a boolean the caller sets, and it returns the text that PROGRESS note (4) says must not go with the artifact.** Rule: hard rule 9; PROGRESS note (4).
- Where: `test_a2a05_partner.py:140,153-154`.
- `with_order_artifact=True` is a claim, not a check: no artifact is passed in or checked.
- **How it fails:** #16 is dropped. RECORD-ALL re-records `order-01`, whose text still says "I can't place orders", and it is current. A2A-05 calls `served_fixture("order-01-breakwater", current, with_order_artifact=True)`. It gets that text and returns it beside an artifact that shows a placed order, the contradiction note (4) forbids.
- **Fix:**
  - take the artifact itself as the argument and return both together;
  - refuse a `narrative_only` fixture whose turn had no cart or order tool. The cassette request `tools` list today holds only `mcp__northwind-rag__retrieve_context`.

**N4. L8's before/after identity check is not tied to the check it guards (TOCTOU, time-of-check to time-of-use).** Rule: change 1 (record only against main's DATA_VERSION 2 index).
- Where: `test_a2a05_partner.py:676-679` and `:688`.
- `index_problems()` and `before = index_identity()` open two separate read-only connections.
- **How it fails:** Window A publishes a new index between line 676 and line 679. `before` then holds the new, unvalidated identity, the after-turn comparison at `:688` matches it, and the turn records against an index that change 1 never approved.
- The window is milliseconds, so this is unlikely.
- **Fix:** take `before` from the same manifest read as `index_problems()`, or run `index_problems()` again after the turn, as pass 1 suggested, alongside the identity comparison.

**N5. L4 is closed only for failures inside `live.search`. A refinement refused after a successful search is still recorded with no flag.** Rules: hard rules 5 and 9 (fail-closed).
- Where: `test_a2a05_partner.py:476-490` and `:583`; behaviour in `answerers.py:221-227` and `services/mcp-rag/src/northwind_mcp_rag/retrieve.py:91-96`.
- `SavedFirstIndex._refine` adds the refinement to `index.new` as soon as `live.search` returns and the leak guard passes. `retrieve_context` then calls `render_sources`, which can raise `ValueError` (an unclassified source type or a malformed ID). The model gets the fixed `SOURCE_REFUSED` failure, but `CountingSearch.failed` stays False.
- **How it fails:** the index gains a chunk with a bad source type. The recorder writes a fixture whose `refinements` hold chunks the model never saw. Check 3 would accept a citation to their source IDs. Replay does the same thing again in the same way, so the e2e test does not catch it either.
- Plan §9's "flags any refinement failure" overstates the fix.
- **Fix:** in record mode, fail the turn on any `tool_result` with `is_error` in the turn's requests. The cassette `match.body` holds them. This covers `SEARCH_FAILED`, `SOURCE_REFUSED`, `SEARCH_LIMIT` and validation errors.

**N6 (low, test strength). `test_only_a_current_answer_turn_is_servable` stops testing most of what it is meant to once the fixtures go stale.**
- Where: `test_a2a05_partner.py:1143-1167`.
- After #12, #16 or #17, every committed fixture is stale. The loop then only asserts `NotServable` for staleness, so the `answer` success branch and the `narrative_only`-without-artifact refusal are never run, and the test stays green. (`never_served` is still refused, because that check comes before the staleness check.)
- **Fix:** test `served_fixture` on hand-made fixtures under `tmp_path` with a current id, as the mutants do. That needs an `answers` parameter, or a monkeypatched `ANSWERS`.

---

## 2. Status per pass-1 finding

- **L1: partly closed.**
  - Closed in the recorder: `run_cassette_problems` (`:379-389`) requires the turn's cassettes to be exactly this run's hashes.
  - Hash-prefix matching is correct: the 12-character log prefix is matched against the full cassette hash, in the right direction.
  - Repeated identical requests are deduplicated by `set`.
  - A call with no hash, an empty run and an extra cassette all fail closed.
  - A non-SSE (count_tokens) cassette raises in `recorded_calls`, which also fails closed.
  - `orphan_cassettes` covers foreign test_ids.
  - The always-on check 9 is weaker than plan §9 says: see N1.
  - Case 2 is closed for serving by the stale refusal in `served_fixture`.
- **L2: closed.**
  - `"stale"` is in the list at `:609`.
  - `LLM_REPLAY_MATCH` is refused at `:649`, before any call, and it is the same variable the plugin reads (`pytest_plugin.py:98`).
  - Any value other than `hash`, including an empty one, fails.
- **L3: closed.**
  - The docstring (`:590-593`) now says what happens: the call is already made, and no fixture is written.
  - Minor inaccuracy: "the proxy has refused any other path" leaves out `/v1/messages/count_tokens`, which is in `FREE_PATHS`, forwarded upstream for free and recorded. `upstream_problems` and `recorded_calls` still fail on it.
- **L4: partly closed.** `CountingSearch` re-raises and sets the flag, and `SavedFirstIndex` calls `search(query, caller)` positionally, so the wrapper is always used. Not covered: N5.
- **L5: closed for the shapes pass 1 named.**
  - Chunk text is scanned at `:266-277`.
  - Spelled money, stock counts and delivery dates are added, each with a mutant.
  - The committed fixtures still pass: the only hit in them is the `recorded` date, which check 6 does not scan.
  - Unlogged residual: number words ("three left", "a hundred dollars"), "arrives Friday" or "tomorrow" without by/on, and `MM/DD` dates. Log these in Open items until #12's money and stock classes land; do not loosen the patterns.
- **L6: partly closed.**
  - Name format, unknown names, `never_served`, staleness and an order narrative without an artifact all fail closed.
  - No text leaks on an error path: `NotServable` messages hold only the name. A chained `JSONDecodeError` keeps the document in `.doc`, which matters only if a future handler serializes the exception's cause (unconfirmed, not a finding).
  - A malformed fixture (not a dict, or no `answer`) raises `AttributeError`/`KeyError` instead of `NotServable`. That still fails closed.
  - Open: N2, N3 and N6.
- **L7: closed.**
  - `check_disclosure` (`:292-313`) needs each disclosing sentence of the model text to appear whole in `shown`, so another "AI" on screen does not hide a replaced sentence. The mutant `8-disclosing-sentence-replaced` proves it.
  - The split cannot hide a replacement: each fragment must be a substring of `shown`, and a reset to the fixed reply fails the check.
  - With no cassettes the loop would pass vacuously, but check 9 then fails, since `upstream_calls` is never empty.
  - Doc nit: plan §3.2 and §5(c) still say `in_sent`; the code uses `in_model_text`, which is stricter.
- **L8: closed with one gap (N4).** The identity is re-read after the turn (`:688`) and before anything is written.
- **L9: closed (logged).** The quoted text is in `order-01-breakwater.json:10-11`.

## 3. Logged residuals (`PROGRESS.md:156`)

- **L9: accurate.**
- **L8: accurate.** `options="-c default_transaction_read_only=on"` is a session default, and #9a (PROGRESS row 70) owns the least-privilege DB role. Worth adding:
  - #9a runs after #18, so RECORD-ALL also records under the session default;
  - the N4 gap.
- **L3: accurate in effect.** `orphan_cassettes` catches only cassettes with a foreign test_id. A leftover from the recorder's own test_id is caught by check 9's count check, and only while a fixture exists; otherwise `test_exactly_one_fixture_per_turn` fails. The instruction to clear the directory before re-recording opens the N1 swap case, which neither check catches.

---

## 4. Checked, no findings

Scoped to the pass-2 diff:
- **HR1:** the only card-shaped string is the sandbox `4242…` in a mutant written to `tmp_path`. Check 5 is unchanged.
- **HR2:** check 6 widened (L5). No price, stock count or date in any answer or chunk.
- **HR3:** synthetic data only. No new data.
- **HR4:** no key, header or `.env` access added.
- **HR5:** check 3 unchanged. See N1 and N5.
- **HR6:** no retrieved text grants a tool. No injection text in the diff, fixtures or plan.
- **HR7:** check 4 and the guard order are unchanged. The first-search guard still runs before the turn.
- **HR8:** the assistant's tools are unchanged. `never_served` is refused, with N2's caveat.
- **HR9:** no state-changing tool added. See N3.
- **HR10:** not applicable to this change.
- **HR11:** per-sentence check closed (L7). A2A-05's protocol-level NFR-08 test is recorded at `PROGRESS.md:129`, note 5.
- **0001, placeholder key in children only; only the proxy holds the real key:** the recorder still builds through `agent_runtime`. Nothing under `services/**` changed.
- **0001, Makefile does not export the key:** Makefile unchanged.
- **0001, no key in CI secrets; CI runs in replay:** `.github/**` unchanged. The new refusal at `:649` adds to replay-by-hash.
- **0001, key file never copied into a worktree:** not touched.
- **0001, budget caps enforced and not raised:** not touched. Pass 2 made no live calls.
- **0001, cassettes still scanned:** the suite stays under `cassettes/`, and check 5 is unchanged.
- **Fixtures or cassettes written before a check runs:** cassettes are necessarily written during the turn. Every new check (stale, `run_cassette_problems`, index identity) runs before the staged fixture is copied to `answers/` (`:726`).

## Files
- /Users/rapido/Projects/northwind-shop-b/evals/tests/test_a2a05_partner.py
- /Users/rapido/Projects/northwind-shop-b/PROGRESS.md (lines 129, 156)
- /Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-13-15-plan.md (§9)
- /Users/rapido/Projects/northwind-shop-b/evals/src/northwind_evals/answerers.py (lines 214-227, read only)
- /Users/rapido/Projects/northwind-shop-b/services/mcp-rag/src/northwind_mcp_rag/retrieve.py (lines 84-96, read only)
- /Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/proxy.py (lines 69-77 and 359-438, read only)
- /Users/rapido/Projects/northwind-shop-b/evals/src/northwind_evals/integrity.py (line 6, read only)

---

# Pass 3, second and last scoped re-review (verbatim report of `security-reviewer`, 2026-11-13)

Diff reviewed: `/tmp/claude-502/northwind-security-15-pass3.diff`.

I found no critical, high or medium problems in pass 3. Under your rule nothing stops the row, and the three lows below can be logged as residuals. N1–N6 are all closed, and so is the residual wording for L5 and L8. I ran nothing because I have no shell. Line numbers are from the worktree file and match the diff.

I re-read CLAUDE.md's hard rules and decision 0001 for this pass, and read the code the fixes depend on:
- a real cassette (`cassettes/a2a-05-partner/0f4b65…json`)
- `services/mcp-rag/src/northwind_mcp_rag/server.py` (`call_tool`, `error_result`) and `retrieve.py:70-97`
- `services/assistant/src/northwind_assistant/turn.py` (`MAX_TURNS`, `error_max_turns`)
- `northwind_evals.answerers.assistant_id`
- PRD §7 tool scoping and the #9a/#18 order in PROGRESS.md
- one CLI-recorded `is_error` tool result in `cassettes/backend-replay/`

Nothing in the diff, plan or PROGRESS tries to steer the review.

## 1. Per pass-2 item

**N1: closed.**
- `call_record` (`evals/tests/test_a2a05_partner.py:403-410`) writes the cassette's own `hash` field. Real cassettes carry the full sha256 there.
- In `check_cassettes` (`:413-431`), a mix of hashed and unhashed entries fails (`:421-422`). When the entries are hashed, `found` carries the real hash and must equal `listed` exactly. A `"cassette": null`, an extra key or a non-dict entry all fail.
- An empty `upstream_calls` still fails (`:427`).
- The swap and mixed tests cover the comparison. The recorder writes the hashes at `:780`.
- The check does not pass vacuously. The ratchet gap is P3-3 below.

**N2: closed.**
- `served_fixture` (`:143-179`) takes the use from `turn["a2a05_use"]` (`:170`), compares the script against `script.sha256` (`:160-162`), and requires `check_identity` to return no problems (`:164-169`). That covers the fixture's `script_sha256`, its message and its `a2a05_use`.
- No exception path serves text. The two `return` statements (`:176`, `:178`) come only after every check.
- Some exceptions escape as raw exceptions, not `NotServable`: an `OSError` from the digest read, and `ValueError`/`KeyError` from `offered_order_tools` → `recorded_calls`. They raise instead of serving, so they fail closed. A2A-05 should treat any exception as a rejection.
- `answer["shown"]` at `:176` is safe outside the `try`, because `check_identity` has already required a dict with a non-empty `shown`.

**N3: closed as specified.** The artifact must be non-empty and the turn's requests must offer a matching tool. With today's cassettes (only `mcp__northwind-rag__retrieve_context`), the narrative is never served. The way `ORDER_TOOL` matches is P3-1 below.

**N4: closed.**
- `index_state` (`:497-506`) takes the problems and the identity from one `index_manifest` read.
- `before` comes from the same read that passed change 1 (`:744`).
- After the turn, a new problem or a different identity fails the turn (`:755-757`). That includes no published index, because then the problems list is non-empty.
- `data_version` is not in the identity tuple, but it is in the problems list on both reads.

**N5: closed.**
- Every mcp-rag tool failure (validation, `SEARCH_LIMIT`, `SEARCH_FAILED`, `SOURCE_REFUSED`, permission, and the catch-all) goes through `call_tool` → `error_result(is_error=True)` (`server.py:121-129,189-200`).
- The CLI's next request carries that as a `tool_result` block in a user message's content list, with `is_error: true` at block level. I confirmed this in a `backend-replay` cassette, and it is the shape `tool_errors` reads (`:390-400`).
- When content is a string, the message holds no tool results, so skipping it is correct.
- `tool_errors` is used in check 9 (`:429`) and in the recorder (`:758`).
- Unconfirmed, no action needed: at the turn cap (`turn.py:183`, `error_max_turns` is not an error), a tool result from the last allowed call never reaches a later request, so `tool_errors` cannot see it. The shopper is then shown the fixed turn-cap reply, which cites nothing, so nothing it would let through is served.
- `all(tool_errors(name) == 0 …)` over the real cassettes passes trivially, because every recorded turn is one call with no tool result. That is fine: the `tmp_path` test proves detection on the real CLI shape.

**N6: closed.**
- `_served_setup` (`:1252`) builds fixtures under `tmp_path` against a fixed `CURRENT` id, so the tests do not depend on the committed fixtures staying current.
- Positive controls exist for turn 0 (served) and turn 8 (served once a cart tool is offered). So the refusals for turn 8 without an artifact, or with retrieval only, are refused for the intended reason.
- Optional: the turn-7 refusal tests check the exception type only. `match=` on the message would pin the reason.

**Residual wording: closed for L5 and L8; N1 is accurate.**
- `PROGRESS.md:156`: the N1 entry is accurate, and it rests on a manual step (see P3-3). The L5 entry matches what pass 2 listed. The L8 entry is accurate: PROGRESS rows 19-20 and 70 put #9a after #18.
- `PROGRESS.md:129` (A2A-05 note 1) overstates the order-tool condition (see P3-2).

## 2. Findings (all low)

**P3-1 (low): `ORDER_TOOL` matches a substring of the full MCP name and checks that a tool was offered, not used.**
- Where: `evals/tests/test_a2a05_partner.py:140` (`ORDER_TOOL = re.compile(r"cart|order", re.IGNORECASE)`) and `:380-387` (`offered_order_tools`).
- Rules: hard rule 9; PROGRESS note (4).
- What is wrong: `re.search` runs over `mcp__<server>__<tool>`, so every tool on any server whose name contains "orders" or "cart" counts. PRD §7 gives the Shopper Assistant `get_order` (own orders, read-only) on `northwind-orders`, and a read-only cart view would count too.
- How it fails:
  1. #16 is dropped, and a later row adds `get_order` for order status. The system prompt is unchanged, so `assistant_id` does not change: it hashes only the model, the prompt module and the output-check module, not the tool list.
  2. RECORD-ALL re-records `order-01-breakwater`, and its text still says "I can't place orders".
  3. Every call now offers `mcp__northwind-orders__get_order`, so `offered_order_tools` returns True.
  4. `served_fixture(..., order_artifact=placed)` returns that text beside a placed-order artifact, the contradiction note (4) forbids.
  5. Even with real cart tools offered, the model may not have used them.
- Fix:
  - match a frozen set of order-placing tool names (the cart add tool, `create_order`) on the tool part after the last `__`;
  - also require a `tool_use` of one of them in the turn's recorded responses.

**P3-2 (low, wording): the docs promise a stronger order-tool check than the code makes.**
- Where: `served_fixture`'s docstring (`:155`, "only if its turn was answered with cart or order tools") and `PROGRESS.md:129` ("whose turn was not answered with cart/order tools").
- What is wrong: the code checks that the tools were offered. Plan §10 and the docstring of `offered_order_tools` (`:381`) say "offered", which is correct.
- How it fails: an A2A-05 implementer moves `served_fixture` relying on "answered with" and adds no check of their own.
- Fix: make P3-1's `tool_use` check true, or change both texts to "offered".

**P3-3 (low): N1's all-or-none rule is per fixture, so the fallback to the weak binding has no ratchet.**
- Where: `:420-422`, plus the manual step at `PROGRESS.md:156` ("RECORD-ALL must confirm every fixture carries `cassette`").
- Rules: hard rule 5; decision 0001 §3 (cassette hygiene).
- How it fails: pass 2's N1 scenario, at RECORD-ALL:
  1. RECORD-ALL clears the suite and re-records.
  2. Eight turns get fixtures with hashes. `pq-02` fails check 2 and leaves cassette C2, while its old unhashed fixture F1 stays.
  3. F1 against C2 still passes check 9 on count, model and stop reason.
  4. Only the manual step stands between that and a committed cassette holding text the output check blocked.
- Fix: a suite-level test that fails when any fixture's `upstream_calls` carry `cassette` and another fixture's do not. It passes today, because none carry one, and catches this case automatically.

**Outside pass-3 scope (noted only, not counted):** `served_fixture` does not take the caller's region (hard rule 7). The fixtures are US-only, because `check_identity` pins them to the script's `REGION`, so A2A-05 must refuse partners from any other region or record per region.

## 3. Checked, no findings (scoped to this pass)

- **HR1:** no card data added; check 5 unchanged.
- **HR2:** check 6 unchanged; no new text.
- **HR3:** synthetic data only; no new data.
- **HR4:** no key, header, `.env` or secret access added.
- **HR5:** check 3 unchanged; N1 and N5 strengthen where an answer can be traced to; see P3-3.
- **HR6:** no retrieved text grants a tool; no injection text in the diff.
- **HR7:** check 4 and the guard order unchanged; region noted above, outside this pass's scope.
- **HR8:** the assistant's tools are unchanged; `never_served` now comes from the script (N2).
- **HR9:** no state-changing tool added; see P3-1 and P3-2.
- **HR10:** not applicable to this change.
- **HR11:** check 8 unchanged.
- **0001, placeholder key in child processes only; only the proxy holds the real key:** recorder still builds through `agent_runtime`; nothing under `services/**` changed.
- **0001, Makefile does not export the key:** Makefile untouched.
- **0001, no key in CI secrets; CI runs in replay:** `.github/**` untouched.
- **0001, key file never copied into a worktree:** not touched.
- **0001, budget caps enforced and not raised:** not touched; pass 3 made no live calls.
- **0001, cassettes still scanned:** the suite is still under `cassettes/`; check 5 unchanged; no cassette changed.

## Files
- /Users/rapido/Projects/northwind-shop-b/evals/tests/test_a2a05_partner.py
- /Users/rapido/Projects/northwind-shop-b/PROGRESS.md (lines 129 and 156)
- /Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-13-15-plan.md (§10)
- /Users/rapido/Projects/northwind-shop-b/services/mcp-rag/src/northwind_mcp_rag/server.py (lines 121-200, read only)
- /Users/rapido/Projects/northwind-shop-b/services/mcp-rag/src/northwind_mcp_rag/retrieve.py (lines 70-97, read only)
- /Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/turn.py (lines 43, 183, read only)
- /Users/rapido/Projects/northwind-shop-b/cassettes/backend-replay/ffb26f9f7aefbca76267e76eb0ae398931d215357e11b067da734c704225d2b1.json (CLI `is_error` shape, read only)
