# Row #27 advisor-reviewer, scoped re-read at 1a4f8d5 (raw, verbatim; trailing spaces stripped)

findings

Scoped re-read of row #27, 0c0db58 to 1a4f8d5. The diff's first two lines match your SHAs: base `1ac39622dfe65404abaae22a2d12e8c15583d00c`, head `1a4f8d521fe164536ab7279782c0fb968be97e40`. Four low findings, nothing medium or above. **M-1, M-2 and L-2 to L-5 are closed.** L-1 (`CLAUDE.md`) is open, for the user. I ran nothing: mutant results are as logged.

## Earlier findings

| Finding | State | What I read |
|---|---|---|
| M-1 | Closed as a record. The gap stays open and is stated. | 0015's note, 0023 §6, the threat-model row and Owners line, the PR body and the `order_tool.py` docstring all now say that a claim with the order number between noun and verb is held by the prompt only. The forms 0015 lists do miss the pattern (`output_check.py:150, 350-360`). See R-1. The user still has to be told in the gate report. |
| M-2 | Closed, as after-the-fact evidence | The log and PR body both say it was made afterwards. See "Mutants". |
| L-2 | Closed | Addendum C item 8 says which parts of the condition were not met literally. The line offsets agree (9 more lines than the candidate, all in `overrides`). I did not compare every byte. The user should confirm it. |
| L-3 | Closed | Addendum C items 7, 9 and 10. The window-guard line sits under item 7 ("B, for PR 2") but belongs under item 5. |
| L-4 | Closed as a record | Addendum C item 12 and the PR body. |
| L-5 | Closed | The `order_tool.py` docstring is accurate. |
| L-1 | Open | In the PR body as the user's decision. |

The new `qf` + `shp` pin is sound: it first shows that each claim alone verifies on its own server, then that the pair is refused for every `srv`. The "qf is taken on any server" mutant is caught by that test alone.

## New findings

### R-1 (checklist 4, low): the wider gap is credited to a user decision made before it was found
- **Where:** `docs/governance/threat-model.md:268`; "Owner: Window B P6, NFR-03" in 0015's row #27 note; PR body line 119.
- **What is wrong:** the Owners line now covers the number-between-noun-and-verb form, found on 2026-11-25. Its Why still reads only "The user's N1-R option (B), 2026-11-24". I saw no user decision on this form or its owner, and the user chose option B without knowing about it.
- **What to do:** add to the Why that the advisor found this form on 2026-11-25 and that its owner awaits the user, or use the document's `needs the user`. Ask in the gate report.

### R-2 (checklist 2, low): the two-shopper test does not test ownership
- **Where:** `services/mcp-orders/tests/test_chk06_tw_edges.py`, `test_two_signed_in_sessions_at_once_never_see_each_others_orders`.
- **What is wrong:** it picks the first shopper that is not S0188. That is S0001, whose region is CA (`data/backend/orders.jsonl:11`), while S0188 is US. With the owner check gone the region check still refuses, and the reverse. The log agrees: this test is not among the failures of either the owner mutant (2 failed) or the region mutant (1 failed).
- **Not a hole in the control:** ownership is proven by the same-bytes test, whose O000001 is S0103's, in the US, and which did fail.
- **What to do:** pick another US shopper (S0002 is one) and log it failing under the owner mutant.

### R-3 (checklist 2, low): two mutants are caught for a different reason than their names say
- **Where:** `docs/reviews/2026-11-25-27-mutants.py.txt:37` and `:50`.
- **"a token with no shopper is served":** nothing is served. With the check removed, `view(shopper=None)` still gives `not_found`, so the test fails on the error code.
- **"any signed shopper, not only the demo one":** the mutant then runs `DEMO_SHOPPERS["S0103"]`, which raises `KeyError`. The failure is probably a crash, not an accepted shopper.
- **What to do:** rename the first. For the second, also mutate the region lookup (`.get(shopper, "US")`) so the assertion itself fails. The tests assert the right things, and no test needs changing.

