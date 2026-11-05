# FOUND-3 evidence: scope decisions, stop, reviews and failing-first log

Date: 2026-11-05 · Branch: `req/FOUND-3-backend-scaffold` · Base: `f50f7c8`
Row 19, narrowed by the user, so **partial**. A commit can't name its own SHA, so the gate report gives
the final head and the `make ci` and `make eval-gate` results at it.

## The stop, now resolved

`git switch -c req/FOUND-3-backend-scaffold origin/main` printed:

```
error: could not lock config file /Users/rapido/Projects/northwind-shop/.git/config: Operation not permitted
error: unable to write upstream branch configuration
```

It exited 1. **Cause: Claude ran it without `--no-track`**, which `/req-start` and decision 0006 both
require in the sandbox, before reading either. The branch was created at `f50f7c8` with no upstream;
HEAD stayed detached at the same commit; the tree stayed clean.

This is stop 4. Decision 0006 says a config-only failure still means stop and report, and that Claude
does not commit. Its recovery does not cover a detached HEAD. The user's "no extra stops" was read as
leaving the listed stops in place.

Continuing with independent work was a choice, not explicit text: CLAUDE.md's "finish all work that
does not depend on it" is written for stopping for a decision, and the "How we work" line just says
stop. `advisor-reviewer` called the reading sound, because the work does not depend on the branch and
the user was away. From then on Claude ran no state-changing git command: no `add`, `commit`, `switch`,
`config` or `stash`. Diffs for review came from `git diff --no-index` with `GIT_OPTIONAL_LOCKS=0`.

**Resolved by the user.** They told Claude to run `git switch req/FOUND-3-backend-scaffold` on its own,
and to stop if any of the five error strings appeared. Both refs were `f50f7c8`. The switch printed
none of them and exited 0, so the tree did not change. The chain then resumed at the commit.

## Other things to know

- **`origin/main` may be stale.** `git fetch` failed in the sandbox with no GitHub credentials. Base and
  the GOV-1 check below use the local ref.
- **GOV-1 clash, not visible here.** `docs/governance/` is not on local `origin/main`, so by the user's
  condition `data-flow.md` was left alone. But GOV-1 is in progress in `northwind-shop-c`: its
  `tests/governance/test_data_flow.py` fails for any `services/` directory with no row in the table, and
  its table has no `services/backend/` row. Whichever of the two merges second fails that test. `-c`
  was not touched. `-c` also shows the same refs pattern (detached at `f50f7c8`, branch at the same
  commit), so it may have hit this same failure; that is inferred from refs only.
- **Spend** is compared per suite, because the ledger is shared with other windows.

## Scope decisions (narrower option taken, as instructed)

| Question | Taken |
|---|---|
| `services/backend/pyproject.toml` is on the control surface through `**/pyproject.toml` and was not pre-approved | Kept: the approved workspace entry needs a member. **Approved by the user afterwards**, provided it matches the siblings. Checked: `[build-system]` requires `uv_build>=0.12,<0.13` only, the same pin as all five siblings, with `build-backend = "uv_build"`; no `[tool.uv.build-backend]`, no `[tool.uv.workspace]`, no git, URL or path source. Its one source is `northwind-common = { workspace = true }`, as in `assistant` and `llm-replay` |
| Does `manifest.json` in "do not touch" include a new `data/backend/manifest.json`? | Read as the fixtures manifest only. `data/backend/manifest.json` is new, and has its own version constant |
| Amounts on orders | Omitted: "by ID only", and hard rule 2. A later row joins `prices.jsonl` |
| PRD backend replay labels (expected outcome, escalation flag) | Not generated: not asked for. On the left list |
| Phone numbers on shoppers | Omitted: not asked for |
| A regeneration target | None: no Makefile targets. `uv run python -m northwind_backend.cli` |
| Refund cap values | Chosen here, none is given: USD 150.00, CAD 200.00, GBP 120.00, EUR 140.00, set in `DEFAULT_REFUND_CAPS` |
| Per-caller scoped views (security finding 3) | Not built, recorded. A partial-scope test per port was added |
| Portal confirmation on the port (security finding 4) | Not built, recorded: a decision for the user |
| Plan said every string argument goes through the card checks, including `reason` | Deviation: `refund` has no `reason` parameter, so there is nothing to check. `region`, `service` and currency skip `card_data_in` and are refused by allow-list or pattern instead, with fixed messages and nothing stored |
| Soft decline `isRetryable` | False: the result is recorded, so the same call can't succeed. A new attempt takes a new key |

