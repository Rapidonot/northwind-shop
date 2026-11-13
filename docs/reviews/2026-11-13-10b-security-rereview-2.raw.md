# Security re-review (scoped), pass 3 (final): row #10b (mcp-catalog)

> Saved verbatim from the security-reviewer's report. In P3's fix, the reviewer wrote the two escapes as `\u2028` and `\u2029`; its output rendered them as raw line breaks, so they are written here as escapes.

**What I read.** First I re-read `CLAUDE.md` "Hard rules" and `/Users/rapido/Projects/northwind-shop/docs/decisions/0001-llm-first-order-and-record-replay.md`. Then I read my pass-2 report `/Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-13-10b-security-rereview.raw.md`, and the file list, header and PROGRESS/COVERAGE hunks of `/tmp/claude-502/northwind-security-10b-r3.diff`.

I read these working-tree files in full:
- `data/src/northwind_data/ingest_guard.py`
- `data/tests/test_ingest_guard.py`
- `services/mcp-catalog/src/northwind_mcp_catalog/live.py`
- `services/mcp-catalog/tests/test_live.py`
- `services/mcp-catalog/tests/test_logs.py`
- `services/rag/tests/test_ingest_verify.py`
- `services/rag/src/northwind_rag/ingest.py`

I also read:
- `services/common/tests/test_mcp_token.py:288-374`
- `services/mcp-rag/src/northwind_mcp_rag/server.py:225-285`
- `docs/governance/threat-model.md:85-87` and `:220-227`
- the fixture price row for `P0001-BLK-REG-L`/UK
- the imports of `verify` under `services/assistant/`
- the repo's existing `O_NONBLOCK` uses (`scan.py:230`, `promotion.py:146`)

I ran nothing, because I have no Bash tool. Every "fails on the mutant" or "passes" claim depends on `make ci` and your mutant logs. I changed nothing.

**Result: nothing above low.** No critical, high or medium findings, and nothing unsafe. There are 3 new low findings (P1 to P3). All seven N dispositions are adequate. Two of them have small residuals, listed below.

---

## 1. Findings, most severe first

### P1 (low): N6's structural fix still needs an owner, and the AST pin still has dynamic gaps
- **Where:** `services/common/tests/test_mcp_token.py:302-342`; `services/common/src/northwind_common/mcp_token.py:158` (public `verify`)
- **Rule:** L4 / NFR-01 (hard rule 8)
- **What is wrong:** every form you listed is now flagged, and the self-test at `:361-373` pins them. These forms are still not flagged:
  - `getattr(northwind_common.mcp_token, "verify")`: `args[0]` is an `Attribute`, not a `Name`, so `:327` does not match.
  - `m = importlib.import_module("northwind_common.mcp_token")` followed by `m.verify`: `m` is never added to `modules`.
  - `vars(mcp_token)["verify"]`, `mcp_token.__dict__["verify"]`, `operator.attrgetter("verify")`, or `getattr(mcp_token, name)` with a non-constant name.
- **How it fails:** a future gate built through one of these forms accepts tokens minted for another server, and the pin stays green. Only deliberate indirection gets past it now, not an ordinary mistake, so it stays low.
- **Fix:** the deferral reason checks out. `services/assistant/tests/test_rag_client.py:23,108` does import `verify`, and `services/assistant/**` is off-limits this row. Give the rename (`verify` to `_verify`, then pin that `verify` is not a public name of the module) to #12. That row already owns `rag_client.py` (the `mint` default) and can update `test_rag_client.py` at the same time. Put it in the #12 row or in "Open items" so it has an owner. "Not done" with no owner is not enough.

### P2 (low): the N3 catalog test pins only `prices.jsonl`
- **Where:** `services/mcp-catalog/tests/test_live.py:141-160`
- **Rule:** L2 / Q4
- **What is wrong:** only the `prices.jsonl` bytes are patched. A partial revert that re-reads `products.jsonl`, `variants.jsonl` or `stock.jsonl` from disk, and keeps the verified prices, passes the test. On the ingest side, the sentinel test covers `ingest()` but not `ingest_enriched()`. Both go through `_sync`, so M9 is caught, but a revert confined to `ingest_enriched` (`ingest.py:54-56`) is not.
- **How it fails:** a later refactor reverts the fix for one of the other three files and nothing goes red.
- **Fix, optional:**
  - Patch a distinct value into each of the four parsed files (a product name, a variant SKU, a stock count) and assert each one.
  - Or make `Path.open`, `Path.read_bytes` and `builtins.open` raise inside the test once the patched `load_verified` has returned.
  - Optionally run the sentinel test over both ingest entry points, using the existing `enriched_migrated` fixture.

  The test itself is sound: the fixture price is `113.98` (`data/fixtures/prices.jsonl:3`), not `1.23`, so M8 is killed.

