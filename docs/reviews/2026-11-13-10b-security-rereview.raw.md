# Security re-review (scoped): row #10b (mcp-catalog), pass 2

**What I read.** I re-read `CLAUDE.md` "Hard rules" and `docs/decisions/0001-llm-first-order-and-record-replay.md` first, then my pass-1 report `/Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-13-10b-security-review.raw.md` and the diff `/tmp/claude-502/northwind-security-10b-r2.diff`. In the diff I read: `ingest_guard.py`, `test_ingest_guard.py`, the governance docs, `test_mcp_token.py`, mcp-rag `server.py` and `test_manifest.py`, `ingest.py`, `test_index.py`, mcp-catalog `app.py`, `live.py`, `test_live.py` and `test_logs.py`, and `test_ingest_verify.py`. In the working tree I also read `data/src/northwind_data/ingest_guard.py`, `services/rag/src/northwind_rag/ingest.py`, `services/rag/src/northwind_rag/sources.py` (`load_records`, `build_chunks`, `_read_jsonl`), `data/fixtures/manifest.json`, `generate.py:49-66`, `writer.py:22` and the `PROGRESS.md` "Open items" section.

I ran nothing (I have no Bash tool), so every "test passes" claim below depends on `make ci`. I made no changes.

**Result:** no critical, high or medium findings. There are 7 new low findings (N1 to N7), and all six pass-1 dispositions are acceptable for a low, with the caveats below.

---

## 1. Findings, most severe first

### N1 (low): `_read_regular` blocks forever on a FIFO, before the regular-file check runs
- **Where:**
  - `data/src/northwind_data/ingest_guard.py:86`: `os.open(path, os.O_RDONLY | os.O_NOFOLLOW)`
  - reached from `services/mcp-rag/src/northwind_mcp_rag/server.py:266-275` (`_dataset_verified`), and from `services/mcp-catalog/src/northwind_mcp_catalog/live.py:131` and `services/rag/src/northwind_rag/ingest.py:35,54`
- **Rule:** decision D4 / Q4, whose docstring says "Any mismatch or failure is false, never an error"; availability of `retrieve_context`.
- **What is wrong:** on both Linux and macOS, opening a FIFO `O_RDONLY` without `O_NONBLOCK` blocks until a writer opens it. The `fstat`/`S_ISREG` check only runs after `open` returns, so a FIFO is never refused. It hangs instead.
- **How it fails:**
  1. Someone with write access to `data/fixtures` (the same actor as L1) runs `mkfifo data/fixtures/stock.jsonl`.
  2. Each staff manifest read on mcp-rag then parks one thread forever inside `asyncio.to_thread` (`server.py:239`).
  3. `retrieve_context` uses the same default executor (`retrieve.py:66`). After about `min(32, cpu+4)` manifest reads, shopper searches queue behind the parked threads and hang.
  4. `make ingest` and mcp-catalog startup hang rather than fail.
- **Fix:**
  - Open with `os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY`. A FIFO open then returns at once and `fstat` refuses it. On a regular file, `O_NONBLOCK` has no effect on `read`.
  - Add a test: `os.mkfifo(copy / "stock.jsonl")` must raise `DatasetMismatchError`, run under a timeout.

### N2 (low): the L5 log test reads only `getMessage()`, so it misses tracebacks
- **Where:** `services/mcp-catalog/tests/test_logs.py:65-67`, 72-73
- **Rule:** hard rules 1 and 4 (no PAN or secret in logs); L5.
- **What is wrong:** `server_text` joins `r.getMessage()`. That leaves out `exc_info`, `exc_text` and `stack_info`, which are where an exception's message goes when a record is logged with `log.exception(...)` or `exc_info=True`.
- **How it fails:** someone changes `server.py:129` to `log.exception("tool failed")`. The traceback then carries `RuntimeError: postgresql://user:hunter2@db/prod` into the real log output. Every assertion still holds: "tool failed" is there, and `hunter2` is not in any `getMessage()`. mcp-rag's equivalent tests check `caplog.text`, which formats tracebacks (`services/mcp-rag/tests/test_server.py:257,332`). This test is weaker than the one it copies.
- **Fix:**
  - Build `server_text` from `logging.Formatter().format(r)` for each server-side record, which includes `exc_text` and `stack_info`.
  - Also assert that no server-side record has `exc_info` set.

