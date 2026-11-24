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
