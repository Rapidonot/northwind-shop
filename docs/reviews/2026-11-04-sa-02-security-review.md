# SA-02 (crude) security review, first pass

Date 2026-11-04. Reviewed the branch against `origin/main` plus uncommitted work, before any
recording. 3 high, 2 medium, 4 low (1 unconfirmed). Key handling and spend controls: no findings.
The fixes below have not been re-reviewed. A second pass after recording covers the cassette, the
price-question test, `make e2e` and these fixes.

| # | Severity | Finding | Decision | What happened |
|---|---|---|---|---|
| 1 | High | The CLI wrote every accepted message to a transcript under its config dir; no scanner covers that path, and `test_an_accepted_message_is_not_logged` could not see it | Fixed | `agent_options()` always passes `--no-session-persistence`. Tests assert no `.jsonl` and no file holding the message after a turn. Seen failing with the flag removed |
| 2 | High | CVVs were not checked at all | Fixed | `contains_cvv`: a 3 or 4 digit number next to a security-code name. A bare number cannot be told from any other. Expiry dates alone are not refused: hard rule 1 names PAN and CVV |
| 3 | High | A PAN passed with 4+ separator characters, words between groups, or circled digits | Fixed, with a limit | NFKC first; standalone groups of 3+ digits up to 12 characters apart. **Limit, accepted by the user:** digits spelled out or touching letters still pass; hosted fields are the real control (PAY-05). The wider over-refusal is accepted because every starter-set query and the PRD §2 example pass |
| 4 | Medium | Card check only on the inbound message | Part fixed | Tool arguments now refused, with fixed text and no search. Model output: moved to PAY-05 (Window B). Scanner patterns: already row #18 |
| 5 | Medium | Rules 2 and 5 rest on the prompt; ungrounded text stays on screen | Part fixed | `retrieval_failed` now clears the text. Uncited claims and invented markers left in the text: row #13. Promotions in retrieved text: row #12. The price test now checks promotion wording itself |
| 6 | Low | `make e2e` could reach a spending proxy through an inherited `LLM_PROXY_URL`; fixed port | Part fixed | Playwright sets the URL. The fixed port stays, a deviation from decision 0001 accepted by the user: 8000 and 5173 are fixed anyway, so e2e runs in one worktree at a time |
| 7 | Low | No rate, concurrency or wall-clock limit on `/api/chat` | Deferred | NFR-14 (Window B). The gateway binds to loopback, and the proxy budget caps still hold |
| 8 | Low | `splitlines()` could leave a hole in reassembled text | Fixed | Splits on `\n` only. Secrets split across blocks or requests are still not rebuilt |
| 9 | Low, unconfirmed | `@path` attachment blocked only by `--bare`, with no test | Fixed | Test added. Removing `--bare` made it fail, so the attachment is real and `--bare` is what stops it |

Also from `test-writer`, fixed before this review: separators the card check missed, an error code
named like an object property crashing the portal, and a cut stream reported as a finished answer. A
cut stream still ends in `message_stop` and a `success` result, so a stop reason in `message_delta` is
the only sign an attempt finished.

## Second pass, after recording

Reviewed the branch including the `assistant-smoke` cassettes. 1 high, 2 medium, 8 low; the four
cassettes are clean. First-pass fixes: 1, 6, 8 and 9 hold; 2, 3, 4 and 5 partly hold, with the gaps
below. These fixes have had no third review.

| # | Severity | Finding | Decision | What happened |
|---|---|---|---|---|
| 1 | High | A regression from this branch: `reassembled-api-key-fragment` escaped `cmd_scan`'s exact match, so a split key hit printed its path and no ROTATE line, against decision 0005 | Fixed | `scan_stream` keeps the kind for key fragments, in `scan.py`; `cli.py` untouched. Test runs a split key through `cli.main` |
| 2 | Medium | Everyday CVV forms passed | Fixed | More names, separators in names, spaced digits, a 40-character gap that may hold digits |
| 3 | Medium | The price test does not prove hard rule 2 | Accepted, reworded | True: its docstring now says it is a smoke test, and row #12 says it isn't evidence. It now checks the turn finished |
| 4 | Low | PAN forms beyond the accepted limit: groups under 3 digits with wide separators, keycap emoji, `(4)(2)`, groups over 12 characters apart, dingbat digits | Accepted by the user | Hosted fields are the real control (PAY-05) |
| 5 | Low | The tool-argument card check had no carry-over to mcp-rag | Recorded | Row #10 names it and the region filter |
| 6 | Low | Text clearing was skipped when another error won | Fixed | Clears on any error when search failed and no source came back |
| 7 | Low | A stream ending with no final event showed nothing | Fixed | The portal shows the fixed error |
| 8 | Low, unconfirmed | Any `message_delta` counted as finished | Fixed | Only `end_turn` and `tool_use` count, so `max_tokens` and `refusal` are errors |
| 9 | Low | Reassembly residuals had no home | Recorded | Row #18 |
| 10 | Low | `extra_args` left other controls open | Fixed | `agent_options()` has no `**kwargs`: an explicit allowlist, `permission_mode` limited to `dontAsk`, anything else a TypeError. The flags behind the refused options are pinned in `extra_args` |
| 11 | Low | Local identifiers and old transcripts | Noted | `.llm-proxy-log/` is gitignored; pre-fix transcripts in the temp dir hold only test text. The user can delete them |
