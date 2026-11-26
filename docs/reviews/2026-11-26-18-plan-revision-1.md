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
2. **The dependency.** mcp-catalog → northwind-backend for the substitutes module only.
   - A test pins the import closure: none of the modules the user named, no `psycopg`, no `claude_agent_sdk`.
   - Backend modules may come only from today's 13 pure ones, which load through
     `northwind_backend.inventory.__init__`.
   - The pyproject and `uv.lock` change only by the workspace entry.
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

### (a), the router test (advisor F1)
The router is all regexes, so there is no operation count to take. It is built as the plan says: the median of
3 CPU-time readings at n and 4n, ratio under 8, and a 60 s CPU backstop. All three tests pass under 10
busy-loop processes.

Limit: the test catches a quadratic pass only when the pass is large next to the router's linear cost.
- A stride-1 `str.find` mutant fails 3 of 6 units.
- A stride-8 one passes all 6.

The output check's two tests do count operations (match-view reads and boundary scans), plus a CPU-time ratio
and a 60 s limit.

**Recommendation: accept the limit.** The user asked for counts "or a far larger margin". A closer bound on the
router would need the router rewritten to count its regex work, which is out of scope.

### The hold reader (stop 5)
- **B (recommended):** allow `psycopg` in exactly `holds.py`, with a new test that its SQL names only
  `inventory_*` tables.
- **A:** put the reader in BE-04's package and import it from `app.py`. That widens the user's condition 2.
- **C:** keep the port, with no runtime wiring.

With A or B, the guard's exit 3 then blocks a window (Open items, "From #18 step 0, re-review N2").

### Exit 3 today
On `northwind` the guard exits 3: "BE-04's tables do not exist in database 'northwind'". While no reader is
wired, that reads as 0 live holds, if the user agrees.

## Next (unchanged)
Commit the advisor's fixes, then the user's go, then `make ci`, then the freeze commit, then W1. The user is
told before each window and before each `make ci`.