## Reviews

**Plan review** (`advisor-reviewer`, `findings`): three medium, three low, no high, no hard rule broken,
so the row proceeded under the user's condition. All six taken into the work or this file.

**`security-reviewer`** (code and tests; `data/backend/*` was not in the diff, so only tests have checked
the generated data): nothing high or critical. Four medium, three low.

| # | Sev | Finding | Outcome |
|---|---|---|---|
| 1 | Medium | Non-ASCII digits in IDs evaded the fraud flag and the ship-once guard | Fixed: `re.ASCII` on every ID pattern |
| 2 | Medium | A second key could authorize and charge one order, and multiply the refund cap | Fixed: one non-voided intent per order, which also makes the cap per order |
| 3 | Medium | Scopes are per instance, so least privilege can't be expressed | **Open**, see below. Partial-scope tests added |
| 4 | Medium | No confirmation on the payment port; `authorize` and `capture` have no real limit | **Open**, see below |
| 5 | Low | `track` could raise on an unknown prefix | Fixed |
| 6 | Low | Order rows carry internal labels (`exception`, `payment_token`) | **Open**, recorded for `mcp-orders` |
| 7 | Low | A test child process inherited the full environment | Fixed |

**Scoped re-review** (`security-reviewer`, fixes only): no high or critical open. Fixes 1, 2, 5 and 7
closed. Findings 3 and 4 accepted as open mediums if tracked, which `PROGRESS.md` row 19 now does. Four
lows, all handled **after** the one allowed re-review, so **`security-reviewer` has not seen these last
changes**. `advisor-reviewer` read them in the row review as a process read, not a security review:

- Dry-run `authorize` could raise during a concurrent insert. Fixed: dry runs take the store lock
  (`IdempotencyStore.exclusive`).
- The `order_already_authorized` message promised a void that might not work. Reworded. A lookup by
  order for a caller that lost its key is on the left list (PAY-08).
- Findings 3, 4 and 6 tracked only in docstrings. Now in `PROGRESS.md`.
- The test file claimed every test failed before its fix. Corrected, and a test added for a refunded
  order blocking a new authorization, the case that would actually reset the cap.

Finding 4 re-rates to **high** as soon as anything calls `authorize` or `capture` without a
confirmation layer. Finding 3's per-caller views must share intents, store and caps, because the
one-intent-per-order rule and the refund cap hold only within one instance.

**Row review** (`advisor-reviewer` at `b79caac`, `findings`): no high or critical, no stop condition.
Three medium and five low, all about what the evidence proves. Handled in a second commit:

| # | Sev | Finding | Outcome |
|---|---|---|---|
| M1 | Medium | The dry-run lock in `_commit` had no test through a fake, and its test's failing-first claim was unsupported | Fixed: `test_a_fakes_dry_run_waits_for_a_real_call_and_then_sees_its_result`. With the line reverted it failed and the other 206 passed. Claim corrected below |
| M2 | Medium | "75 of 75" was wrong: `store lock` had become the same mutation as `dry run holds the lock`, so the lock in `run` had no mutant on the final code | Fixed: the script now refuses duplicate mutants. The real figure then was 74 distinct. Now 76, each lock with its own killer |
| M3 | Medium | "All other tests are proved by the mutants" was false for `test_found3_data_safety.py` | Fixed: a planted-data pass below, and a control for the three detectors that had none |
| L1 | Low | "Full diff" overstated the first security review; "not reviewed" became half true | Corrected above |
| L2 | Low | `PROGRESS.md` named the user as running the switch | Corrected: Claude ran it on the user's instruction |
| L3 | Low | "The same count as AUTO-1" was true of xfails only | Corrected in Results |
| L4 | Low | Two plan deviations unrecorded | Recorded in the scope table. `region` and `service` added to `test_as_other_string_fields` |
| L5 | Low | The PR body left out the unreviewed fixes and the possibly stale `origin/main` | Added |

