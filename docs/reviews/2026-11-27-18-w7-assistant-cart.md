# Row #18, W7: `assistant-cart` (2026-11-27)

- **Window:** opened by the user at `74dd39a` with `RUN_BUDGET=0.25`. The freeze is at `2ca787e`, and the runner
  was at `origin/main` (`bcc135b`).
- **Hold guard before the window** (10:29:24Z): `live holds: 0 in database 'northwind'`, exit 0. The recording
  builds its catalog in process, so no `DATABASE_URL` catalog took part.
- **Why this window:** the $0 replay at the frozen head had 1 miss
  (`test_cart_journey_e2e.py::test_add_refused_add_change_quantity_remove`). The out-of-stock refusal's alternatives
  changed with step 0 (b), as planned (`2026-11-27-18-zero-cost-replays.md`).
- **Recording:** `make record SUITE=assistant-cart` with `FASTEMBED_CACHE_PATH` set, 10:33:06–10:33:16Z, exit 0,
  1 passed. The fresh answer met the test's planned expectation: it named only the alternatives the server
  offered.
- **Spend:** `make llm-spend` showed $32.704032 before and $32.722916 after, so **$0.018884**. The row's real spend
  so far is $0.018884.
- **Proxy hit log:** the mark before the window was 42 lines. After it there were 10 lines: 1 `recorded` and 9
  `hit`, with no refusal.
- **Cassettes:** 10 before.
  - Added 1: `f61e2643c9b8…`, the refusal turn on the new alternatives.
  - Removed 1: `ed523317d600…`, the old refusal turn. No line after the mark reads it.
  - The other 9 were all read and are kept. 10 after.
- **Replay right after** (model cache set): 1 passed, no miss.
- **Scan:** `make scan-cassettes` clean (0 findings). The key-fragment check from the runner clone is the user's.
- **Freeze diff** (`git diff 2ca787e..HEAD` on the frozen paths): empty.
- **First-byte p50 and p95:** not measured. The recording log does not print them for one recorded call.
