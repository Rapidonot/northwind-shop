# Row #16, addendum 8: security-reviewer raw report (2026-11-23)

Diff reviewed: /tmp/claude-502/northwind-security-16-addendum8.diff (working tree on 47e08f7: offer rule, proxy
tool_result reorder, ship.sh checks wait). Verbatim summary of the findings as returned:

- No critical, high or medium findings. 4 low.
- L1 (low): tool_order rebuilds `rank` per user message: O(users x tool_uses) on the event loop, before auth in
  the sandbox proxy and before the gate's content checks in the runner; a crafted 2 MB body can block the loop
  for minutes. Not a spend bypass. Fix: build `rank` once per assistant message.
- L2 (low): reorder re-serializes before `gate.refuse`'s `_has_one_meaning`, so duplicate keys collapse and a
  UTF-16/32 body is re-encoded: such bodies are forwarded instead of refused (`refused-body`) and stop counting
  toward MAX_REFUSALS. Not a smuggle (gate, leak scan and upstream read the same bytes). Fix: do not reorder (or
  refuse) a body that is not strict UTF-8 or has duplicate keys.
- L3 (low): re-serialization can grow the body past MAX_BODY_BYTES (floats expand, `1e15` -> `1000000000000000.0`)
  and can emit invalid JSON (`1e400` -> `Infinity`); a lone surrogate raises UnicodeEncodeError (a 500 in the
  sandbox, `refused-unreadable` in the runner). The reservation stays priced on forwarded bytes. Fix:
  `allow_nan=False`, catch UnicodeEncodeError, and bound the forwarded size.
- L4 (low): an `offer` match is counted only at its sentence's end, so a turn reset with `discard=True` or
  aborted while an offer is open counts neither `offer` nor the blocking class. No text is released (fail closed);
  only the evidence is short. Fix: count the pending decision in `reset`/`abort` before `_clear()`.
- Area 1 (offer drop): no finding beyond L4. `_skip_space` skips spaces and tabs only and never emits;
  `_blocking_class` applies the same patterns, views, exemptions, duration gate, unparsed durations and links as
  `_first_match`; new triggers only add holding.
- Area 2 (proxy): the user's condition holds on every path (scrub, key, log, gate.refuse, the reservation's
  len(raw), request_leaks, upstream content, the cassette scan all read the reordered bytes; content-length is not
  forwarded). `_POSITIONAL` is a permutation of slots. No key-guessing oracle. miss_diff leaks nothing new.
- Area 3 (ship.sh): no finding. Settings validated before any gh call; PR-controlled names compared only;
  a failed or empty poll refuses at the deadline; the bucket read is unchanged. Informational: `2>/dev/null` on
  the poll makes a gh auth/network failure read as "CI has not started".
- Outside the diff: the staged deletions under cassettes/eval-answers must be the planned ones, listed in the PR
  body and gate report.