## Failing-first

`test-writer` wrote its 149 tests after the code, and all passed on first run, so none had been seen
failing. Removing each guard in turn showed why that proved little: **31 of the first 61 mutants
survived.** Tests asserted "refused", and a neighbouring guard refused instead. Claude added
`test_found3_guards.py` and `test_found3_review_fixes.py`, which assert the exact error, then re-ran.

Seen failing before their fix, on real defects:

- 2 tests: `test_the_carrier_names_a_bad_destination` for an unhashable `region` or `service` (`TypeError`)
- 11 tests from security findings 1, 2 and 5: `test_non_ascii_digits_*` (2),
  `test_every_id_pattern_takes_ascii_digits_only` (5), `test_a_second_key_cannot_authorize_an_order_again`,
  `test_a_lost_key_cannot_charge_an_order_twice`, `test_a_dry_run_also_reports_an_order_already_authorized`,
  `test_track_refuses_an_unknown_carrier_prefix_without_raising`
- 1 test from the re-review: `test_a_refused_second_authorization_does_not_promise_a_void_will_work`
- 1 test from the row review: `test_a_fakes_dry_run_waits_for_a_real_call_and_then_sees_its_result`, with
  the dry-run lock reverted (`assert waited`; the other 206 passed)

Not a real defect, though earlier versions of this file listed it as one:
`test_a_dry_run_waits_for_an_effect_in_progress` failed first, but `exclusive` did not exist yet, so it
probably failed on the missing method, not on the race. The log holds only the `FAILED` line. Its proof
is the `dry run holds the lock` mutant below.

All other tests passed first time. That includes the partial-scope, voided-frees-the-order,
declined-attempt and refund-reset tests. Mutants run with `-x`, so each one proves only the test it
names, not every test that covers that guard. Many tests are named by no mutant, so they have not been
seen failing at all.

`test_found3_data_safety.py` reads the committed data, so no generator mutant reaches it. It was proved
by planting one bad value at a time in `data/backend/`, restored by bytes (see below).

The one test input changed after a failure: the `_intent` helper now uses a distinct order per intent,
which fix 2 requires. No assertion changed; `security-reviewer` confirmed that.

Mutation script: kept in the scratchpad, not committed (AUTO-2 item 8 covers committing one). Source
hashes matched before and after every pass. The final pass logs the source hash, and refuses two mutants
that produce the same source.

### First pass: 61 mutants, 31 survived

```
SURVIVED    transient not recorded
SURVIVED    store lock
SURVIVED    contains_pan
SURVIVED    contains_cvv
SURVIVED    long digit run
SURVIVED    key pattern
SURVIVED    amount positive
SURVIVED    bool is not an amount
SURVIVED    currency pattern
SURVIVED    token pattern
SURVIVED    intent pattern
SURVIVED    unknown token
SURVIVED    capture of voided
SURVIVED    capture currency
SURVIVED    over authorized
SURVIVED    void of voided
SURVIVED    void of captured
SURVIVED    refund before capture
SURVIVED    refund currency
SURVIVED    caps copied
SURVIVED    status card check
SURVIVED    shopper pattern
SURVIVED    fraud read card check
SURVIVED    one shipment per order
SURVIVED    tracking pattern
SURVIVED    postal pattern
SURVIVED    service check
SURVIVED    track card check
SURVIVED    public variants only
SURVIVED    public products only
SURVIVED    priced in region only
```

### Final pass: on the final code

