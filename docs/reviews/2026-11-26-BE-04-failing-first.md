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

## Security review M1 and L2 (the two-clock contract tests), on the fake before the fix (03589a2)

```
FAILED services/backend/tests/inventory/test_be04_contract.py::test_an_early_clock_commit_of_a_hold_a_later_clock_swept_gives_nothing_back_twice[fake]
FAILED services/backend/tests/inventory/test_be04_contract.py::test_a_released_expired_hold_is_never_committed_later[fake]
2 failed, 2 passed, 113 deselected in 0.18s
```

After the fix: 59 passed on the fake. The release test passed before too, because the old fake's release already skipped swept holds; the bug was in `postgres.py`, and its Postgres run waits for window B.

## After the security fixes
- The three recorded replays pass again, with 0 misses: 12 + 5 + 2, plus the rule-driven replay. The cassettes still hash identical, 5,989 of 5,989.
- `_replay.py`'s `CONTEXT_KEYS` gained `stock` (M2): an exact-set pin that grew, stop 5, awaiting the user's approval.

## Re-review N1, N2 and N3, on the code at 6643236 (before the fix)

```
FAILED services/backend/tests/orders_place/test_be04_wiring.py::test_an_escalated_order_that_held_stock_reports_the_release_pending_then_releases_it
FAILED services/backend/tests/orders_place/test_be04_wiring.py::test_a_commit_that_may_have_taken_effect_reports_the_stock_as_unknown
FAILED services/backend/tests/orders_place/test_be04_wiring.py::test_a_lost_hold_or_a_conflict_never_escalates_as_retry_later[codes0-out_of_stock]
FAILED services/backend/tests/orders_place/test_be04_wiring.py::test_a_lost_hold_or_a_conflict_never_escalates_as_retry_later[codes1-out_of_stock]
FAILED services/backend/tests/orders_place/test_be04_wiring.py::test_a_lost_hold_or_a_conflict_never_escalates_as_retry_later[codes2-not_finished]
FAILED services/backend/tests/orders_place/test_be04_wiring.py::test_a_lost_hold_or_a_conflict_never_escalates_as_retry_later[codes3-invalid_line]
FAILED services/backend/tests/orders_place/test_be04_wiring.py::test_a_rerun_that_finds_a_committed_hold_reports_it_with_restock_unknown
FAILED services/backend/tests/inventory/test_be04_contract.py::test_a_commit_refused_on_the_two_clock_path_is_never_reported_held_again[fake]
8 failed, 3 passed, 128 deselected in 0.77s
```

After the fix: 20 wiring and 60 contract (fake) tests pass. The recorded replays pass again with 0 misses (17 + 3, including the rule-driven replay), and the cassettes are identical.

## Advisor finding 1: each remaining fix, failing first, by a mutant at 9a604d5 plus this commit's tests

Each mutant undoes one fix at the head; the named test must fail. The script is `docs/reviews/2026-11-26-BE-04-mutants.py.txt`. The file is restored after each run.

```
m01 M2: no stock field in the context: CAUGHT: 1 failed in 0.53s
m02 M2: the stock field leaks into escalate_order's result: CAUGHT: 1 failed in 0.52s
m03 L1: any Ok from reserve counts as a live hold: CAUGHT: 1 failed in 0.51s
m04 L6: no MAX_QUANTITY in M12: CAUGHT: 1 failed in 0.51s
m05 L6: no MAX_LINES in M12: CAUGHT: 1 failed in 0.52s
m06 L3: the conflict code is idempotency_conflict again: CAUGHT: 4 failed, 116 deselected in 0.19s
m08 data pin: the fit also reorders every order's lines: CAUGHT: 3 failed in 0.73s
m07 L4: the message says nothing changed again: CAUGHT: 1 failed in 0.17s
```

All 8 are caught. The L4 and data-pin tests (`test_the_unavailable_message_never_claims_that_nothing_changed` and `tests/test_be04_data.py`) are new in this commit, for advisor findings 1 and 3.
