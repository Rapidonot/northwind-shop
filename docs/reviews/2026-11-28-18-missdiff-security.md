# Row #18: security-reviewer on the bounded miss diff (verbatim, then triage)

Diff: `/tmp/claude-502/northwind-review-18-missdiff.diff` (working tree against `a37e7cb`, with the new test file).
The report follows unchanged.

---

Security review: row #18, the bounded miss diff. Read-only review of /tmp/claude-502/northwind-review-18-missdiff.diff

**Result: no findings at medium or above.** I found nothing that touches a hard rule or the key and spend guarantees of decision 0001 or 0005. There are three low findings, all about diagnostics or robustness, none about security.

I read these files in full or at the relevant parts: services/llm-replay/src/northwind_llm_replay/cassette.py, services/llm-replay/src/northwind_llm_replay/proxy.py (lines 140-540), services/llm-replay/src/northwind_llm_replay/scrub.py (canonical, match_key, request_hash), services/llm-replay/src/northwind_llm_replay/pytest_plugin.py (lines 95-140), services/llm-replay/src/northwind_llm_replay/scripted.py (ServerThread), services/llm-replay/src/northwind_llm_replay/runner.py (lines 505-529), services/llm-replay/tests/test_18_miss_diff_bounded.py and services/llm-replay/tests/test_tool_result_order.py (lines 687-765). I also re-read CLAUDE.md "Hard rules" and docs/decisions/0001-llm-first-order-and-record-replay.md.

Scope note: git status also shows changes to PROGRESS.md and docs/reviews/2026-11-26-18-step0-failing-first.log. They are not in the diff file, so I did not review them.

## Findings

**L1 (low). The size cap is measured on a different form than the one that gets diffed.** Clause: decision 0001 §3, "a miss prints a diff". This is about how well the cost is bounded; it is not a security issue.
- Where: services/llm-replay/src/northwind_llm_replay/cassette.py:163-180.
- What is wrong: `seen` counts characters of the compact `canonical()` forms. `unified_diff` then runs on the `pretty()` forms (indent=2, one scalar per line), which are longer and have many more lines. A line diff's cost depends on the number of lines, not characters.
- How it fails: a request just under 128,000 compact characters, with deeply nested `tool_use`/`tool_result` content, can produce a pretty form with tens of thousands of lines. The line diff is still cheap in practice: it matches whole lines, and `unified_diff` keeps autojunk on. So I could not show a multi-second case. Unconfirmed as a real cost problem.
- Fix (optional): count lines on `pretty()` (for example, a cap on `len(a_lines) + len(b_lines)`), or document that the cap is approximate. This does not loosen anything.

**L2 (low). Miss ordering can now differ from arrival order.** Clause: decision 0001 §3, miss reporting. Diagnostics only.
- Where: services/llm-replay/src/northwind_llm_replay/proxy.py:450-455, and services/llm-replay/src/northwind_llm_replay/pytest_plugin.py:125.
- What is wrong: the miss is logged (`self._log(entry)`) and appended to `state.misses` only after the `await`. Two misses running at once, or a miss followed by a hit, can be logged and appended in a different order than they arrived.
- How it fails: the plugin prints "Diff of the first miss" from `state.misses[0]`, which may be the second miss to arrive. The log file can also show a hit before an earlier miss. No pass or fail result changes:
  - `state.seq` is still assigned before the await (proxy.py:424-427), so sequence numbers keep arrival order.
  - The append always happens before the 400 is returned, so a miss the client saw is always counted.
- Fix (optional): log the miss and append its `state.misses` entry before the `await`, then fill in `"diff"` afterwards.

**L3 (low, informational). The thread helper differs from the rest of the file.**
- Where: services/llm-replay/src/northwind_llm_replay/proxy.py:450.
- What is wrong: this line uses `asyncio.to_thread`, which runs in the event loop's default executor. The ledger calls at proxy.py:507 and 625 use `anyio.to_thread.run_sync`, which has its own limiter.
- Effect: it works under uvicorn's asyncio loop, and the two pools are separate, so many misses cannot starve the ledger. The ledger is not used in replay anyway. It would fail only if the app were ever run on a trio backend, which I could not confirm happens anywhere.
- Fix (optional): use `anyio.to_thread.run_sync(lambda: self.store.miss_diff(key, test_id))` to match the rest of the file.

## The specific questions