### P3 (low): the N5 test relies on invisible raw characters in the source
- **Where:** `services/mcp-catalog/tests/test_live.py:167-168`
- **Rule:** N5 (test robustness)
- **What is wrong:** I checked the bytes: `x<U+2028>y<U+2029>z` occurs twice on those lines, and U+0085 is written as the escape `\u0085`. The coverage you claimed is real. But the U+2028 and U+2029 characters are raw, so an editor, a paste or a normalising tool could silently turn them into spaces. The test would then stop covering two of its three characters, and still pass.
- **Fix:** write them as `\u2028` and `\u2029` escapes, like `\u0085`.

---

## 2. Review of the changed lines (your item 1)

**`O_NONBLOCK` semantics: sound on both platforms.**
- **FIFO:** `open(O_RDONLY|O_NONBLOCK)` returns at once on Linux and macOS (POSIX). Only `O_WRONLY|O_NONBLOCK` with no reader fails, with `ENXIO`. `fstat` then sees `S_IFIFO`, the `OSError` at `ingest_guard.py:90` is raised, `finally` closes the fd, and `load_verified` turns it into `"<name>: missing or not a regular file"`. A FIFO at `manifest.json` gives `"manifest.json is missing or unreadable"`.
- **Regular files:** on local filesystems, Linux and macOS ignore `O_NONBLOCK` for `read`. This matches the repo's existing pattern (`scan.py:230`, `promotion.py:146`).
- **Other file types:**
  - A directory opens and is refused by `S_ISREG`.
  - A socket file fails `open` with `ENXIO`/`EOPNOTSUPP`, which is an `OSError` and is caught.
  - `O_NOFOLLOW` still raises `ELOOP` on a final-component symlink.
  - `O_NOCTTY` exists on both platforms. On macOS it is a no-op, which is harmless.
- **fd ownership:** unchanged and correct. There is no leak on the new raise path.
- **mcp-rag:** `_dataset_verified` (`server.py:270-274`) catches `Exception`. A FIFO now gives `False` at once instead of parking an executor thread, so the starvation path from pass-2 N1 is closed.

**FIFO test (`test_ingest_guard.py:223-248`): it cannot hang CI or leak a thread on the fixed code.**
- **Fixed code:** `verify_dataset` returns or raises at once, the thread ends, and `join(timeout=5)` returns early.
- **On a regression:**
  - The daemon thread stays blocked in `open(2)` with the GIL released.
  - The test fails after 5 s with a clear message, and pytest carries on.
  - The interpreter abandons the daemon thread at exit, so the process does not hang.
  - Cleaning up `tmp_path` unlinks the FIFO without opening it, so it does not block either.
  - The blocked thread is the only leak, and only on a regression.
- **Optional tidy-up:** on failure, release the thread by opening the FIFO with `O_WRONLY|O_NONBLOCK`. That succeeds once a reader is waiting.
- **Coverage:** `[result] = outcome` runs only after the liveness assert, so there is no race. `stock.jsonl` is reached after the other listed files pass, so the test exercises the FIFO branch and not an earlier mismatch.

**`_jsonl` (`live.py:115-118`).** It splits on `b"\n"`, and `bytes.strip()` drops only ASCII whitespace. A trailing `\r` is valid JSON whitespace. The writer escapes raw `\r`/`\n` inside values, so mcp-catalog and ingest's text-mode iteration (which splits on `\n`, `\r` and `\r\n`, not on U+2028) now read the same records. There is no hard-rule-2 effect: the same four files are parsed, and price bands are still dropped.

**`test_logs.py` (N2).**
- `Formatter().format(r)` includes `exc_text` and `stack_info`.
- `:70` refuses any server-side record that has `exc_info` or `stack_info`.
- The not-vacuous asserts (`tool failed`, `tool refused`, `request refused: token`) remain.
- The needles cover the mark, the full PAN, the token, its signature, the DSN and `hunter2`. Dropping the bare `"4242"` weakens nothing, because the 16-digit PAN is still checked.

**`ingest.py`.** Unchanged in this pass apart from what N3 pins. The sentinel test patches the module global that `ingest()` calls, so M9 is killed.

---

## 3. Dispositions of N1 to N7 (your item 2)