### N3 (low): no test pins the L2 fixes
- **Where:** `services/mcp-catalog/src/northwind_mcp_catalog/live.py:131-199`, `services/rag/src/northwind_rag/ingest.py:35-36,54-56,66-71`
- **Rule:** L2 / Q4.
- **What is wrong:** both fixes behave identically to the old code whenever the files do not change between verification and use, and that is the only case the tests exercise.
  - A mutant `FixtureCatalog` that calls `load_verified(...)` and then re-reads the files with `path.open` passes all of `test_live.py`.
  - A mutant `_sync` that stores `manifest["dataset_sha256"]` instead of its `dataset_sha256` argument passes `test_index.py` and `test_ingest_verify.py`, because verification already forces the two values to be equal.
- **How it fails:** a later refactor reverts either fix and nothing goes red.
- **Fix:**
  - **Catalog:** monkeypatch `northwind_mcp_catalog.live.load_verified` to return a `VerifiedDataset` whose `prices.jsonl` bytes differ from disk. Assert that the store holds the patched price. Alternatively, make `Path.open` and `builtins.open` raise after `load_verified` returns.
  - **Ingest:** monkeypatch `northwind_rag.ingest.verify_dataset` to return a sentinel hash, and assert that `rag_index_manifest.dataset_sha256` stores that sentinel.

### N4 (low): the remaining L2 gap is wider than the open item says, and the threat-model row overstates the fix
- **Where:**
  - `docs/governance/threat-model.md:87` ("each read once without following a symlink … storing the hash it verified (L2)")
  - `docs/governance/threat-model.md:225` (the open item)
  - `services/rag/src/northwind_rag/ingest.py:66-68`
  - `services/rag/src/northwind_rag/sources.py:225` (`path.open`), 404 and 409
- **Rule:** L2 / Q4.
- **What is wrong:** after `verify_dataset`, ingest reads `manifest.json` three more times, through `_sync`'s `load_manifest`, `load_records` and `ingestible_files`. It reads every ingestible file again through `path.open`, which follows symlinks. The open item mentions only "`build_chunks` re-reads". It does not mention that those re-reads follow a symlink, or that `data_version` comes from an unverified re-read. Separately, `O_NOFOLLOW` covers only the last path component, so a symlinked `fixtures_dir` or ancestor is still followed. That is acceptable, but the docs do not say it.
- **How it fails:**
  - A file swapped for a symlink after verification gets chunked, under a hash that mcp-rag reports as `dataset_verified: true`.
  - A concurrent `make data` with a `DATA_VERSION` bump can stamp the old hash with the new `data_version`.
- **Fix:**
  - **Now (docs):** limit "each read once without following a symlink" to the verification read. Reword the open item to cover all three of these: the chunker re-reads, those re-reads follow symlinks, and `data_version` comes from a re-read. Note that only the final path component is guarded.
  - **Later:** pass `VerifiedDataset` into `build_chunks` and `_sync` and parse its `manifest.json` and file bytes. This needs `load_verified` to keep the manifest bytes too.

### N5 (low): `_jsonl` splits on `str.splitlines()`, not on `\n`
- **Where:** `services/mcp-catalog/src/northwind_mcp_catalog/live.py:116`
- **Rule:** availability (it fails closed). This is the same class of bug as the logged `parse_sse_usage` item in PROGRESS.md "Open items".
- **What is wrong:** the writer uses `ensure_ascii=False` (`data/src/northwind_data/writer.py:22`), so U+2028, U+2029 or U+0085 inside a string value is written raw. `splitlines()` splits records on those characters. Ingest's `path.open` iteration splits on newlines only (with universal newlines), so the two readers handle the same verified bytes differently. The current fixtures contain none of these characters; I grepped and found 0.
- **How it fails:** a product description containing U+2028 makes mcp-catalog fail at startup with a `JSONDecodeError`, while ingest accepts the same dataset.
- **Fix:** `data.split(b"\n")`, then decode each line. Add a test with a planted U+2028.

