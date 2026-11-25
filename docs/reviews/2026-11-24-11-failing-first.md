# Row #11 failing-first log

## rules + validate_plan (before northwind_rag.query existed)
E   ModuleNotFoundError: No module named 'northwind_rag.query'
=========================== short test summary info ============================
ERROR services/rag/tests/test_rag04_query_rules.py
ERROR services/rag/tests/test_rag04_validate_plan.py
!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
2 errors in 0.10s

## eligible_products (before the tool existed)
ERROR services/mcp-catalog/tests/test_rag04_eligible_products.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.45s

## filter accuracy (replay, before W1: no cassette)
LLM cassette miss (10 request(s); suite 'query-understanding', mode replay). The model input changed: re-record on purpose with `make record SUITE=query-understanding`.
Diff of the first miss against the nearest cassette:
WARNING  northwind_assistant.understand:understand.py:263 query understanding failed: BadRequestError
WARNING  northwind_assistant.understand:understand.py:263 query understanding failed: BadRequestError

## Not failing-first, said plainly
- test_rag04_plan_claim.py and test_rag04_turn_qu.py were written after the code they test (mcp_token claim; the turn wiring). Both pass; the security pass and test-writer cover them.

## Named-product exception (after W5, the user's and advisor's go)
- `evals/tests/test_rag04_named_product.py` was written first. Its raw output before the change (the test and the
  code share commit `b4a3521`, so git cannot show the order):
  ```
  ERROR evals/tests/test_rag04_named_product.py
  !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
  1 error in 0.46s
  ```
  The cause is `ImportError: names_catalog_product`. After `b4a3521`: 16 passed.
- **Note:** `test_a_browsing_query_still_narrows_on_both_paths` fails first only through that ImportError. Against
  the pre-fix logic it would pass. It is the requested "still narrows" guard, not a behaviour change.
- **`bc618e0` (the vouch list):**
  - The failure it fixes, `make eval-integrity` at `b4a3521`'s code:
    `frozen vectors vouched (…, disk): 5 of 9, largest distance 2.300e-07 (allowed 1e-04)` and
    `eval-integrity FAILED against origin/main`. The 4 keys are `hdmcdpek…`, `kdldcdlh…`, `neljmjcf…` and
    `odpaehbm…`: "no fixture, eval or smoke text has this key".
  - The new test, run against `b4a3521`'s `query_plans.py`:
    ```
    >       assert len(texts) == 7  # W1's 7 frozen eval part texts, all still vouched
    E       AssertionError: assert 3 == 7
    ```
  - After `bc618e0`: 22 passed in the file, and integrity vouches 9 of 9.
  - **Superseded by option (b)** (`0d4d721`): `bc618e0`'s change is reverted and the 4 vectors are dropped. The
    test is now `test_the_frozen_part_texts_are_the_searched_ones` (3 eval part texts), and integrity vouches 5 of 5.

## After the fact (the advisor's full review, finding H1)

Each fix's pinning tests were run against the pre-fix file (`git show <rev>:<path>`) or a one-line mutation, then the
file was restored from HEAD. The runner, with the final cases, is committed at `docs/reviews/2026-11-24-11-ff-runner.py.txt`. The authoritative output is in the last section, "Final run of the committed runner".

- **Two weak first attempts** were replaced by the lines at the end of the list:
  - The L4 swap failed only at import, so a mutation of `_around` replaces it.
  - The first cache mutation, the read guard only, passed. The write guard alone already keeps the cache empty
    outside live mode. The second mutation removes both guards.

- **security M1: planner stops on non-replayed paths** (evals/src/northwind_evals/query_plans.py@6d86bb3): `5 failed in 1.67s`
  `E       Failed: DID NOT RAISE SystemExit`
- **security L1: cap keeps the later expiry** (services/mcp-rag/src/northwind_mcp_rag/ledger.py@6d86bb3): `1 failed in 0.44s`
  `E       assert not True`
- **security L2+L3: card text in c/s items; turn with a trailing newline** (services/common/src/northwind_common/mcp_token.py@6d86bb3): `3 failed in 0.47s`
  `E       AssertionError: assert not True`