```
source sha256: b9509c62fad91f0e0676319c69c3cbfa3f43472e5fae576d5a5932f24f70decf
killed      replay returns first: test_found3_guards.py::test_a_transient_result_from_an_effect_is_not_recorded
killed      key conflict refused: test_found3_idempotency.py::test_a_different_request_on_a_used_key_is_refused
killed      transient not recorded: test_found3_guards.py::test_a_transient_result_from_an_effect_is_not_recorded
killed      store lock: test_found3_guards.py::test_the_store_runs_a_slow_effect_once_under_concurrent_replays
killed      fake dry run takes the lock: test_found3_review_fixes.py::test_a_fakes_dry_run_waits_for_a_real_call_and_then_sees_its_result
killed      only transient retryable: test_found3_idempotency.py::test_a_transient_failure_is_not_recorded_so_a_retry_does_the_effect_once[authorize]
killed      business not retryable: test_found3_refund_and_errors.py::test_refund_at_the_cap_passes_and_one_minor_unit_over_is_refused[CAD]
killed      scope is per operation: test_found3_review_fixes.py::test_a_refund_only_payment_credential_cannot_capture_or_void
killed      order id ascii: test_found3_review_fixes.py::test_non_ascii_digits_cannot_alias_a_flagged_order
killed      intent ascii: test_found3_review_fixes.py::test_every_id_pattern_takes_ascii_digits_only[<lambda>-invalid_intent]
killed      token ascii: test_found3_review_fixes.py::test_every_id_pattern_takes_ascii_digits_only[<lambda>-invalid_token]
killed      shopper ascii: test_found3_review_fixes.py::test_every_id_pattern_takes_ascii_digits_only[<lambda>-invalid_shopper_id]
killed      postal ascii: test_found3_review_fixes.py::test_every_id_pattern_takes_ascii_digits_only[<lambda>-invalid_postal_code]
killed      one live intent per order: test_found3_review_fixes.py::test_a_second_key_cannot_authorize_an_order_again
killed      voided frees the order: test_found3_review_fixes.py::test_a_voided_authorization_frees_the_order
killed      tracking prefix known: test_found3_review_fixes.py::test_track_refuses_an_unknown_carrier_prefix_without_raising
killed      scope check: test_found3_refund_and_errors.py::test_every_error_category_on_every_port[carrier-permission]
killed      outage: test_found3_idempotency.py::test_a_transient_failure_is_not_recorded_so_a_retry_does_the_effect_once[authorize]
killed      card refused before storing: test_found3_card_data.py::TestCardLookingValuesAreRefused::test_as_idempotency_key_on_every_state_changing_call[4111111111111111]
killed      contains_pan: test_found3_guards.py::test_each_card_check_refuses_what_only_it_can_see[overrides2]
killed      contains_cvv: test_found3_guards.py::test_each_card_check_refuses_what_only_it_can_see[overrides1]
killed      long digit run: test_found3_guards.py::test_each_card_check_refuses_what_only_it_can_see[overrides0]
killed      key pattern: test_found3_guards.py::test_authorize_names_the_input_it_refuses[overrides0-invalid_idempotency_key]
killed      order id pattern: test_found3_refund_and_errors.py::test_every_error_category_on_every_port[fraud-validation]
killed      amount upper bound: test_found3_card_data.py::test_an_amount_that_looks_like_a_card_number_is_refused[4111111111111111]
killed      amount positive: test_found3_guards.py::test_authorize_names_the_input_it_refuses[overrides7-invalid_amount]
killed      bool is not an amount: test_found3_guards.py::test_authorize_names_the_input_it_refuses[overrides9-invalid_amount]
killed      currency pattern: test_found3_guards.py::test_authorize_names_the_input_it_refuses[overrides10-invalid_currency]
killed      dry_run skips store and effect: test_found3_idempotency.py::test_dry_run_has_no_effect_and_does_not_burn_the_key[authorize]
killed      dry run holds the lock: test_found3_review_fixes.py::test_a_dry_run_waits_for_an_effect_in_progress
killed      partly refunded still live: test_found3_review_fixes.py::test_a_new_intent_cannot_reset_the_refund_cap[partly]
killed      refunded still live: test_found3_review_fixes.py::test_a_fully_refunded_order_cannot_be_authorized_again
killed      token pattern: test_found3_guards.py::test_authorize_names_the_input_it_refuses[overrides4-invalid_token]
killed      intent pattern: test_found3_guards.py::test_a_malformed_intent_is_not_reported_as_an_unknown_one
killed      soft decline: test_found3_generator.py::test_soft_decline_orders_are_declined_and_others_authorize
killed      unknown token: test_found3_guards.py::test_authorize_names_the_input_it_refuses[overrides6-unknown_token]
killed      second key cannot capture: test_found3_idempotency.py::test_a_second_key_cannot_capture_twice
killed      capture of voided: test_found3_guards.py::test_capture_names_why_it_refuses
killed      capture currency: test_found3_guards.py::test_capture_names_why_it_refuses
killed      over authorized: test_found3_guards.py::test_capture_names_why_it_refuses
killed      void of voided: test_found3_guards.py::test_void_names_why_it_refuses
killed      void of captured: test_found3_guards.py::test_void_names_why_it_refuses
killed      refund before capture: test_found3_guards.py::test_refund_names_why_it_refuses
killed      refund currency: test_found3_guards.py::test_refund_names_why_it_refuses
killed      refund over captured: test_found3_refund_and_errors.py::test_refund_above_what_was_captured_is_refused
killed      no cap fails closed: test_found3_refund_and_errors.py::test_a_currency_with_no_cap_is_refused
killed      refund cap: test_found3_guards.py::test_changing_the_caps_mapping_afterwards_does_not_move_the_cap
killed      cap is cumulative: test_found3_refund_and_errors.py::test_cumulative_split_refunds_cannot_pass_the_cap
killed      cap boundary: test_found3_refund_and_errors.py::test_refund_at_the_cap_passes_and_one_minor_unit_over_is_refused[CAD]
killed      caps copied: test_found3_guards.py::test_changing_the_caps_mapping_afterwards_does_not_move_the_cap
killed      dry payment not saved: test_found3_idempotency.py::test_dry_run_has_no_effect_and_does_not_burn_the_key[capture]
killed      status card check: test_found3_guards.py::test_read_calls_refuse_card_data_by_name
killed      one assessment per order: test_found3_refund_and_errors.py::test_every_error_category_on_every_port[fraud-business]
killed      flagged goes to review: test_found3_generator.py::test_fraud_flag_orders_get_review_and_others_allow
killed      shopper pattern: test_found3_guards.py::test_fraud_names_a_malformed_shopper
killed      fraud read card check: test_found3_guards.py::test_read_calls_refuse_card_data_by_name
killed      undeliverable: test_found3_generator.py::test_address_error_orders_are_refused_by_the_carrier_and_others_accepted
killed      one shipment per order: test_found3_guards.py::test_a_second_key_cannot_ship_an_order_twice
killed      unknown tracking: test_found3_guards.py::test_a_malformed_tracking_number_is_not_reported_as_an_unknown_one
killed      tracking pattern: test_found3_guards.py::test_a_malformed_tracking_number_is_not_reported_as_an_unknown_one
killed      region check: test_found3_card_data.py::TestCardLookingValuesAreRefused::test_as_other_string_fields[4111111111111111]
killed      region str first: test_found3_guards.py::test_the_carrier_names_a_bad_destination[overrides1-invalid_region]
killed      postal pattern: test_found3_guards.py::test_the_carrier_names_a_bad_destination[overrides2-invalid_postal_code]
killed      service check: test_found3_card_data.py::TestCardLookingValuesAreRefused::test_as_other_string_fields[4111111111111111]
killed      service str first: test_found3_guards.py::test_the_carrier_names_a_bad_destination[overrides4-invalid_service]
killed      track card check: test_found3_guards.py::test_read_calls_refuse_card_data_by_name
killed      public variants only: test_found3_guards.py::test_only_public_active_priced_variants_are_sellable
killed      public products only: test_found3_guards.py::test_only_public_active_priced_variants_are_sellable
killed      priced in region only: test_found3_guards.py::test_only_public_active_priced_variants_are_sellable
killed      stock summed over nodes: test_found3_generator.py::test_committed_data_backend_equals_a_fresh_build
killed      exception count: test_found3_generator.py::test_committed_data_backend_equals_a_fresh_build
killed      reserved email domain: test_found3_generator.py::test_committed_data_backend_equals_a_fresh_build
killed      seed used: test_found3_generator.py::test_committed_data_backend_equals_a_fresh_build
killed      held orders get no tracking: test_found3_generator.py::test_committed_data_backend_equals_a_fresh_build
killed      sorted stock order: test_found3_generator.py::test_committed_data_backend_equals_a_fresh_build
killed      cli refuses fixtures: test_found3_generator.py::test_the_cli_refuses_an_output_directory_inside_the_fixtures

76 mutants, 0 survived, 0 no match; source restored: True
```