### N6 (low): the L4 bare-`verify` pin still has gaps
- **Where:** `services/common/tests/test_mcp_token.py:302-318`
- **Rule:** L4 / NFR-01.
- **What is wrong:**
  - The `Attribute` branch matches only when the receiver is the literal name `mcp_token`. `import northwind_common.mcp_token as tok` followed by `check = tok.verify` (a reference, not a call) is not flagged, and neither is a later `check(token, …)`. Direct calls such as `tok.verify(...)` are caught by the `Call` branch.
  - `getattr(mcp_token, "verify")` and `from northwind_common.mcp_token import *` are not flagged either.
- **How it fails:** a future gate verifies through an aliased module reference or `getattr`. Tokens minted for other servers are then accepted there, and the pin stays green.
- **Fix:**
  - **Structural, preferred:** make bare verification private in `mcp_token.py` (for example `_verify`), so `verify_for` is the only public entry point. Then pin that `verify` is not in the module's public names.
  - **Otherwise:** track the names bound to `mcp_token` by `Import`/`ImportFrom` aliases, flag `.verify` on any of them, and flag `getattr(<that name>, "verify")` and `import *` from `mcp_token`.
  - Either fix tightens the pin and loosens nothing.

### N7 (low): `dataset_sha256` does not cover the manifest's own fields
- **Where:**
  - `data/src/northwind_data/ingest_guard.py:110-143`
  - `generate.py:49-66` (`manifest.json` is not in `files`)
  - `services/rag/tests/test_index.py:399-296`, i.e. `test_version_change_…` (hunk at diff lines 1279-1296), which edits `data_version` after `rehash` and still verifies
