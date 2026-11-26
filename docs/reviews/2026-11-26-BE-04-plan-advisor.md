# BE-04 plan revision 2: advisor-reviewer (plan review), verbatim

Verdict: **findings** (4 medium, 4 low; none high or critical). Applied in the plan file, marked "advisor F1" to "advisor F8".

1. Item 6 / stop report, medium, plan line 115 (option A). The plan says A costs "a data-version bump and the regenerated manifest". It costs much more:
   - Editing `stock.jsonl` changes the fixtures' `dataset_sha256`.
   - `data/backend/manifest.json` stores `source_dataset_sha256`, and `services/backend/tests/test_found3_generator.py:287` checks it against the fixtures. So the backend data has to be regenerated too.
   - That means a `BACKEND_DATA_VERSION` bump, and `services/backend/tests/test_be05_labels.py:56` checks `== 2`.
   - Every plain and enriched RAG index stores `dataset_sha256` (`services/rag/src/northwind_rag/ingest.py:68-70`). Until they are re-ingested, mcp-rag's manifest reports `dataset_verified: false` (`services/mcp-rag/src/northwind_mcp_rag/server.py:319`) and `make eval-enrichment-report` fails `_index_problems` (`evals/src/northwind_evals/cli.py:940-951`).
   - Re-ingesting means `make ingest` and `make ingest-enriched` on a running stack (`make up` is the user's terminal), in every checkout and the runner. That may hit the Postgres that window B is using for `make ci`.
   - I found no cassette containing either variant ID or a dataset hash, so the claim that "no cassette goes stale" under A holds.

2. Item 1 / stop report, medium, plan lines 57-58 and 105. The count of 2 may be too low, and the plan does not say how it was produced:
   - It comes from a read-only script that sums demand per (variant, region). It is not the pin, which hasn't been built or run.
   - Item 5 says "Otherwise split per line". If one line cannot be spread across nodes, a US line whose quantity is more than either DC-EAST or DC-WEST holds, but not more than both together, is refused with no other order involved. A per-region sum cannot see that.
   - The count covers `orders.jsonl` through orders_place only. It does not cover the backend-audit or backend-orchestrator drivers, which the pin also replays.
   - The report should say "estimated, pin not yet run". It should either state that a line can split across nodes or recount per node.

3. Item 1, medium, plan line 30 against line 52. The availability formula `available = on_hand - live holds - committed` double-counts commits, because `commit` already lowers `on_hand` by the quantity. Built that way, it would refuse more orders and change the pin's count.

4. Item 6 (change 2), medium, plan lines 46 and 61. Expired holds are swept only on the stock rows that `reserve` locks. Any other availability read, such as substitutes' `available >= qty` or node choice, would still count expired holds against stock unless it computes live holds as `status='held' AND expires_at > now`. The user's change 2 says expired holds don't count against available stock. The plan needs to say how every read honours that.

5. Item 6 (change 3), low, plan lines 26-30. The stock-source table leaves out two readers: `nodes.py` (per-node stock) and `substitutes.py` (`available`). It also doesn't say where substitutes' "live price" comes from (hard rule 2).

6. Item 1, low, plan lines 52 and 69. The user asked for `reserve()` and `release()`. The plan adds `commit()` at capture, plus a re-reserve when a hold has expired. Without commit nothing stops a later oversell, so the addition is justified. But it should be named to the user as an addition. `release()` on cancel is deferred because no cancel tool exists. That is logged with owner #19 (line 85) and is acceptable as stated.

7. Stop report, low. Claim B, that the model never sees quantities or totals, is accurate for model input:
   - The `orders_place.py:35-38` docstring and `validate_order` (L494) return only the issues and the line count.
   - The fraud score is a hash of the IDs (`fakes/fraud.py:1`).
   - No cassette contains either variant ID.

   It holds only under conditions B doesn't state:
   - The replacement variant for O000936 is in US stock, priced in USD and under the payment fake's order limit.
   - The line count stays the same, and no new collision is created.
   - The generator change leaves every other order byte-identical. That needs a pin.
   - `tracking.jsonl` and `replay_labels.jsonl` also contain these orders.

8. Stop report, low. CLAUDE.md says to finish all work that does not depend on the decision before stopping. The report should list what can go ahead now (items 1-6 and 8-11, the race and contract tests, M12) and what waits on A-D (only item 7, the wiring, and the pin). It should also note that running the `@integration` race tests against the shared Postgres could disturb window B's `make ci`.

**Confirmed accurate**
- The stop is correctly raised. All four orders are `exception: null` (`data/backend/orders.jsonl` lines 88, 369, 476 and 936), and all appear in backend-replay cassettes, so a refusal would cause recorded misses. The stock figures match `stock.jsonl` (EU 4; US DC-EAST 0 and DC-WEST 3).
- Option C, "no result changes": true in practice, because each pair is far apart in file order at `CONCURRENCY = 3` (`_replay.py:24`). It is a scheduling assumption, though, not a guarantee.
- Option D is accurately described.
- Report 4(a), CHK-01, matches `cart.py:221-260`. Exactly one cassette has an `"alternatives"` block (`cassettes/assistant-cart/ed523317d6005a20df286edba324ec1d87220734ebe5f8130fbd85101d5c05d9.json`).
- Report 4(b), `uv.lock`: `psycopg` and `psycopg-binary` 3.3.7 are already locked (L1107 and L1125) and used by assistant, gateway, mcp-rag and rag. `services/backend/pyproject.toml` has only `northwind-common` today.
- The plan widens scope nowhere else. `check_stock` and mcp-catalog are unchanged, and no prompt, description or schema changes.

**Checked**
1. Scope: within BE-04 and M12, and out of mcp-catalog. The only extra is commit (finding 6). Not routine, correctly (`services/backend/**`).
2. Failing-first: does not apply yet (no diff). The plan does include a read-then-write variant that must oversell.
3. Tests weakened: does not apply yet. Note that A or B would mean updating the version pin in `test_be05_labels.py:56`.
4. Hard rules and protected paths: does not apply yet. The plan keeps hard rules 2, 9 and 10 in view.
5. Spend and window: stated as $0, with no recording and no re-record.
6. Requested changes: all four are present. Gaps are in findings 1, 4 and 5.
7. Control surface: the plan touches none. It stops (stop 8) if a `conftest.py` is needed, which is likely for the Postgres race fixture.

**Control surface**
No control-surface files are named in the plan. Possible: a new `services/backend/tests/**/conftest.py` for the integration race fixture, which the plan already says is a stop 8. Options A and B touch `data/fixtures/**` or `data/backend/**`, which are not on the control surface. `data/enrichment/public.jsonl` contains both variants, but A leaves it unchanged.

**Caller's follow-up to F2:** recounted per node, in file order, with commits lowering the stock and each line from a single node: still 2 (O000476, O000936). No single line needs more than one node holds.
