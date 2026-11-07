# AUTO-2 evidence

Row A2, trimmed scope. Plan and the user's answers: `docs/reviews/2026-11-07-AUTO-2-plan.md`. Base `7d45a40`.

## Failing first

### Item 1, `decided` owners (D4)

With the nine rows changed and `tests/governance/test_owners.py` not yet changed:

```
FAILED tests/governance/test_owners.py::test_every_owner_is_real
FAILED tests/governance/test_owners.py::test_the_table_uses_every_kind_of_owner
2 failed, 159 passed
```

After the test change, 170 passed.

### Items 5 and 6, before the code

```
evals/tests/test_integrity.py: 5 symlink tests failed
tests/hooks/test_settings.py, test_sandbox_settings.py: 6 failed (the proposal has no enrich rule)
tests/ship/test_ship.py -k "docs_diff or data/enrichment": 4 failed
```

### Items 7a and 7c, before the code

```
tests/hooks/test_changed_tests.py: collection error, tools/changed_tests.py does not exist
tests/hooks/test_ship_marker.py: 6 failed, 18 passed
```

The 18 passed because a Makefile with no filter prints nothing, which is what the run-the-tests cases expect, or because they pin what
must not change. They get their failing run from the mutations below.

### Item 2, before the code

```
tests/ship/test_finish.py: 61 failed, 8 passed (no `finish` target, no `--labels` mode)
```

The 8 that passed were refusals a missing target also satisfies, such as a bad checkout, or checks on the script's text. Their
failing run is in the mutations below.

### Items 3, 4 and 7b, before the changes

```
tests/hooks test_notify_hook, test_req_skills, test_stop_conditions, test_gate_report, test_advisor_reviewer: 78 failed, 108 passed
```

One of the 78 was a mistake in the test, not a finding: the token `cat` matches "notification". It now looks for ` cat`.