- **security L4: around bounds in range** (services/rag/src/northwind_rag/query.py@6d86bb3): `1 error in 0.44s`
  `E   ImportError: cannot import name 'names_catalog_product' from 'northwind_rag.query' (/Users/rapido/Projects/northwind-shop/services/rag/src/northwind_rag/query.py)`
- **security L4: the eval scores the gateway's fallbacks** (evals/src/northwind_evals/query_plans.py@6d86bb3): `2 failed in 0.48s`
  `E       AttributeError: module 'northwind_evals.query_plans' has no attribute 'shopper_parts'`
- **security L6: the SDK logger is quiet** (services/assistant/src/northwind_assistant/rag_client.py@6d86bb3): `1 failed in 0.47s`
  `E       AssertionError: assert 'anthropic' in ('mcp.client', 'mcp.shared', 'httpx', 'httpcore', 'httpx2', 'httpcore2')`
- **security L7: no SDK retries in live mode** (services/assistant/src/northwind_assistant/understand.py@6d86bb3): `1 failed in 1.24s`
  `E       AssertionError: assert 2 == 0`
- **re-review N1: quiet after the client is built** (services/assistant/src/northwind_assistant/understand.py@330f379): `1 failed in 0.48s`
  `E       assert [10] == [30]`
- **re-review N2: a refusal extends the cap entry** (services/mcp-rag/src/northwind_mcp_rag/ledger.py@330f379): `1 failed in 0.43s`
  `E       AssertionError: assert not True`
- **test-writer F1+F2: card text never to Haiku or a token on its own** (services/assistant/src/northwind_assistant/understand.py@dd4de87, services/common/src/northwind_common/mcp_token.py@dd4de87): `2 failed in 0.60s`
  `E       AssertionError: assert ['multi_part-...ewlines', ...] == []`
- **test-writer F3: validate_plan refuses card text in any form** (services/rag/src/northwind_rag/query.py@dd4de87): `1 failed in 0.09s`
  `E       AssertionError: assert ['dotted', 's... 'cvv_spaced'] == []`
- **M2: eval part texts are frozen queries** (evals/src/northwind_evals/cli.py@bfe9ca8): `1 failed in 1.68s`
  `E       AssertionError: assert {'alpaca-blen...nce question'} <= {'A review sa... price?', ...}`
- **M2 red team: RT-010/011 part texts are frozen queries** (evals/src/northwind_evals/cli.py@ce9f4c5, evals/src/northwind_evals/query_plans.py@ce9f4c5): `1 failed in 1.22s`
  `E       TypeError: part_texts() got an unexpected keyword argument 'suite'`
- **named product, gateway path** (services/assistant/src/northwind_assistant/understand.py@b4a3521^): `4 failed in 0.44s`
  `E       AssertionError: assert {'parts': [{'c': ['Glacier Blue'], 's': ['4XL']}]} is None`
- **named product, eval path** (evals/src/northwind_evals/query_plans.py@b4a3521^): `4 failed in 0.53s`
  `E       AssertionError: assert [('Is the Cor...oducts=None))] == [('Is the Cor...oducts=None))]`
- **user's item 3: the cache is live-mode only (mutation: cache in every mode)** (mutation in services/assistant/src/northwind_assistant/understand.py): `1 passed in 0.47s`
- **decision 0022: the deadline is live-mode only (mutation: deadline in every mode)** (mutation in services/assistant/src/northwind_assistant/understand.py): `1 failed in 1.14s`
  `E       AssertionError: assert ('timeout' == 'model'`
- **user's item 4: the model is not told the filter (mutation: name it in the prompt)** (mutation in services/assistant/src/northwind_assistant/turn.py): `1 failed in 1.77s`
  `E           assert 'price_max' not in '[{"role": "... "medium"}}]'`
- **user's item 3: the cache is live-mode only (mutation: cache read and write in every mode)** (mutation): `1 failed in 0.52s`
  `E           assert 1 == 3`
- **security L4: around bounds in range (mutation: _around returns them unclamped)** (mutation): `2 failed in 0.45s`
  `E       assert (0, 0) == None`

**Not failing-first, said plainly:**
- `test_rag04_plan_claim.py`, and the other tests in `test_rag04_turn_qu.py`, were written after their code. Of
  `test_rag04_turn_qu.py`, the cache, deadline and "under $50" tests now have mutation evidence above.
