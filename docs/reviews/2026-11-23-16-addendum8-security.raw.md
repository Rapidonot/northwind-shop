# Row #16, addendum 8: security-reviewer raw report (2026-11-23)

The diff reviewed was `/tmp/claude-502/northwind-security-16-addendum8.diff`: the working tree on `47e08f7`,
covering the offer rule, the proxy's tool_result reorder and the `ship.sh` checks wait. The report follows as
the reviewer returned it, apart from markdown layout.

## Security review: row #16, addendum 8 (offer drop, proxy tool_result reorder, ship.sh checks wait)

**Result:** no critical, high or medium findings. There are 4 low findings, and nothing found releases held text,
gets a payload past a scanner, or gets around the budget or the required checks.

**What I read:**
- The diff: `/tmp/claude-502/northwind-security-16-addendum8.diff`, base `47e08f7`.
- The rules: `CLAUDE.md` hard rules, `docs/decisions/0001-llm-first-order-and-record-replay.md`, and the plan's
  addendum 8 updates 2 and 3 in `docs/reviews/2026-11-17-16-plan.md`.
- The full current source:
  - `services/assistant/src/northwind_assistant/output_check.py`
  - `services/llm-replay/src/northwind_llm_replay/proxy.py` (handle)
  - `services/llm-replay/src/northwind_llm_replay/runner.py` (gate and forward)
  - `tools/ship.sh`

Nothing in the diff, tests or docs tried to steer the review.

### Findings (most severe first)

**L1 (low). Reorder costs O(N·M) CPU and runs before auth.**
- **Where:** `services/llm-replay/src/northwind_llm_replay/tool_order.py:27`, called per user message at `:72`,
  and `proxy.py:342`.
- **Rule:** decision 0001 spend control; availability.
- **What is wrong:** `_ordered` rebuilds `rank` from `uses` for every user message that follows one assistant
  message. The cost is (number of user messages) × (number of `tool_use` blocks). It runs synchronously on the
  event loop.
  - In the sandbox proxy it runs before the placeholder-auth check (`proxy.py:388`).
  - In the runner it runs after the token check but before the gate's content checks.