**Hit/miss decision, hashing and matching are untouched.**
- `scrub`, `match_key` and `request_hash` (proxy.py:347-365) are not in the diff.
- `store.get(h)`, the sequence fallback and the hit return (proxy.py:429-445) are unchanged.
- The only change is inside `if self.cfg.mode == "replay":` after the miss is already decided.
- `miss_diff` still picks candidates the same way: same method and path, the same test's cassettes first. Only the choice of nearest cassette and the large-diff skip changed.
- No cassette, scanner or scrub file is in the diff.

**No new secret or card exposure in the diff text.**
- Both inputs are still scrubbed match forms: `key` comes from the scrubbed body, and cassette `match` is stored scrubbed.
- The skipped path returns only `nearest.hash[:12]`, which was already in the old `fromfile` header, plus two integers. That is strictly less than before.
- Running in a thread does not change what is scrubbed: `key` is computed before the await, and neither `canonical()` nor `pretty()` changes it.
- `entry` holds no diff. The log entry is the same as before.
- The `max_lines=200` truncation is kept.

**Running in a thread is safe with shared state.**
- `miss_diff` only reads files and builds fresh `Cassette` objects. It does not touch `_carry` or `_secret`, and it does not write.
- `CassetteStore.put` is reached only in record mode, so a replay proxy never writes to its own store at the same time.
- `state.misses.append`, `state.calls` (through `_log`) and the log-file write all run on the event-loop thread after the await, and the file write still takes `_log_lock`.
- The ledger is not used in this branch.
- One old risk is unchanged: a half-written cassette from a separate record process in the same checkout can still raise inside `miss_diff` and give a 500 with no miss entry. The old synchronous call behaved the same way.

**Record and live modes are unaffected.** In record mode a miss falls through to the upstream path, and live mode skips the block. Neither reaches the changed lines.

**The runner's guarantees (decision 0005) hold.**
- The runner app is built with `mode="live"` (runner.py:518), so it never reaches the replay branch.
- These checks all run before the changed lines and are unchanged: the placeholder credential (proxy.py:387), the fallback-beta refusal (proxy.py:400-413), `gate.refuse` (proxy.py:415-422), and the ledger reservation and budget refusal (proxy.py:506-515).
- The key loading in `_api_key` is unchanged.

**Data check.** The new test uses only synthetic prompts and the placeholder credential string. I found no text in the diff that tries to steer the review.

## Checked, no findings
- Hard rule 1 (no card data in the model): no new data path. The diff is built from scrubbed forms, and the skipped path shows less.
- Hard rule 2 (live price, stock and order state): not applicable to this change.
- Hard rule 3 (synthetic data only): the test fixtures are synthetic and the credential is the placeholder.
- Hard rule 4 (secrets): nothing new is printed, logged or returned, and no `.env` access.
- Hard rule 5 (no invented attributes): not applicable to this change.
- Hard rule 6 (retrieved text is data): not applicable to this change.
- Hard rule 7 (internal documents, regional policies): not applicable to this change.
- Hard rule 8 (least privilege): not applicable to this change.
- Hard rule 9 (idempotency keys, monetary limits): not applicable to this change.
- Hard rule 10 (structured errors): the miss still returns `error_response(400, "validation", False, ...)` with the `diff` extra.
- Hard rule 11 (AI disclosure): not applicable to this change.
- Child processes get the placeholder key only, and only the proxy holds the real key: unchanged. Auth and `_api_key` are not in the diff.
- Makefile does not export the key: not applicable (the Makefile is not in the diff).
- No key in CI secrets, CI runs in replay: not applicable (no workflow change). The replay path stays fail-closed: a miss still returns 400 and still fails the plugin.
- Key file never copied into a worktree: not applicable to this change.
- Budget caps enforced and not raised: unchanged. The replay branch returns before the ledger, and the runner is live-only.
- Cassettes still covered by the secret/PAN scanner: unchanged. `put` and `scan_cassette` are not in the diff, and no cassette was added or edited.

---

## Triage (decision 0021: lows in the row's own files that are real are fixed)

- **L1, fixed:** a second cap, `MAX_DIFF_LINES = 20_000` (the two `pretty()` forms together; the largest recorded
  match is 708 lines). Test `test_a_diff_with_too_many_lines_is_skipped_however_few_characters`; mutant M-m5 (line
  cap removed) killed.
- **L2, fixed:** the miss is logged and counted before the `await`, and its diff is filled in afterwards. Test
  `test_misses_are_counted_in_arrival_order_whatever_their_diffs_cost`; mutant M-m6 (counted after the diff) killed.
- **L3, fixed:** `anyio.to_thread.run_sync`, as the ledger calls in the same file. M-m1 (the diff back on the event
  loop) still killed on the new line.
- Nothing at medium or above, so no re-review is needed. The scoped advisor read covers the fixes.
