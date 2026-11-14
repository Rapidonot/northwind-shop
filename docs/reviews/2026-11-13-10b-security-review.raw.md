# Security review: row #10b (CC-10 partial: mcp-catalog)

**Input:** `/tmp/claude-502/northwind-security-10b.diff` (37 files, base `6221106`, uncommitted working tree; `uv.lock` excluded). I read the full diff, and the plan at `/Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-13-10b-plan.md` (§1, §3 with D1–D4 and D2 as amended, §6). Before the review I re-read `CLAUDE.md` "Hard rules" and `docs/decisions/0001-llm-first-order-and-record-replay.md`. I also read these working-tree files in full: `mcp_token.py`, `tool_scopes.py`, `services/mcp-rag/src/northwind_mcp_rag/server.py`, `services/rag/src/northwind_rag/ingest.py`, `services/assistant/src/northwind_assistant/rag_client.py`, `data/tests/test_fixtures_current.py`, and the fixture rows for P0001 and P0009. I did not run any tests: I have no Bash tool.

**Off-limits paths:** the diff does not touch `services/assistant/**`, `cassettes/**` or `evals/fixtures/a2a-05-partner/**`. It also leaves `Makefile`, `.github/**`, `.claude/**`, `CLAUDE.md`, `.gitignore`, any `conftest.py` and `data/fixtures/**` alone. The control-surface files it does change are the root `pyproject.toml` and the new `services/mcp-catalog/pyproject.toml`, and the plan §6 names both.

**Result:** no critical, high or medium findings. There are six low findings. Most are gaps in evidence or documentation, plus one design limit that should be logged.

---

## 1. Findings, most severe first

### L1 (low): the docs present `verify_dataset` as a tamper control, but it only checks that the files and manifest agree
- **Where:**
  - `data/src/northwind_data/ingest_guard.py:69-104`
  - `docs/governance/threat-model.md:87` (Tampering, "Poisoned content in the index": "`dataset_sha256` verified: **landed**")
  - `docs/governance/tool-register.md`, the index-manifest row (`dataset_verified`)
- **Rule:** hard rule 6 and NFR-03 (poisoned index content); the threat-model register.
- **What is wrong:** `manifest.json` is not authenticated. The hash formula is public (`generate.py:63`), and the diff's own test helper `rehash` (`services/rag/tests/test_index.py`) re-signs a manifest in eight lines. `verify_dataset` therefore catches drift and accidental corruption. It does not catch a deliberate edit.
- **How it fails:**
  1. Someone plants an instruction in `data/fixtures/reviews.jsonl` in a working tree.
  2. They recompute the per-file hash and `dataset_sha256` the way `rehash` does.
  3. `make ingest` passes `verify_dataset` and indexes the planted review.
  4. mcp-rag's manifest then reports `dataset_verified: true` for the poisoned dataset.

  The only anchor against a re-signed manifest is `data/tests/test_fixtures_current.py::test_committed_fixtures_match_the_generator`. It runs in CI against committed fixtures, never at ingest time. `data/fixtures/**` is not on the control surface.
- **Fix:**
  - Reword the threat-model row and the tool-register `dataset_verified` text: the check proves the files on disk match their manifest, and the anchor against a re-signed manifest is `test_committed_fixtures_match_the_generator` in CI.
  - Optionally add a real anchor: pin the expected `dataset_sha256` per `DATA_VERSION` in reviewed code (`data/src`, which is in `RUNNER_SYNC_PATHS`), and have `verify_dataset` compare against that pin as well.

### L2 (low): the code verifies one read of the files and then uses another
- **Where:**
  - `services/rag/src/northwind_rag/ingest.py:35` and `:54`: the hash `verify_dataset` returns is discarded. `_sync` re-reads `manifest.json` at `:62` for `IndexVersion.dataset_sha256`, and `build_chunks` re-opens every file.
  - `services/mcp-catalog/src/northwind_mcp_catalog/live.py:131`: `verify_dataset`, then `:134/155/172/178` re-open each file through `_jsonl` (`:115-116`, `path.open`). That call follows symlinks, while `verify_dataset` (`ingest_guard.py:97-99`) refuses them.
  - Inside `verify_dataset` itself, `is_symlink()` and `read_bytes()` are two separate calls.