- The `*_tw.py` files are `test-writer`'s tests of code that already existed. Their findings came from failing runs
  of those files (all fixed in `f698484`, with evidence above).
- `test_rag04_post_review_tw.py` (23 tests, the advisor's H2) found no defect.

## Security H2-L1 (internal product names are not keys)
- `test_an_internal_products_name_is_not_a_key` was written first; before the fix:
  ```
1 failed in 0.41s
E           AssertionError: assert not True
E            +  where True = names_catalog_product('Tell me about the Corvane Ascent Pro Shell')
  ```
- After the fix: 17 passed in the file; `make eval-snapshot` leaves all 308 contexts byte-identical.

## The advisor's re-read: two more weak entries, replaced (its L2)

- Two entries above failed only because an API was missing, not on behaviour:
  - "security L4: the eval scores the gateway's fallbacks", which failed with `AttributeError … shopper_parts`;
  - "M2 red team", which failed with `TypeError … 'suite'`.
- Behaviour mutations replace them:

- **security L4: the eval falls back like the gateway when the token cannot hold the plan (mutation: no size check)** (mutation): `1 failed in 0.47s`
  `E       AssertionError: assert [('éééééééééé..., 'P0064'})))] == [('something'...oducts=None))]`
- **M2 red team: RT-010/011 part texts are frozen queries (mutation: drop the red-team line)** (mutation): `1 failed in 1.79s`
  `E       AssertionError: assert {'EU parcel c...t and timing'} <= {'A review sa... price?', ...}`

- **The runner, as first committed, still held the superseded cases** (the advisor's last re-read). It now holds the
  final ones; see the next section.
- **A session edit of a `test-writer` test (its L3):**
  - In `test_rag04_post_review_tw.py`, the session changed `assert len(new) == 5` to `<= 5`, so the check still
    holds once this row merges and `origin/main` already holds the keys.
  - The property `new <= wanted` (every new key is a frozen query's key) is unchanged.
  - The test never failed; this is not a relaxation made after a failure.

## Final run of the committed runner (authoritative; the advisor's last re-read, L2)

- **The runner:** `docs/reviews/2026-11-24-11-ff-runner.py.txt`, with the final 19 cases. Each case gives the exact
  file at a revision, or the exact old and new text of the mutation, and its test IDs.
- **The cases:**
  - the four replacements (L4 `_around`; L4 size check; M2 red team; the cache in every mode);
  - the user's items 3 and 4, and the deadline mutation;
  - H2-L1.
- **The run:** from the repo root, `uv run python <copy of the runner>`, on a clean tree (it asserts clean before and
  after and restores every file from HEAD), 2026-11-24 at `f4c6686`. Output:

- **security M1: planner stops on non-replayed paths** (evals/src/northwind_evals/query_plans.py@6d86bb3): `5 failed in 1.70s`
  `E       Failed: DID NOT RAISE SystemExit`
- **security L1: cap keeps the later expiry** (services/mcp-rag/src/northwind_mcp_rag/ledger.py@6d86bb3): `1 failed in 0.47s`
  `E       assert not True`
- **security L2+L3: card text in c/s items; turn with a trailing newline** (services/common/src/northwind_common/mcp_token.py@6d86bb3): `3 failed in 0.46s`
  `E       AssertionError: assert not True`
- **security L4: around bounds in range (mutation: _around returns them unclamped)** (mutation in services/rag/src/northwind_rag/query.py): `2 failed in 0.47s`
  `E       assert (0, 0) == None`
- **security L4: the eval falls back like the gateway when the token cannot hold the plan (mutation: no size check)** (mutation in evals/src/northwind_evals/query_plans.py): `1 failed in 0.49s`
  `E       AssertionError: assert [('éééééééééé..., 'P0136'})))] == [('something'...oducts=None))]`
- **security L6: the SDK logger is quiet** (services/assistant/src/northwind_assistant/rag_client.py@6d86bb3): `1 failed in 0.46s`
  `E       AssertionError: assert 'anthropic' in ('mcp.client', 'mcp.shared', 'httpx', 'httpcore', 'httpx2', 'httpcore2')`
- **security L7: no SDK retries in live mode** (services/assistant/src/northwind_assistant/understand.py@6d86bb3): `1 failed in 1.20s`
  `E       AssertionError: assert 2 == 0`
- **re-review N1: quiet after the client is built** (services/assistant/src/northwind_assistant/understand.py@330f379): `1 failed in 0.46s`
  `E       assert [10] == [30]`
- **re-review N2: a refusal extends the cap entry** (services/mcp-rag/src/northwind_mcp_rag/ledger.py@330f379): `1 failed in 0.44s`
  `E       AssertionError: assert not True`
- **test-writer F1+F2: card text never to Haiku or a token on its own** (services/assistant/src/northwind_assistant/understand.py@dd4de87, services/common/src/northwind_common/mcp_token.py@dd4de87): `2 failed in 0.57s`
  `E       AssertionError: assert ['multi_part-...ewlines', ...] == []`
- **test-writer F3: validate_plan refuses card text in any form** (services/rag/src/northwind_rag/query.py@dd4de87): `1 failed in 0.10s`
  `E       AssertionError: assert ['dotted', 's... 'cvv_spaced'] == []`
- **M2: eval part texts are frozen queries** (evals/src/northwind_evals/cli.py@bfe9ca8): `1 failed in 1.64s`
  `E       AssertionError: assert {'alpaca-blen...nce question'} <= {'A review sa... price?', ...}`
- **M2 red team: RT-010/011 part texts are frozen queries (mutation: drop the red-team line)** (mutation in evals/src/northwind_evals/cli.py): `1 failed in 1.83s`
  `E       AssertionError: assert {'EU parcel c...t and timing'} <= {'A review sa... price?', ...}`
- **named product, gateway path** (services/assistant/src/northwind_assistant/understand.py@b4a3521^): `4 failed in 0.45s`
  `E       AssertionError: assert {'parts': [{'c': ['Glacier Blue'], 's': ['4XL']}]} is None`
- **named product, eval path** (evals/src/northwind_evals/query_plans.py@b4a3521^): `4 failed in 0.56s`
  `E       AssertionError: assert [('Is the Cor...oducts=None))] == [('Is the Cor...oducts=None))]`
- **user's item 3: the cache is live-mode only (mutation: cache read and write in every mode)** (mutation in services/assistant/src/northwind_assistant/understand.py): `1 failed in 0.52s`
  `E           assert 1 == 3`
- **decision 0022: the deadline is live-mode only (mutation: deadline in every mode)** (mutation in services/assistant/src/northwind_assistant/understand.py): `1 failed in 1.13s`
  `E       AssertionError: assert ('timeout' == 'model'`
- **user's item 4: the model is not told the filter (mutation: name it in the prompt)** (mutation in services/assistant/src/northwind_assistant/turn.py): `1 failed in 1.80s`
  `E           assert 'price_max' not in '[{"role": "... "medium"}}]'`
- **security H2-L1: an internal product's name is not a key (mutation: keys from every product)** (mutation in services/rag/src/northwind_rag/query.py): `1 failed in 0.46s`
  `E           AssertionError: assert not True`

- **Result:** all 19 cases fail as they should. The earlier lists above are kept as the history.

## `make ci` at `2caef81`: the 25 failures, and what fixed each (the advisor's re-read before the final `make ci`)

`caffeinate -dims make ci DATABASE_URL=…/northwind_16` at `2caef81` exited 2. Lint, mypy (385 files) and the scan
passed; pytest gave 25 failed and 7,804 passed. The failing tests, as printed:

- `evals/tests/test_enrichment_e1_more.py::test_the_command_checks_each_side_as_its_own_kind`
- `evals/tests/test_enrichment_e1_more.py::test_the_command_exits_0_when_e1_passes`
- `evals/tests/test_enrichment_e1_more.py::test_the_command_exits_1_on_a_b_only_failure`
- `evals/tests/test_enrichment_e1_more.py::test_the_command_exits_1_on_a_plain_index_problem_and_writes_no_report`
- `evals/tests/test_enrichment_e1_more.py::test_the_command_exits_1_on_an_enriched_index_problem`
- `evals/tests/test_enrichment_e1_more.py::test_the_command_exits_1_when_a_clause_fails_and_reports_it`
- `evals/tests/test_enrichment_e1_more.py::test_the_command_exits_1_when_both_sides_are_the_same_index`
- `evals/tests/test_enrichment_e1_more.py::test_the_command_exits_1_when_there_is_no_enriched_index`
- `evals/tests/test_rag03_compare.py::test_live_retrieval_passes_each_items_region_as_a_shopper`
- `services/assistant/tests/test_cc10_token_exposure.py::test_the_token_is_in_no_file_of_the_runtime_and_not_on_stderr`
- `services/assistant/tests/test_chk01_cart.py::test_no_recorded_red_team_turn_writes_a_cart`
- `services/assistant/tests/test_tool_surface.py::test_every_recorded_request_offers_the_frozen_surface[assistant-redteam]`
- `services/gateway/tests/test_playwright_servers.py::test_the_gateway_attaches_exactly_mcp_rag_and_mcp_catalog`
- `services/mcp-catalog/tests/test_catalog_canary.py::test_the_canary_title_and_id_are_in_no_response_byte`
- `services/mcp-catalog/tests/test_chk01_criteria.py::test_the_registry_holds_the_catalog_reads_and_the_four_cart_tools_and_nothing_else`
- `services/mcp-catalog/tests/test_tools.py::test_the_catalog_source_never_touches_the_index`
- `services/mcp-catalog/tests/test_tools.py::test_the_server_serves_exactly_the_five_tools`
- `services/rag/tests/test_context_checks.py::test_no_chunk_is_dumped_whole`
- `services/rag/tests/test_frozen_vectors.py::test_the_committed_file_covers_every_chunk_eval_query_and_smoke_question`
- `tests/governance/test_tool_register.py::test_the_tool_the_assistant_really_offers_has_a_register_row`
- `tests/mcp/test_contract.py::test_a_description_says_when_not_to_use_it_and_gives_an_example[northwind-catalog-eligible_products]`
- `tests/mcp/test_contract.py::test_no_scope_grants_a_tool_outside_prd_sevens_read_only_set[northwind-catalog]`
- `tests/mcp/test_contract.py::test_the_catalog_listing_is_within_the_five_read_only_tools[shopper-query]`
- `tests/mcp/test_contract.py::test_the_catalog_registry_is_exactly_the_five_read_only_tools`
- `tests/mcp/test_contract.py::test_tools_list_is_within_the_agents_scope_and_read_only[shopper-query-northwind-catalog]`

These failures are the failing-first evidence for the fixes that followed:
- **15 fixed without loosening any check** (`e7df4b6`):
  - the 8 E1 tests and the 1 RAG-03 test: their seam (`_live_plain`);
  - token exposure: `mint_turn` through `mint`;
  - the chunk-dump pin: a loop-variable rename;
  - the tool register: a row added;
  - the contract's description test: the description;
  - the catalog-source test: a comment;
  - the canary scan: coverage added.
- **10 pins updated as the user approved under stop 5** (`30e4a52`).
- After both: `make check` at `30e4a52` exited 0 (pytest 7,830 passed; Vitest 104 passed).

The two new checks added in `e7df4b6` had no failing run, so mutations show them failing (each restored from HEAD
afterwards):

- **canary: the live filter never names P0009 (mutation in live.py: internal products kept)**: `1 failed in 0.59s`
  `E               assert ('NWCANARY-28c917c5dc94' not in '{"region":"...9","P0200"]}'`
- **canary: the live filter never names P0009 (mutation in live.py: every product listed, no checks)**: `1 failed in 0.59s`
  `E               assert ('NWCANARY-28c917c5dc94' not in '{"region":"...9","P0200"]}'`
- **RAG-03 seam: _live binds each item's region (mutation in cli.py: _live binds US)**: `1 failed in 1.17s`
  `E       AssertionError: assert [('a', 'US', ...', 'shopper')] == [('a', 'UK', ...', 'shopper')]`

- **The `OPTIONAL_CLAIMS` pin** (`test_chk01_criteria.py`, `{"sid", "qf"}`) was changed in `9d103a7`, the first
  implementation commit, with the `qf` claim. It is not one of the ten.

## `make ci` at `b8c669d`: the drift pin (re-pinned with the user's approval under stop 5)

`caffeinate -dims make ci DATABASE_URL=…/northwind_16` at `b8c669d` exited 2.
- **Passed:** lint, mypy (386 files), the scan, the unit tests (7,833 passed, 9 skipped, 24 xfailed) and Vitest (104).
- **Integration:** 1 failed, 302 passed, 1 skipped:
  `evals/tests/test_rag03_drift.py::test_the_eval_retrieval_over_every_item_gives_the_macos_hash`. Output:

```
E       AssertionError: assert 'b5bc42db9fb7...e737e889d8051' == '168c823b9237...ee9960b496b4d'
E         
E         - 168c823b92378ee8c8c26a1c0ed5ceee1fa4fa7140b9f64b1bfee9960b496b4d
E         + b5bc42db9fb7b39b66636d52aa4f12a7ad5304c458d50333dc5e737e889d8051
```

**Measured on `northwind_16`** (index `3a89123f2aaf586f`), over the 308 items:
- `cli._live_plain` gives `168c823b92378ee8c8c26a1c0ed5ceee1fa4fa7140b9f64b1bfee9960b496b4d`, the old pin exactly.
- `cli._live` gives `b5bc42db9fb7b39b66636d52aa4f12a7ad5304c458d50333dc5e737e889d8051`.
- They differ on exactly 8 items: EV-018, EV-029, EV-030, EV-031, EV-035, EV-067, EV-068 and EV-190, the 8 contexts
  the eval re-snapshot changed.

So the pin moved because `_live` runs the planned first search, a behaviour change, not a weakened check. The session
stopped under stop 5, and the user replied "re-pin approved".
- **`d0c0bb2`:** `MACOS_HASH` is `b5bc42db…`.
- **The next commit:** `MACOS_HASH_PLAIN` (`168c823b…`) is asserted on `cli._live_plain`, so the plain path keeps its
  own pin (the advisor's suggestion). This adds a check.

## GitHub's python job at `92530a6`: `git show origin/main` with no history (fixed with the user's approval under stop 5)

**The user's approval, word for word,** from the user's message of 2026-11-25 (headed "Advisor + user"):

> Approved fix (stop 5, this row's own test): follow the repo's convention in evals/tests/test_rag08b_citations.py:118-121 and pytest.skip("origin/main is not available in this checkout") when the ref can't be resolved. Note in the test that eval-integrity-main covers the same add-only/vouch check in CI.

The `advisor-reviewer` did not issue this approval; its reads are reviews, not approval.

GitHub's `CI / python` job checks out without history, so `origin/main` does not exist there.
`test_rag04_post_review_tw.py::test_every_new_frozen_vector_is_the_key_of_a_frozen_query` ran
`git show origin/main:…` with `check=True` and failed every time.

**Reproduced locally.** A clone without that ref: `git clone --no-checkout --single-branch --branch req/11-query-understanding`,
where `git rev-parse --verify origin/main` exits 1. The test then ran with `GIT_DIR` set to that clone:

**Before the fix:**
```
E               subprocess.CalledProcessError: Command '['git', 'show', 'origin/main:data/embeddings/fastembed-BAAI-bge-small-en-v1.5-384.jsonl']' returned non-zero exit status 128.
1 failed in 1.57s
```

**After the fix.** It follows `test_rag08b_citations.py`: the test calls `pytest.skip` when the ref cannot be resolved.

**What the comment says about CI coverage.** GitHub runs a related but weaker check, not the same one:
- `Guard / eval-integrity-main` refuses a changed or removed frozen vector (add-only).
- `CI / eval-gate`'s integrity step vouches that every added vector belongs to a known chunk or a frozen query.
  `_frozen_texts` also covers the fixture chunks.
- No CI job enforces "only frozen queries, at most 5 new". That strict form runs wherever origin/main exists, as in a
  local `make ci`.
- The approval's note asked for "the same add-only/vouch check". The comment is corrected to say what CI actually
  runs (the advisor's low at `2f3783d`).

```
SKIPPED [1] evals/tests/test_rag04_post_review_tw.py:226: origin/main is not available in this checkout
1 skipped in 1.06s
```

**With origin/main present** (this checkout), the whole file passes: 23 passed.

No other test this row added or changed reads git history. A grep of the added test files and of the row's
hunks in the changed ones for `origin/main`, `subprocess`, `git`, `rev-parse` and `merge-base` found only this test.
