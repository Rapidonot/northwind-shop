# Row #18 (RECORD-ALL): plan, revision 1 (2026-11-26)

The approved plan is `/Users/rapido/.claude/plans/ethereal-wishing-sifakis-of-d4a7f7a2-d97f-448c-9d2a-6ee6ad5b9c7b.md`;
the runbook it builds on is `docs/reviews/2026-11-26-18-runbook-draft.md`, revised the same day. This revision adds
the user's settled points and says where step 0, as built, differs from the plan. It goes to `advisor-reviewer`
before the user (CLAUDE.md, "Running a row"; step 0's advisor review F7).

## The user's settled points, now part of the plan
1. **Holds.** Wire the port to BE-04's Postgres when `DATABASE_URL` is set; the default stays empty in tests and
   replay.
   - **Blocked by stop 5:** `services/mcp-catalog/tests/test_tools.py::test_the_catalog_source_never_touches_the_index`
     forbids `psycopg` in mcp-catalog's source. The options are below.
   - Every window starts with the hold guard (`python -m northwind_backend.inventory.live_holds`). Exit 0 lets
     the window open. Exit 1 or 2 stops it. Exit 3 means the tables are absent.
   - Its output line goes in the window record (runbook §2 and §4, item 4a).
2. **The dependency.** mcp-catalog → northwind-backend for the substitutes module only, with an
   import-closure test: none of the modules the user named. The pyproject and `uv.lock` change only by the
   workspace entry. How "only the substitutes module" is read is the session's choice, put to the user
   below.
3. **The stale cassette.** It is not in `make ci` (`e2e` only), but GitHub's browser job runs it, so nothing is
   pushed before W7. `P0008-CHR-REL-M` still gets 3 alternatives in US, so W7's recorded expectation can hold.
4. **The spend stop** is $25 of real spend for the row.

## The freeze list, added
`services/mcp-catalog/src/northwind_mcp_catalog/{holds,server,live}.py`, beside `cart.py`, `tools.py` and
`substitutes.py` (runbook §1). Their sha256 values are taken at the freeze commit.

## Step 0 as built, where it differs from the approved plan (put to the user)

### (b) and (c) use region totals, not per-node stock (advisor F2; security review L1)
The plan said `reservable` reads "the catalog's per-node stock minus live holds", and that stock is "Σ over the
region's nodes of max(0, on_hand − live holds)". The catalog store keeps region totals: it sums the fixture
rows per region. So what was built is the region total less the region's live holds.

Effect: an alternative whose units are split across a region's nodes (US has two) can be offered in the chat and
still be short at checkout. Checkout then escalates `out_of_stock`, which is safe, but the shopper learns it late.

- **Option 1 (recommended):** keep region totals for #18, and move per-node stock to row #21 (CHK-05). It
  changes no model input in the recorded suites, and it keeps step 0 small before the freeze.
- **Option 2:** build per-node stock in the catalog store now, before the freeze: a per-node `LiveCatalog` read,
  with `set_stock` keeping its region semantics for the tests. It changes no tool description. Recorded values
  change only where a region has more than one node and the split matters. Cost: about half a day, plus a
  re-review.

### (a), the router test (advisor F1 and R1)
The router is all regexes, so there is no operation count to take. Its test went through three versions:
- **`3666dd8`:** one CPU reading per size, ratio at most 6, 30 s CPU backstop. 6x and 30 s were the security
  review's L3 fix, and the re-review closed L3 on them.
- **`7c27ebf`:** built "as the plan says": the median of 3, ratio under 8, 60 s. That loosened L3's fix (advisor
  R1), and the user was not told.
- **Now:** the median of 3 **and** L3's 6x and 30 s, stricter than both earlier versions. It passes in 3 quiet
  runs and in 3 runs under 10 busy-loop processes.

Limit: the test catches a quadratic pass only when the pass is large next to the router's linear cost.
- At 6x, a stride-1 `str.find` mutant fails 5 of 6 units (at 8x it failed 3).
- A stride-8 one passes all 6.

The output check's two tests do count operations (match-view reads and boundary scans). They add a CPU-time
ratio (8x) and a 60 s limit on **one** reading per size (advisor R8). The counts are the main bound there.

**For the user:** keep the median of 3 with 6x and 30 s (the current code, and the recommendation), or the
plan's 8x and 60 s (looser than L3). A closer bound would need the router rewritten to count its regex work,
which is out of scope. `security-reviewer` reads the current version (scoped).

### The hold reader (stop 5)
- **B (recommended):** allow `psycopg` in exactly `holds.py`, with a new test that its SQL names only
  `inventory_*` tables.
- **A:** put the reader in BE-04's package and import it from `app.py`. That widens the user's condition 2.
- **C:** keep the port, with no runtime wiring.

With A or B, the guard's exit 3 then blocks a window (Open items, "From #18 step 0, re-review N2").

### How "only the pure substitutes module" is read (advisor F5 and R2)
Importing `northwind_backend.inventory.substitutes` runs the package's `__init__`, which loads 13 backend
modules, all pure today: `InventoryFake`, `fakes._base`, `fakes.catalog`, `recovery`, `idempotency` and
others. The test asserts that the loaded `northwind_backend.*` modules are a **subset of** those 13 and that
none of the forbidden modules loads. This reading is the session's choice, not the user's.
- **Option 1 (recommended):** accept the 13-module allow-list.
- **Option 2:** read the condition literally. Load `substitutes` without running the package `__init__`,
  either by moving the module or by importing it by file path, so only it and its own imports load.

### Exit 3 today
On `northwind` the guard exits 3: "BE-04's tables do not exist in database 'northwind'". While no reader is
wired, that reads as 0 live holds, if the user agrees.

## Next (unchanged)
Commit the advisor's fixes, then the user's go, then `make ci`, then the freeze commit, then W1. The user is
told before each window and before each `make ci`.

## The user's answers (2026-11-27)
- Holds reader: **B** (built in `b188b05`).
- Per-node stock: **option 1 (a)**. No recorded scenario can offer a split alternative, because every recorded cart
  request is quantity 1. Per-node stock for refusals of 2 or more units is an open item owned by #21.
- Router limits: the median of 3, with 6x and 30 s.
- The 13-module allow-list: option 1.
- Exit 3 **always blocks** a window. BE-04's schema is to be applied to `northwind` from the user's terminal, so
  the guard can return a real 0 before W1.

## Correction (advisor read 3, 2026-11-27)
The answer above called per-node stock a case "no recorded scenario can offer", because "every recorded cart
request is quantity 1". Both statements were wrong as written:
- **The split is real.** The cart journey's one recorded refusal, `P0008-CHR-REL-M` in US at quantity 1, offers
  `P0001-BLK-REL-M`, `P0003-SLT-REL-M` and `P0004-BLK-REL-M`. All three have their US units at both DC-EAST and
  DC-WEST (4+47, 54+72 and 30+60). So the user's condition, as worded, is met.
- **It changes nothing at quantity 1.** Each alternative has a single node holding at least 47 units, so the region
  total and the single-node maximum choose the same alternatives. Per-node stock would change only refusals of 2 or
  more units, and RECORD-ALL records none.
- **The journey also sets quantity 2.** That step raises an in-stock line whose stock the test sets to 5, so it is
  never refused.

The session read "split" as "no single node can fill the request". That reading is the session's, not the user's,
so the choice goes back to the user: confirm (a), or build per-node stock now. The commands and output are in the
log, under "advisor read 3, finding 1".
