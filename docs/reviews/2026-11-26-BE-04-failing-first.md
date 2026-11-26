# BE-04 (row #25): failing-first log

## M12: test_m12_lines.py against orders_place.py at HEAD b5e2d50 (before the fix)

```
services/backend/tests/orders_place/test_m12_lines.py:162: AssertionError
=========================== short test summary info ============================
FAILED services/backend/tests/orders_place/test_m12_lines.py::test_a_line_with_a_quantity_below_one_is_invalid_line_and_escalated[0]
FAILED services/backend/tests/orders_place/test_m12_lines.py::test_a_line_with_a_quantity_below_one_is_invalid_line_and_escalated[-1]
FAILED services/backend/tests/orders_place/test_m12_lines.py::test_a_line_with_a_quantity_below_one_is_invalid_line_and_escalated[-50]
FAILED services/backend/tests/orders_place/test_m12_lines.py::test_a_negative_line_cannot_lower_the_total_of_a_valid_one
FAILED services/backend/tests/orders_place/test_m12_lines.py::test_a_bad_line_among_good_ones_makes_the_order_invalid
FAILED services/backend/tests/orders_place/test_m12_lines.py::test_a_variant_on_two_lines_is_invalid_line_and_escalated
FAILED services/backend/tests/orders_place/test_m12_lines.py::test_duplicate_lines_that_each_fit_stock_but_overflow_when_summed_do_not_pass
FAILED services/backend/tests/orders_place/test_m12_lines.py::test_duplicate_lines_that_fit_when_summed_are_still_invalid
FAILED services/backend/tests/orders_place/test_m12_lines.py::test_the_invalid_line_reason_is_code_owned_not_the_models
9 failed, 2 passed in 0.63s
```

After the fix: 11 passed.

## Node choice: US postal codes as the data writes them (`US-7494`)

test-writer's `test_the_real_us_order_postal_code_format_picks_a_dc_by_its_digits` failed on the first nodes.py (`assert 'DC-EAST' == 'DC-WEST'`: it read the `U` of `US-`). After the fix: 36 passed.

## Race (Postgres)

Not run yet: window B holds the shared Postgres. `test_a_read_then_write_reserve_oversells_under_the_same_race` is the sensitivity check.

## Wiring pin (`test_be04_wiring.py`): on the version-2 orders, before the generator fit

```
        assert len(real) == len(orders()) == 1000
        changed = sorted(oid for oid in real if real[oid] != deep[oid])
>       assert changed == [], f"{len(changed)} orders' tool results changed: {changed[:10]}"
E       AssertionError: 2 orders' tool results changed: ['O000476', 'O000936']
E       assert ['O000476', 'O000936'] == []
services/backend/tests/orders_place/test_be04_wiring.py:115: AssertionError
```

On the version-3 orders (`BACKEND_DATA_VERSION` 3, `_fit_to_stock`): 10 passed. 0 orders' tool results changed, 0 reservations refused, 950 placed.

## Cassettes, after the data change and the wiring (the user's condition for option B)

- sha256 of every file in `cassettes/backend-replay`, `backend-audit` and `backend-orchestrator`, taken before the data change and again after the wiring: **identical, 5,989 of 5,989**. That includes the 12 `backend-replay` cassettes that name O000476 or O000936 (6 each).
- Replay, `LLM_MODE=replay`, where the plugin fails a test on any miss:
  - `test_be05_replay_recorded.py`: 2 passed (all 1,000 orders), 4 min 21 s;
  - `test_be02_agent_recorded.py`: 12 passed;
  - `test_be01_recorded.py`: 5 passed.
  - **0 misses.**