- **Rule:** Q4 / decision D4. The docstring in `live.py:131` says "a tampered dataset never seeds the store".
- **How it fails:** `make data`, a `git switch` or an editor save in the same checkout lands while `make ingest` or mcp-catalog is starting.
  - Ingest can index a mix of old and new files and stamp the index with a hash that was verified against different bytes. mcp-rag then reports that hash as verified.
  - mcp-catalog can seed from a file that was swapped for a symlink after the check.

  This is lab-only and needs a race or local write access. Anyone with write access can already do L1.
- **Fix:**
  - Have `verify_dataset` return the verified bytes (name to bytes) along with the hash. `FixtureCatalog` and `build_chunks` should parse those bytes, and `_sync` should store the returned hash, not a re-read of the manifest.
  - Read each file once (`os.open` with `O_NOFOLLOW`, then hash the bytes from that descriptor).

### L3 (low): every server holds the one master key, so any of them can mint tokens for every other server
- **Where:** `services/mcp-catalog/src/northwind_mcp_catalog/app.py:22` (`signing_key()`), `services/common/src/northwind_common/mcp_token.py:91-131`
- **Rule:** hard rules 4 and 8, NFR-01 (residual risk after L4).
- **What is wrong:** the `srv` claim stops a token minted for one server being replayed on another, and that works. But HMAC is symmetric, and mcp-catalog only needs to *verify* tokens, yet it holds `NORTHWIND_MCP_TOKEN_KEY`. That key mints any `srv`, `agent` and `aud`.
- **How it fails:** a bug or compromise in the mcp-catalog process can mint a `catalog-subagent` (staff) token for `northwind-rag` and read the index manifest. When mcp-orders and mcp-policy share the key, the same compromise reaches their tools too.
- **Fix:** derive one verification key per server from the master, for example `HMAC(master, "srv:" + name)`. Give each server only its derived key, and keep the master in the gateway alone. This is a design change, so it should be logged with an owner (#12 or hardening) rather than done in this row.

### L4 (low): the test that guards against bare `verify(` calls is narrow
- **Where:** `services/common/tests/test_mcp_token.py:288-300`
- **Rule:** L4 / NFR-02.
- **What is wrong:**
  - It scans only `services/*/src` (root = `services`), so `data/src`, `evals/src` and the later `agents-external/` and `partner-agent/` roots are not covered.
  - It matches only a call node named `verify`, so `from northwind_common.mcp_token import verify as check`, then `check(token, …)`, passes the test.
- **How it fails:** a future server under `agents-external/`, or any aliased import, verifies with bare `verify`, and a `northwind-rag` token is then accepted there.
- **Fix:**
  - Also flag any `ImportFrom` of `verify` from `northwind_common.mcp_token`, aliased or not, and any `mcp_token.verify` attribute access, outside `mcp_token.py`.
  - Scan every source root, using the same root list as the other source pins.

### L5 (low): no test backs the catalog's log-hygiene claim
- **Where:** `docs/governance/data-flow.md:72` ("Logs categories and codes only: no argument, token, key or exception text"). The evidence column cites `server.py`, `live.py` and `tests/mcp/test_contract.py`, and none of them checks logs.
- **Rule:** hard rule 1 (PAN in logs), hard rule 4 (token in logs).
- **What is wrong:** I read the code and it is clean today. `server.py:122` and `:129` log a fixed text and an exception type name, and the gate logs `request refused: token`. But mcp-rag has `test_server.py::test_no_token_or_key_reaches_the_logs` and `test_cc10_http_surface.py::test_no_query_text_reaches_a_server_log_on_any_path`, and mcp-catalog has neither.
- **How it fails:** a later change logs `params.arguments`, or the SDK logs request params at DEBUG, on the catalog path. A PAN sent in a catalog argument, or a token, reaches a log and no test fails.
- **Fix:** add the same tests for the catalog: root logger at DEBUG, then calls with a PAN, a marker and a bad token on every tool, asserting none of those strings appear in any server-side record.

### L6 (low): the hard-rule-6 hand-off to #12 is not registered
- **Where:** `services/mcp-catalog/src/northwind_mcp_catalog/server.py:74-79`. The `json_result` docstring says labelling is "the assistant's, at #12".
- **Rule:** hard rule 6 (NFR-03).
- **What is wrong:** `get_product` returns free text (`description` and the attribute strings) as raw JSON text, and nothing labels it as data. The deferral is written only in a docstring and in plan §4. It is not in `docs/governance/threat-model.md`'s open items, which add only the `mint` default, the call cap and the in-memory store. It is not in PROGRESS.md row 12 either, and PROGRESS.md is not yet updated in this diff.
- **How it fails:** #12 attaches the catalog tools using its PROGRESS row as scope, and catalog text reaches the model unlabelled.
- **Fix:** add an open-item row ("catalog tool output not yet labelled as data for the model", owner #12) to the threat model. Add it, and the `mint`-default removal, to PROGRESS.md row 12 when PROGRESS.md is updated.

---

## 2. Checked, no findings

**Hard rules**
- **1 (card data):**
  - Every catalog handler calls `holds_card_data` over all keys and values, including split values, before checking keys, looking anything up or logging (`tools.py` `_checked`).
  - `holds_card_data` moved to `cards.py` byte-for-byte, and mcp-rag re-exports it.
  - Error text never echoes an argument.
  - Tests cover a PAN in a known key, in an unknown key, as a key, and split across values.
- **2 (price and stock from live tools only):**
  - `get_price` and `check_stock` read the in-memory store on every call.
  - `FixtureCatalog` loads only named fields, so `price_bands` is dropped and summaries and `get_product` carry no price.
  - No import of `northwind_rag` or `psycopg`, and no reference to `rag_chunks` (AST and text pin).
- **3 (synthetic data):** only the sandbox test PAN `4242…` and marker strings.
- **4 (secrets):**
  - The key comes only from `NORTHWIND_MCP_TOKEN_KEY` through `signing_key()`, whose error names the variable, not the value.
  - The gate logs no token, and nothing reads `.env`.
  - The `KEY` constants in `testing.py` are test-only, as in mcp-rag.
- **5 (no invented product attributes):** not applicable to this change. No model is involved, and the tools return catalog records only.
- **6 (retrieved and tool text is data):** no tool access depends on content, and the scope comes only from `tool_scopes`. The labelling deferral is L6.
- **7 (internal-only records and regions):**
  - **Visibility:**
    - `_sees` passes only `"public"` for a shopper, so unknown visibility values fail closed.
    - A variant is visible only if its product is visible too.
    - `get_product` filters `variant_ids`.
    - Search filters on visibility before matching, so `returned` and `more` count only visible products.
  - **Existence oracle:** none found.
    - An internal ID gets the same `not_found` envelope as an unknown well-formed ID, per tool.
    - `invalid_*_id` depends only on the ID's format.
    - Visibility is decided before `region_required` and `not_sold_in_region`.
    - The timing difference is one or two dict lookups, which does not matter over HTTP plus an MCP session. Search short-circuits on `_sees`, so how long a search takes does not depend on whether the query matches an internal product.
    - P0009's canary, ID and title are absent from every response byte in all 4 regions (`test_catalog_canary.py`, `test_visibility.py`).
    - In the fixtures, only P0009 and its 4 variants are internal, and the guide IDs (`CG-*`, `SG-*`) are all public documents.
  - **Regions:** price and stock use the token's region only, and a `region` argument is refused as `unknown_argument`.
- **8 (least privilege):**
  - The shopper-assistant's catalog scope is exactly the 5 read tools, matching PRD §7.
  - `catalog-subagent` gets an empty catalog scope.
  - No tool name contains refund, capture or assign (contract test).
  - Scoping does not attach anything: the assistant still uses `turn.py:102` `allowed_tools=[ALLOWED_TOOL]`, with no catalog URL or token.
  - `set_price` and `set_stock` cannot be reached. `call_tool` dispatches only through the `self._tools` dict, intersected with the agent's scope. There is no `getattr` dispatch, and there are no resources or prompts. A setter name gets the same `not_allowed` envelope as an unknown tool, and `calls` stays 0.
- **9 (idempotency and limits):** no state-changing tool is added, and the setters are not tools.
- **10 (structured errors):**
  - Every path returns the envelope with fixed text.
  - An unexpected exception becomes `tool_failed`, and only the exception's type name is logged (the planted-secret test covers this).
  - An empty search returns `isError: false` with `[]`.
- **11 (AI disclosure):** not applicable to this change.

**L4: token cross-use between servers**
- `srv` is in the signed body and required (`CLAIMS` equality), and it must be one of `SERVERS`.
- `verify_for` checks the signature before reading `srv`, and refuses with the same bare `TokenError`.
- Both directions get a bare 401 (contract and gate tests). An old token without `srv` is refused.

**The `mint` default (`server="northwind-rag"`)**
- It cannot widen access. A forgotten `server` produces a rag token, which mcp-catalog refuses.
- On rag, the agent's scope is unchanged.
- The one production minter (`rag_client.py:47`) wants rag anyway.

**The gate move**
- Header lookup matches Starlette's `Headers.get`: first exact `b"authorization"` name, decoded as latin-1, else `""`. Parity is tested, including duplicate and mixed-case headers.
- Lifespan passes through, a websocket gets close 1008, other scope types are dropped, a failure is a bare 401 with `www-authenticate: Bearer`, and the claims go in a copied `state`.
- The differences all fail closed:
  - a scope with no `headers` now gets a 401 (Starlette raised);
  - a `str` header name would raise `TypeError` and give a 500 rather than a 401 (uvicorn always sends bytes);
  - the logger name changed from `northwind_mcp_rag` to `northwind_mcp_gate`. mcp-rag's `test_server.py:343` captures at root level, so its log test still sees the refusal.
- mcp-rag keeps `token_gate(app, key, clock)` with the same signature.

**`verify_dataset` bypasses**
- File names must match `[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}` and must not contain `..`, so there are no path separators, no absolute paths and no `..`.
- A symlink in the final component is refused.
- Every ingestible, live and config name must also be listed under `files`.
- The answer key is hashed but never parsed.
- The formula matches `generate.py:47,63`.
- Errors name only a file that passed the regex, or a manifest field.
- The remaining limits are L1 and L2.

**Dependencies of `northwind_common`**
- At runtime there are none new: `mcp_gate.py` and `cards.py` use the standard library only, and `services/common/pyproject.toml` is unchanged.
- At test time, `services/common/tests/test_mcp_gate.py` imports `starlette` and `northwind_mcp_catalog`. That is a test-only cross-package import. The runner syncs `--no-dev --package northwind-llm-replay` (`Makefile:183`), so its environment is unaffected.

**Decision 0001 checklist**
- Child processes get only a placeholder key: not applicable to this change (`llm.py` and `llm-replay` are untouched, and mcp-catalog makes no LLM calls and has no `anthropic` import).
- The Makefile does not export the key: not applicable (the Makefile is not in the diff).
- No key in CI secrets, and CI runs in replay: not applicable (`.github/workflows/**` is untouched).
- The key file is never copied into a worktree: not applicable (nothing touches `LLM_ENV_FILE` or `.env`).
- Budget caps enforced and not raised: not applicable (nothing touches the proxy, the ledger or `LLM_BUDGET_USD`; the plan's spend is $0).
- Cassettes still covered by the secret/PAN scanner: not applicable (no change to the scanner or cassettes, and no new on-disk artifact needs scanning).

**Prompt injection:** I found no text in the diff, the plan or the fixtures that tries to steer this review.

---

## 3. Unconfirmed

1. **SDK input validation.** I assume the MCP low-level `Server` does not validate tool arguments against `inputSchema` before the handler runs. If it did, its error would echo argument values (including a PAN) outside the envelope. `test_tools.py`'s `card_data_refused` and `invalid_argument` cases would fail in that case, so they cover it, but I could not run them. Confirm in `make ci`.
2. **SDK DEBUG logging on the catalog path.** mcp-rag's HTTP-surface log test covers the same SDK, so this is probably clean. There is no catalog-specific evidence (see L5).
3. **Running `services/common/tests`.** I did not check whether any runner or ship target runs `services/common/tests` in an environment without mcp-catalog installed. If one does, `test_mcp_gate.py` would fail to import. That is a correctness issue, not a security one.