- **Rule:** L1 (the docs' precision).
- **What is wrong:** `data_version`, `ingestible`, `live_tables`, `config` and `never_ingest` can be edited without re-signing, and `verify_dataset` still passes. Hard-coded checks limit the impact: `EXPECTED_FILES` (`sources.py:407`), `LIVE_TABLES` and the `_` prefix rule (`ingest_guard.py:28,51-54`). I found no path from such an edit to a new file being ingested.
- **How it fails:** an edited `data_version` produces an index version with a wrong number over the same content, and mcp-rag reports it as verified.
- **Fix:** add one clause to the L1 wording (the threat-model row and the `load_verified` docstring): the hash covers the listed files' bytes, not the manifest's other fields. Optionally, include a canonical encoding of those fields in the dataset hash at the next `DATA_VERSION` bump. That is a generator change, so it is for a later row.

---

## 2. Dispositions of L1 to L6

| Finding | Verdict | Note |
|---|---|---|
| L1 | Adequate | The threat model (`:87`), the tool register and the `load_verified` docstring now say it is a consistency check, not authentication, and name the CI anchor. The optional `DATA_VERSION` pin was offered as optional. Add N7's clause. |
| L2 | Partly adequate | The catalog parses the verified bytes (`live.py:131`), and `_sync` stores the verified hash. The single `O_NOFOLLOW` open plus `fstat` is correct apart from N1. The fixes are not pinned (N3), and the open item understates the remaining gap (N4). "needs the user" is a parking label, not an owner. A row owner (for example hardening or #18) would be better. |
| L3 | Adequate as logged | Open item at `threat-model.md:224`, owner "needs the user". The design change is rightly not made in this row. |
| L4 | Improved, not closed | It now scans every root, plus aliased `ImportFrom` and `mcp_token.verify`, with a self-test (`test_mcp_token.py:288-343`). Gaps remain (N6). |
| L5 | Adequate in coverage, weak in assertion | Every tool, refusal paths, a failing store, a refused token, root at DEBUG, and the expected log lines are asserted, so the test is not vacuous. Tracebacks are not checked (N2). |
| L6 | Adequate | Open item at `threat-model.md:223`, owner #12. |

**Still to do at row end:** `PROGRESS.md` is unchanged in the working tree. The pass-2 lows, the L2 remainder, L3 and L6 (row 12) still need to go into `PROGRESS.md` before `/req-done`.

---

## 3. Checked, no findings (this pass's scope)

**`load_verified` and `_read_regular`**
- **fd handling:** no leak and no double close. The `finally` closes the fd if `fstat` or `fdopen` raises, and `fd = -1` hands ownership to the file object.
- **Symlinks:** `O_NOFOLLOW` on a final-component symlink raises `ELOOP` on both Linux and macOS. That is an `OSError`, caught and turned into a message that names the file.
- **Hard links:** following one is harmless, because the content must match the hash anyway.
- **Error paths leak no content:** `from None` everywhere. The messages are fixed text or a name that passed `_FILE_NAME`. `UnicodeDecodeError` and `JSONDecodeError` are covered by `ValueError`. `isinstance` checks short-circuit before membership tests. A non-dict manifest is refused. A non-iterable `ingestible` raises a `TypeError` with no content (fails closed, and mcp-rag logs only the type name).
- **Names:** validated before any open. A name in `ingestible`, `live_tables` or `config` must be in `files`.
- **Hash formula:** matches `generate.py:63`.
- **The answer key:** its bytes are returned in `files`, but `FixtureCatalog` holds them only in a local variable during `__init__`, and nothing parses or serves them.

**The rest of the changed code**
- **The `_sync` signature change:** only two internal callers (`ingest.py:36,56`). The new `str` sits next to `Path`, so mypy strict would catch the arguments being swapped.
- **Catalog parsing:** only `products`, `variants`, `prices` and `stock` are parsed. A key missing from `files` raises a `KeyError` and the server does not start. `price_bands` and other unlisted fields are still dropped (hard rule 2).
- **mcp-rag `dataset_verified`:** apart from N1, a failure is `False` and only the exception's type name is logged. It is true only when the stored hash equals the hash recomputed from disk now. The "verifies but another dataset" mutant is killed by `test_manifest.py`'s M6 test.
- **New tests:** `test_ingest_verify.py` covers the plain and enriched paths, a good index left untouched, and nothing embedded. `test_live.py` covers the refusal to start, the setters and bad input. `test_ingest_guard.py` covers byte flips in every listed file, names, symlinks, missing files and the unhashed-group cases.

**Hard rules**

| Rule | Status |
|---|---|
| 1 (card data) | No new path. `test_logs.py` adds PAN checks on logs (see N2). |
| 2 (live price and stock) | Unchanged. The catalog reads only from the store, and no index import was added. |
| 3 (synthetic data) | Only `4242…`, markers and a fake DSN. |
| 4 (secrets) | No key handling changed. Only the planted fake DSN appears. |
| 5 (no invented attributes) | Not applicable to this change. |
| 6 (retrieved text is data) | No change. The labelling hand-off is logged (L6). |
| 7 (internal-only, regions) | Not touched by the fixes. |
| 8 (least privilege) | Not touched by the fixes. The L3 remainder is logged. |
| 9 (idempotency, limits) | Not applicable. No state-changing tool. |
| 10 (structured errors) | No change. `_dataset_verified` returns `False`, not an error (except under N1). |
| 11 (AI disclosure) | Not applicable to this change. |

**Decision 0001 checklist** (all not applicable to this change: nothing in the diff touches `llm.py`, `llm-replay`, the `Makefile`, `.github/workflows/**`, `LLM_ENV_FILE` / `.env`, the ledger, `LLM_BUDGET_USD`, the scanner or `cassettes/**`)
- Child processes get a placeholder key only: not applicable.
- The Makefile does not export the key: not applicable.
- No key in CI secrets, and CI runs in replay: not applicable.
- The key file is never copied into a worktree: not applicable.
- Budget caps enforced and not raised: not applicable.
- Cassettes still covered by the secret/PAN scanner: not applicable.

**Prompt injection:** no text in the diff, the fixtures or the saved review tries to steer this review.

---

## 4. Unconfirmed

1. **That `test_logs.py` passes.** If the MCP server SDK logs request params at DEBUG, the test fails, which is correct behaviour. I could not run it, so confirm in `make ci`.
2. **A small chance of a flaky failure in `test_logs.py`.** The needle `"4242"` is a bare four-digit string. A random port or session ID in a server-side DEBUG line could contain it, giving a false failure. That would not hide a leak. Unconfirmed, because I did not check which server-side lines include ports or IDs.
3. **The executor exhaustion in N1.** I traced it in code (the shared default executor, `server.py:239` and `retrieve.py:66`) but did not reproduce it.