### R-4 (checklist 4, low): small record inaccuracies
- The PR body says the last security pass's "three lows were fixed". L3 was recorded as Open with an owner, not fixed.
- The PR body says "the log names the tests", but the log names at most three per mutant; three lines end in "...".
- `2026-11-25-27-advisor-review.raw.md` is headed "verbatim", while trailing spaces were stripped, as you told me. I saw no changed words.

## Mutants
- All five controls I named are broken by at least one mutant: the owner check, the allow-list (two), the card check, `shp`/`srv` (five, at mint and verify), and the session in the shopper mac. The old strings I compared with the source match. The script counts "NOT APPLIED" as not killed and reports 0.
- The script edits only the scratch copy, restores each file, and asserts that the four main packages are imported from the copy. `unmutated` and `restored` both show 176, which equals 66 + 31 + 26 + 29 + 18 + 6 for the six groups of tests.
- Where I worked out the expected failures (the six `shp`/`qf` mutants), the logged counts match: 5, 2, 8, 8, 11, 1.
- **Not covered:** `tw_gaps`' streamed blocked-claim tests and `tw_edges`' token-expiry and foreign-key tests. The first group pins main's unchanged check, and the second the existing token gate, so I do not raise them.

## Checked
1. **Scope: checked.** Only the files you named appear changed. The diff has two more new files than before (the mutants script and my report). Blob IDs I could compare with the first diff are unchanged: `mcp_token.py`, `tool_scopes.py`, `book.py`, `tools.py`, `server.py`, `session.py`, `signin.py`, `settings.py`, `chat.py`, `turn.py`, `prompt.py`, `orders_client.py`, the baseline, two cassettes, and the row's other test files I had read. I had no earlier blob ID for the other nine cassettes; still 11, all added.
2. **Failing first: checked,** with R-2 and R-3. The feature's evidence is after the fact, and the records say so.
3. **No weakened test: checked.** One test file changed, by addition only.
4. **Hard rules and protected paths: checked,** R-1 and R-4. The one `src` change reads as docstring only.
5. **Spend and window: checked.** No new spend is claimed. I did not read the ledger.
6. **Requested changes: checked.** The plan now holds the items it lacked.
7. **Control surface: listed below.**

Unconfirmed: that the mutants ran as logged and on which commit; the user's quoted words; that `prompt-02210891` matches this `prompt.py`; `make ci`, not yet run. `security-reviewer` has still read nothing after `57635e2`.

## Control surface
Same set as before; two of these files changed since 0c0db58.
- `Makefile`
- `pyproject.toml` (root)
- `uv.lock`
- `services/mcp-orders/pyproject.toml` (new)
- `infra/docker-compose.yml`
- `evals/baselines/dense-only.json` (`baseline-change` label needed)
- `services/common/src/northwind_common/mcp_token.py`
- `services/common/src/northwind_common/order_tool.py` (new; **docstring changed since 0c0db58**)
- `services/common/src/northwind_common/tool_scopes.py`
- `services/common/tests/test_chk06_shopper_claim.py` (new; **one test and its constant added since 0c0db58**)
- `services/common/tests/test_mcp_token.py`
- `services/common/tests/test_tool_scopes.py`
- `cassettes/assistant-orders/`, 11 added: `334613e0…`, `489ccd9a…`, `534b0e00…`, `5c8254d2…`, `6354fbeb…`, `a1969c24…`, `b040ca89…`, `c39cf32f…`, `d6e2cc97…`, `e717ce86…`, `ff4efcb7…`

Any fix moves the head, so `make ci` runs after it, on the new SHA.

**For the user, in the gate report:** the M-1 gap and its owner (R-1), the promote condition (L-2), `CLAUDE.md`'s `make dev` line (L-1), and that the row's security evidence was made after the fact (M-2).
