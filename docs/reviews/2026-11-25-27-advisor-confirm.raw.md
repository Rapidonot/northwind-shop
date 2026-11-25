# Row #27 advisor-reviewer, confirming read at 68d0929 (raw, verbatim; trailing spaces stripped)

findings

Confirming read of row #27 at 68d0929. The diff's first two lines match your SHAs: base `1ac39622dfe65404abaae22a2d12e8c15583d00c`, head `68d09295c38b83a22a1ac33438c001162f7e2dec`. **R-1 to R-4 are closed.** One new low, a PR-body wording point, and L-1 (`CLAUDE.md`) still waits for the user. Nothing medium or above. I ran nothing: results are as logged.

## R-1 to R-4

| Finding | State | What I read |
|---|---|---|
| R-1 | Closed | The Owners Why says the form was found on 2026-11-25, after the option B decision, and that its owner awaits the user. 0015 and the PR body say "proposed owner … awaiting the user". The Tampering row still says "Owned by the rest of NFR-03 (Window B), see Owners", and Owners carries the caveat. |
| R-2 | Closed | The other shopper is now the lowest-numbered one with orders in S0188's region, and the test asserts the regions are equal. On the data that is S0002 (US). The log shows this test failing under the owner mutant, where the earlier run did not. The region check no longer masks it. The change tightens the test. |
| R-3 | Closed | The first mutant is renamed to what it does. The second now removes the demo check and uses `.get(shopper, 'US')`, and its old string matches `settings.py:41-43`, so S0103 is accepted as signed in and the failure is the assertion, by reading. |
| R-4 | Closed | The PR body now says two lows were fixed and L3 is Open. The script prints every failing test, and the second log run names them. The raw report's heading says trailing spaces were stripped. |

The second mutant run is consistent: 176 before and after, 29 mutants, 0 not killed. Only the owner mutant's count changed (2 to 3), as expected. The first run is kept in the log, not overwritten.

## New finding

### C-1 (checklist 4, low): PR body, lines 105-113
- "Its three checks on the resolved merge hold" and the "L-2, L-3, L-4 … L-5 … L-1" bullet now sit under the re-read at `1a4f8d5`. Both belong to the row review at `0c0db58`.
- "All fixed" covers R-1, but R-1's owner still awaits the user. "All closed; R-1's owner awaits the user" would be exact.
- My `1a4f8d5` report and this one are not saved in `docs/reviews/`; only the first is.
- This alone does not need a new head. If you edit it, the head moves, and `make ci` runs on the new SHA.

## Checked
1. **Scope: checked.** The same file set as at 1a4f8d5. By blob ID, the files that differ from it are the eight you named.
2. **Failing first: checked.** The changed test is logged failing under the mutant, after the fact, and the log says so.
3. **No weakened test: checked.** One test changed, and it is stronger.
4. **Hard rules and protected paths: checked,** C-1. No `src`, cassette or baseline blob changed since 1a4f8d5.
5. **Spend and window: checked.** No new spend is claimed. I did not read the ledger.
6. **Requested changes: checked.** The window-guard line is now under Addendum C item 5.
7. **Control surface: listed below.** Unchanged since 1a4f8d5.

Unconfirmed, as before: that the mutants ran as logged; the user's quoted words; that `prompt-02210891` is this `prompt.py`'s hash; `make ci`, not yet run. `security-reviewer` has read nothing after `57635e2`.

## Control surface
- `Makefile`
- `pyproject.toml` (root)
- `uv.lock`
- `services/mcp-orders/pyproject.toml` (new)
- `infra/docker-compose.yml`
- `evals/baselines/dense-only.json` (`baseline-change` label needed, from the user)
- `services/common/src/northwind_common/mcp_token.py`
- `services/common/src/northwind_common/order_tool.py` (new)
- `services/common/src/northwind_common/tool_scopes.py`
- `services/common/tests/test_chk06_shopper_claim.py` (new)
- `services/common/tests/test_mcp_token.py`
- `services/common/tests/test_tool_scopes.py`
- `cassettes/assistant-orders/`, 11 added: `334613e0…`, `489ccd9a…`, `534b0e00…`, `5c8254d2…`, `6354fbeb…`, `a1969c24…`, `b040ca89…`, `c39cf32f…`, `d6e2cc97…`, `e717ce86…`, `ff4efcb7…`

**For the user, in the gate report:** the M-1 gap and its owner; the promote condition (L-2); `CLAUDE.md`'s `make dev` line (L-1); that the feature's first security tests have after-the-fact evidence (M-2); that the advisor did not re-read the plan after its first fixes and no review of Addenda A and B is recorded (L-4); and that `security-reviewer` has not read what came after `57635e2`.