### Planted data: `test_found3_data_safety.py`

The 17th test is the control added for M3. It has no data input, so planted data can't reach it.

```
PAN in orders: 4 failed: test_no_digit_run_of_13_or_more_anywhere[orders.jsonl], test_no_string_field_contains_a_pan, test_the_cassette_scanner_finds_nothing_in_data_backend[orders.jsonl], test_the_no_pan_checks_can_fail
PAN in shoppers: 3 failed: test_no_digit_run_of_13_or_more_anywhere[shoppers.jsonl], test_no_string_field_contains_a_pan, test_the_cassette_scanner_finds_nothing_in_data_backend[shoppers.jsonl]
PAN in tracking: 3 failed: test_no_digit_run_of_13_or_more_anywhere[tracking.jsonl], test_no_string_field_contains_a_pan, test_the_cassette_scanner_finds_nothing_in_data_backend[tracking.jsonl]
PAN in manifest: 3 failed: test_no_digit_run_of_13_or_more_anywhere[manifest.json], test_no_string_field_contains_a_pan, test_the_cassette_scanner_finds_nothing_in_data_backend[manifest.json]
unreserved email: 1 failed: test_emails_use_only_reserved_domains
real brand: 1 failed: test_no_real_brand_or_carrier_name_appears_anywhere
real carrier: 2 failed: test_every_carrier_is_a_fictional_one_from_carriers, test_no_real_brand_or_carrier_name_appears_anywhere
unlisted carrier: 1 failed: test_every_carrier_is_a_fictional_one_from_carriers
unknown variant: 1 failed: test_every_product_and_variant_resolves_in_fixtures_and_is_public_and_priced
price key on a line: 1 failed: test_a_line_carries_only_ids_and_a_quantity_and_orders_carry_no_money_or_stock
money in a string: 1 failed: test_a_line_carries_only_ids_and_a_quantity_and_orders_carry_no_money_or_stock
order in the wrong region: 2 failed: test_every_order_refers_to_a_generated_shopper_in_the_same_region, test_every_product_and_variant_resolves_in_fixtures_and_is_public_and_priced
planted token gone: 1 failed: test_the_no_pan_checks_can_fail

17 tests, 16 seen failing; never failed: ['test_the_host_brand_and_money_checks_can_fail']; data restored: True
```

