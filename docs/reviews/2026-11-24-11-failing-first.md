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
- `evals/tests/test_rag04_named_product.py` written first. Before the change it failed at collection:
  `ImportError: names_catalog_product`, 1 error. After `b4a3521`: 16 passed.
