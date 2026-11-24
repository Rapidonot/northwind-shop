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
