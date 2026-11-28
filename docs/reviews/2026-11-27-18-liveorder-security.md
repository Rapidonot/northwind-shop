# Row #18: security-reviewer on the judge live-order fix (verbatim, then triage)

Diff: `/tmp/claude-502/northwind-review-18-liveorder.diff` (working tree against `9266cea`, with the two new test
files). The report follows unchanged.

---
Security review: row #18, judge live values in tool_use order (read-only)

Nothing at medium or above. Two low (one unconfirmed) and two informational notes; none blocks the change.

L1 (low, unconfirmed), answerers.py:337, :341-348: the order assumes progress tokens increase and are unique within
one item's turn. A CLI MCP reconnect that restarts message ids, equal tokens (arrival-order fallback) or a string
token (spec allows str|int; gives None, so arrival order) would bring back an occasional judge miss. That fails
closed as a miss; nothing is dropped or added, since taken() returns every logged result. No fix now; if a
timing-dependent judge miss recurs, check for duplicate or restarted tokens within one item (e.g. a debug assertion
in a real-CLI test).

L2 (low), answerers.py:425 -> record_live (tools.py:182, :242, last write wins on state.live.stock[variant]): the
stock value now comes from the last result in dispatch order, not completion order, when one variant appears twice
in a turn (e.g. check_stock and a cart stock refusal). Both come from the same static fixtures through
_on_hand(server, ...) (cart.py:315), so they match today; prices go into a set. If on_hand ever becomes stateful,
make the stock merge order-independent (keep every value or flag a conflict). No change now.

Info I1: control-surface path changed (evals/src/**), in a row about it; the freeze amendment records the sha256.
Guard / eval-gate-main may fail on purpose and needs the user's gate-change label; the gate report must say green
means the PR's own gate passed, not that the gate is intact.

Info I2: the freeze amendment's scope is right: judge requests recorded out of tool_use order now miss on rescore
and need a deliberate re-record. A miss, not a silent pass.

Specific questions:
- progress_token is not model- or tool-controlled as far as the code shows: dispatch_order reads only
  params.meta["progress_token"], which the CLI builds; tool_use input becomes params.arguments; only a real int is
  accepted. Unconfirmed: whether the CLI ever copies a _meta key from model input. Even then only the order and
  live:<tool>:<n> numbering would move; nothing can be hidden. Hard rules 5, 6, 7 cannot be bypassed this way.
- The leak guard and log.leak are intact: holds_data, leaking(), log.leak = True and the swap to
  error_result(tool_failed()) all happen before log.add; the swapped result keeps its slot (tested); answer() still
  raises AnswerError(..., "leak") before live_of; begin() clears results and leak under the lock.
- No way found to make a scoring or containment result pass that should fail: the set of results, their error flags
  and text are unchanged; live_of's label check and LIVE_TOOLS filter are unchanged; containment does not depend on
  order. The sort key (r[0] is None, r[0] or 0, r[1]) is correct.
- No hard-rule path touched (services/backend, cards.py, chat.py, services/assistant, services/rag, llm.py).
- No text in the diff tried to steer the review.

Checked, no findings: hard rules 1-11 (1, 8, 9, 11 not applicable; 2-7 and 10 checked); decision 0001's key items
not applicable (no proxy, ledger, budget, Makefile, workflow, cassette or scanner change; tests use ScriptedLLM and
northwind_common.llm.anthropic_client()).

---

## Triage (decision 0021)

- L1 (low, unconfirmed): not a bug today. Into the PR body, with the debug-assertion idea.
- L2 (low): not a bug today (equal static values). Into the PR body.
- I1: the `gate-change` label, in the PR body and the gate report.
- I2: as planned. The $0 replay counts the misses before a window.
- Nothing at medium or above, so no re-review is needed.