## Results

At `b79caac`, the first commit, clean tree, make's own exit codes:

- `make ci`: exit 0. Lint clean; cassette scan clean; 2,483 passed, 22 xfailed; 34 portal; 86 integration;
  `eval-integrity` passed against local `origin/main`. AUTO-1 was 2,278 passed and 22 xfailed, so the xfails
  are unchanged and the passes are AUTO-1's plus this row's 205.
- `make eval-gate`: exit 0, equal to the baseline on every metric, 100/100 replayed, 0 stale.

The second commit adds two tests and widens one, with no source change. The gate report has the results
at the final head. On that tree:

- FOUND-3 tagged tests: 207 passed, none skipped or xfailed.
- Mutants: 76 of 76 distinct mutants killed. Planted data: 16 of 16 data-safety tests seen failing.
- Spend: total $1.150794 before and after, 2,598 settled calls before and after. This row added no call,
  and every suite is unchanged. The $0.113222 above the $1.037572 in the AUTO-1 row is exactly suite
  `rag-enrichment`, another window's.
- `uv.lock`: `northwind-backend` entries only, no third-party change.
- `data/src`, `data/fixtures`, `evals`, `cassettes`, `Makefile`, `.github`, `conftest.py`: no change.
- Not run: the browser job, which runs only in GitHub CI. `make up` was not needed: Postgres was up.
- `vitest` printed `failed to copy trust settings of system certificate` 24 times in the sandbox. The
  tests passed; the cause was not investigated.

Control surface changed: root `pyproject.toml` and `uv.lock` (pre-approved),
`services/backend/pyproject.toml` (approved afterwards, see scope decisions).