- **How it fails:** take a 2 MB body (the runner's `MAX_BODY_BYTES`) holding about 35k minimal `tool_use` blocks
  in one assistant message, followed by about 35k `{"role":"user","content":[]}` messages. That is roughly 10⁹
  dict insertions, which blocks the runner's loop for minutes while the window clock runs. It is not a spend
  bypass: the reservation still applies afterwards.
- **Fix:** build `rank` once, when an assistant message sets `uses`, and pass it in. That makes the walk linear.
  Nothing is loosened.

**L2 (low). Reorder hides duplicate-key and non-UTF-8 refusals.**
- **Where:** `proxy.py:342-345` together with `runner.py:313-320` and `:409`.
- **Rule:** decision 0001 runner gate.
- **What is wrong:** when a reorder happens, the bytes are re-serialized from the parsed object before
  `gate.refuse` runs `_has_one_meaning`. As a result:
  - Duplicate keys are collapsed (last one wins).
  - A UTF-16 or UTF-32 body is re-encoded as UTF-8. `json.loads` on bytes accepts those encodings.
  - So these bodies are forwarded instead of refused as `refused-body`, and they no longer count toward
    `MAX_REFUSALS` ("a few mean someone is probing").
  - The sandbox proxy (`via_runner`) also re-serializes before the runner ever sees the body.
- **Why it is not a smuggle:** the gate, the leak scan and upstream all read the same re-serialized bytes. Only
  the tamper signal is lost.
- **Fix:** before reordering, refuse when the original `raw` is not strict UTF-8 or has duplicate keys (the same
  test as `_has_one_meaning`), in every proxy, not only one with a gate.

**L3 (low). Re-serialization can grow the body past the size cap, and can emit invalid JSON.**
- **Where:** `proxy.py:345` against `runner.py:109` and `:575`.
- **Rule:** decision 0001 spend control.
- **What is wrong:**
  - `MAX_BODY_BYTES` is checked against the original declared `content-length` only. After `json.dumps`:
    - Float literals expand. For example `1e15` becomes `1000000000000000.0`, about 3.8 times longer. The
      runner does not inspect `tool_use.input` contents, so a 2 MB body can go upstream at about 7.6 MB.
    - `1e400` becomes `Infinity`, which is invalid JSON. Upstream returns a 400, the ledger charges the worst
      case, and the runner counts a refusal.
  - A lone-surrogate escape (`\udXXX`) in a reordered body raises `UnicodeEncodeError` at `.encode("utf-8")`.
    The runner turns that into `refused-unreadable` (fail closed). The sandbox proxy returns an unstructured
    500 with no log entry. Before this change it failed at `_log` instead.
- **Why the budget still holds:** the reservation is priced on the forwarded length. The size cap is simply no
  longer an upper bound on what goes upstream.
- **Fix:**
  - Use `json.dumps(..., allow_nan=False)` and turn its `ValueError` into a structured 400.
  - Catch `UnicodeEncodeError` there and return a structured 400.
  - In `RunnerGate.refuse`, also refuse when `len(raw) > MAX_BODY_BYTES` on the forwarded bytes.

**L4 (low). A pending offer is never counted when a turn is discarded or aborted.**
- **Where:** `output_check.py:1416-1420` and `:1454-1455`, against `reset(discard=True)` at `:849-853` and
  `abort()` at `:856-858`.
- **Rule:** hard rules 2 and 5; counts are evidence.
- **What is wrong:** an `offer` match is counted only when its sentence ends, through `_blocking_class`. A
  replaced class is counted at match time. If the attempt is cut while an offer is still open, `_clear()` throws
  the state away with no count.
- **How it fails:** the model streams "I can check the price: it's 45 dollars" with no full stop yet, then
  `reset(discard=True)` or `abort()` runs. `counts` gets neither `money` nor `offer`. No text is released (fail
  closed); only the evidence is short.
- **Unconfirmed:** whether counts from cut attempts are reported anywhere.
- **Fix:** in `reset` and `abort`, when `_dropping` is a dropped class, count
  `_blocking_class(self._consumed, len(self._raw)) or "offer"` before `_clear()`.

### Area answers

**1. Offer drop: no finding beyond L4.**
- **`_skip_space` (`:1441-1449`):** it moves `_consumed` and `_plain_since` past spaces and tabs only. It never
  emits, and never skips `\n`, `\r` or a non-space character. A payload cannot sit in the skipped gap.
- **Next sentence:** text after the dropped sentence goes through `_first_match` as normal. A match that starts in
  the dropped sentence is clamped by `_sentence_start` to `_plain_since`, so the Reset arithmetic is not
  corrupted.
- **Undecided offers:** an open offer holds everything from its sentence start until a boundary or `final`, and
  is never released while undecided. The Reset path for words released before the match completes is the
  existing mechanism, and a test covers it.
- **`reset()`, `abort()`, `_clear()`:** they clear `_skip_space` and the dropping state, so held text is
  discarded (fail closed).
- **`_blocking_class` against `_first_match`:** it uses the same patterns, plain and marked views, exact
  haystacks, `_exempt`, duration gate and `_vouched`, `_unparsed_duration` (with `_undecided` restored), the link
  scan, and earliest-start selection. The differences:
  - Its view starts at the sentence start, so a lookbehind exemption (conditional, negation, weight) sees no more
    context than before. That can only block more.
  - It runs with `final=True` on a complete sentence, which matches what `_first_match` decides at a boundary.
- **One count per sentence:** a second payload in an offer sentence is not counted separately. That is the same
  as the existing rule for replaced classes, so it is not a regression.
- **New TRIGGER words:** they only add holding (latency). "can" and "may" are not triggers, but `\bi['’\s]` right
  after them holds the rest; at most "Can"/"May" goes out and is taken back by a Reset.

**2. Proxy reorder: the user's condition holds on every path. Findings L1 to L3 apply here.**
- **The bytes every step reads:** after `proxy.py:345`, every step uses the reordered `raw` or `body`:
  - scrub, key and hash (`:349-366`)
  - the log body, which is the scrubbed key
  - `gate.refuse(raw, body)` (`:417`)
  - the reservation `len(raw)` (`:497`)
  - `request_leaks(raw)` (`:519`)
  - the upstream `content=raw` (`:547`)
  - the cassette put, which runs the PAN and secret scan on the scrubbed reordered key
- **Nothing reads the original:** nothing re-reads `request.body()`. `content-length` is not in
  `FORWARD_HEADERS`, so it cannot go stale.
- **Paths checked:**
  - Record, replay, live and `via_runner`: covered by the above. In `via_runner` mode the sandbox reorders first,
    and the runner's own `Proxy` then sees an ordered body and leaves it untouched.
  - The runner's live proxy: it is the same `handle`.
- **Escapes and `ensure_ascii=False`:** decoding escapes only makes the raw-text scan stronger, and
  `readable_forms` already decodes JSON.
- **NaN, huge integers and nesting depth:** `NaN` round-trips unchanged. Integers over 4300 digits and deep
  nesting already raise in `json.loads`, before this change.
- **`_POSITIONAL`:** a permutation of the slots. The number and placement of `cache_control` markers do not
  change, and no value is introduced that was not already in the body.
- **Key-guessing oracle:** no new response depends on the key. `request_leaks` is still the last step, after
  the reservation.
- **`miss_diff`:** replay only. It narrows to the same test's scrubbed, committed cassettes, which were already
  diffable across the suite. Nothing new leaks.

**3. `ship.sh` wait: no finding.**
- **Env settings:**
  - Both settings must match `^[0-9]{1,6}$`, then go through `10#`. The wait is capped at 600 s and the poll is
    kept between 1 s and the wait. All of this runs before any `gh` call.
  - An empty value refuses.
  - A `SECONDS` value set in the environment only moves the base, not the interval.
- **PR-controlled names:** check and workflow names come through `@tsv` and `read -r`. They are only compared
  with quoted `[[ == ]]`, never executed. The refusal message prints only the `REQUIRED_JOBS` constants.
- **Merging without the required checks:**
  - A failed or empty poll means "none yet" and refuses at the deadline. It is never treated as success.
  - After the wait, the unchanged bucket read still refuses any non-`pass` entry and any missing required job. A
    check seen in the poll but missing from the bucket read refuses.
- **macOS bash 3.2:** fine. It uses unquoted `=~` with an ERE, `10#`, `$'\t'`, and `(( )) ||` under `set -e`.
- **Informational:** `2>/dev/null` at `:236` means a `gh` auth or network failure shows up as "CI has not
  started" after up to 600 s. It fails closed but the message is misleading.

### Checked, no findings

- **Hard rule 1 (no card data in the model):** the PAN scan runs on the reordered scrubbed body in both orders
  (test); no card path changed.
- **Hard rule 2 (price and stock only from live tools):** the live-price and live-stock exemptions are applied
  the same way in `_blocking_class`; nothing new is released.
- **Hard rule 3 (synthetic data only):** the tests use the sandbox PAN `4111…` and a fake key built at runtime.
- **Hard rule 4 (secrets):** nothing prints or logs a secret; `miss_diff` exposes only scrubbed cassettes;
  `ship.sh` prints no token.
- **Hard rule 5 (never invent attributes):** the offer rule only drops text.
- **Hard rule 6 (retrieved text is data):** the reorder moves whole tool-result blocks and grants no tool.
- **Hard rule 7 (internal documents and regions):** not applicable to this change (`_vouched` and its source
  filtering are unchanged).
- **Hard rule 8 (least privilege):** not applicable to this change.
- **Hard rule 9 (idempotency and monetary actions):** not applicable to this change.
- **Hard rule 10 (structured tool errors):** not applicable to tools. The proxy-side unstructured 500 is noted
  under L3.
- **Hard rule 11 (AI disclosure):** enforced by the static portal component `apps/portal/src/AiDisclosure.tsx`,
  which model text and the offer drop cannot remove.
- **0001, placeholder key only for child processes, real key only in the proxy:** not touched (`llm.py`
  unchanged). The new e2e tests use loopback `ScriptedLLM` and a fake key.
- **0001, the Makefile does not export the key:** not applicable to this change (Makefile not in the diff).
- **0001, no key in CI secrets, CI runs in replay:** not applicable to this change (no workflow change).
- **0001, the key file is never copied into a worktree:** not applicable to this change.
- **0001, budget caps enforced and not raised:** the reservation is on forwarded bytes (test-pinned), the cap
  values are unchanged, and there is no bypass (see L3).
- **0001, cassettes still covered by the secret/PAN scanner:** the cassette put and scan path is unchanged;
  there is a PAN test in both orders.

### Outside this diff (unconfirmed)

The git status snapshot from the start of the session shows many staged deletions under
`cassettes/eval-answers/`. They are not in the reviewed diff, and I cannot run git to confirm their current
state. `cassettes/**` is control surface, and a deleted cassette makes a row non-routine. Confirm these
deletions belong to the planned W6 re-record and that they are listed in the PR body and the gate report, not
swept into this commit by accident.

Files: `/Users/rapido/Projects/northwind-shop/services/llm-replay/src/northwind_llm_replay/tool_order.py`,
`/Users/rapido/Projects/northwind-shop/services/llm-replay/src/northwind_llm_replay/proxy.py`,
`/Users/rapido/Projects/northwind-shop/services/llm-replay/src/northwind_llm_replay/runner.py`,
`/Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/output_check.py`,
`/Users/rapido/Projects/northwind-shop/tools/ship.sh`
