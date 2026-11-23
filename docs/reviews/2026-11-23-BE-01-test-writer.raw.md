# BE-01 test-writer report (row #24), 2026-11-23

This is the agent's final report, condensed from its hand-back. The facts are unchanged.

**File:** `services/backend/tests/orchestrator/test_be01_criteria.py`, 20 tests. Scripted model only: no cassettes, no `conftest.py`, nothing under `src/` touched.

**Runs.** These all happened before the user's hold on test runs (2026-11-23) reached the agent:
- first run: 18 passed and 2 failed, both mistakes in the tests:
  - attempt and outcome events share an `action_id`;
  - a test expected `not_permitted` where the section says `not_run`;
- after the fixes: 20 passed, twice;
- since the hold: no pytest;
- `ruff format`, `ruff check` and `mypy` on the file: clean.

The tests are to be rerun once the hold lifts.

**Coverage:**
- two orders in parallel (a barrier);
- exactly `MAX_CONCERNS`, and one more refused;
- every unsupported topic annotated;
- valid empty results: a parcel with no scans, an order with no fraud assessment;
- a foreign tracking number refused, with zero carrier calls;
- a subagent with no tool call is not a silent success;
- no model text in the answer;
- one envelope per run, with child IDs;
- no run ID, time or deadline in any model request;
- the audit chain verifies, and children name the run as parent;
- a refused request is audited without its text;
- four failing-sink cases (down for a task, task outcome lost, run outcome lost, down from the start).

**Agent's note, not a bug:** for a foreign tracking number the section failure is `not_run`, because the task's own parcel was never looked up. The `not_permitted` refusal is in the audit only. This is consistent with `orders_check` (N5).