| Finding | Verdict | Note |
|---|---|---|
| N1 | Adequate | The flags are correct on both platforms, and the test is bounded at 5 s. A regression fails it and does not hang CI. |
| N2 | Adequate | See section 2. See unconfirmed item 2 for a possible flake on the `exc_info` assert. |
| N3 | Adequate, with residual P2 | M8 and M9 are each killed by a test that fails on them. Partial reverts are not pinned. |
| N4 | Adequate (docs) | `threat-model.md:87` now limits the claim to "the verification read". The open item at `:225` names the chunker re-reads, symlink-following on re-read, `data_version` from a re-read, and last-component-only guarding. Owner "needs the user": same caveat as pass 2. It is a parking label, so a row owner would be better. Nit: `:87` cites `ingest_guard.py::verify_dataset` for "mcp-catalog parses the verified bytes", but mcp-catalog calls `load_verified`. |
| N5 | Adequate, with residual P3 | The fix is correct, and a `splitlines()` mutant would raise `JSONDecodeError` in the test. |
| N6 | Adequate for this row, with residual P1 | Every listed form is flagged and self-tested, and `verify_for` is not flagged. The deferral reason is verified. The rename needs an owner. Harmless over-match: a plain `import northwind_common.mcp_token` adds `northwind_common` to `modules`, so `northwind_common.verify` would be flagged. |
| N7 | Adequate (docs) | The `load_verified` docstring (`ingest_guard.py:105-109`) and `threat-model.md:87` both say the hash covers the listed files' bytes, not the manifest's own fields. |

**Row-end reminder (housekeeping, not a security finding).** The `PROGRESS.md` row #10b note says "6 lows … and one scoped re-review". "Open items" (from `PROGRESS.md:152`) has no #10b entry yet. Before `/req-done`, add:
- passes 2 and 3 with their lows;
- the L2 remainder, L3 and the N4 open items;
- P1 with its owner (#12, as proposed);
- P2 and P3, if they are not fixed first.

---

## 4. Checked, no findings

**Hard rules**
- 1 (card data): no new path. `test_logs.py` is stronger, now reading tracebacks and refusing `exc_info`.
- 2 (live price and stock): `_jsonl` parses the same four verified files. Nothing reads the index, and price bands are not used.
- 3 (synthetic data): only `4242…`, markers, a fake DSN and synthetic fixture rows.
- 4 (secrets): no key or env handling changed, and no `.env*` file is read.
- 5 (no invented attributes): not applicable to this change.
- 6 (retrieved text is data): no change in this pass. L6 is still owned by #12.
- 7 (internal-only, regions): not touched in this pass.
- 8 (least privilege): pin strengthened (N6). The remaining gap is P1, and L3 is still logged.
- 9 (idempotency, limits): not applicable. No state-changing tool.
- 10 (structured errors): no change. `_dataset_verified` now returns `False` promptly on a FIFO and no longer blocks.
- 11 (AI disclosure): not applicable to this change.

**Decision 0001 checklist** (all not applicable: the diff does not touch `llm.py`, `llm-replay`, `Makefile`, `.github/workflows/**`, `LLM_ENV_FILE`/`.env`, the ledger, the budget caps, the scanner or `cassettes/**`)
- Child processes get a placeholder key only: not applicable.
- The Makefile does not export the key: not applicable.
- No key in CI secrets, and CI runs in replay: not applicable.
- The key file is never copied into a worktree: not applicable.
- Budget caps are enforced and not raised: not applicable.
- Cassettes are still covered by the secret/PAN scanner: not applicable.

**Prompt injection:** nothing in the diff, the fixtures, the saved reviews or the diff's header note tries to steer this review.

---

## 5. Unconfirmed

1. **`O_NONBLOCK` on unusual filesystems.** On some network or FUSE mounts, a regular-file `read` might return `EAGAIN`. `FileIO.readall` would then return `None`, and `_sha256(None)` would raise `TypeError`. That escapes `load_verified` as a non-`DatasetMismatchError`. It still fails closed: ingest and mcp-catalog abort, and mcp-rag returns `False`. I found no evidence that any supported setup hits this. If you want it closed, clear `O_NONBLOCK` with `fcntl(F_SETFL)` once `S_ISREG` passes.
2. **A possible flake on `test_logs.py:70`.** If uvicorn or the MCP SDK logs a benign connection error with `exc_info` during shutdown, the test fails. That fails loud and hides no leak. I did not check those libraries' shutdown logging.
3. **Mutant results** (the FIFO mutant without `O_NONBLOCK`, M8, M9) and all test passes: these are your claims. I could not run them, so confirm them in `make ci` and the mutant log.
