# SA-01 PR 2: fail-first evidence (advisor-reviewer M1)

Each mutant edits one file, runs the named test, and restores the file (`git diff` is empty
afterwards). A fix's test must fail on its mutant; the outcome is copied from pytest/Vitest.
Earlier fail-first runs (the test-writer double pay, PR 1's F4, F1-F6) are listed in the PR body.

## F7: the Host allow-list dropped from `from_portal`

File: `services/gateway/src/northwind_gateway/portal_write.py`. Exit code 1.

```
FAILED services/gateway/tests/sa01_checkout/test_sa01_pr2_security_fixes.py::test_f7_a_rebinding_host_is_refused_even_with_a_matching_origin
1 failed, 19 deselected in 1.17s
```

## Write guard: the portal header check dropped from `from_portal`

File: `services/gateway/src/northwind_gateway/portal_write.py`. Exit code 1.

```
FAILED services/gateway/tests/sa01_checkout/test_sa01_pr2_tw_cart_boundaries.py::test_every_cart_write_is_behind_the_guard_with_a_403_permission_envelope[/api/cart/items-headers0]
FAILED services/gateway/tests/sa01_checkout/test_sa01_pr2_tw_cart_boundaries.py::test_every_cart_write_is_behind_the_guard_with_a_403_permission_envelope[/api/cart/items-headers1]
FAILED services/gateway/tests/sa01_checkout/test_sa01_pr2_tw_cart_boundaries.py::test_every_cart_write_is_behind_the_guard_with_a_403_permission_envelope[/api/cart/quantity-headers0]
FAILED services/gateway/tests/sa01_checkout/test_sa01_pr2_tw_cart_boundaries.py::test_every_cart_write_is_behind_the_guard_with_a_403_permission_envelope[/api/cart/quantity-headers1]
6 failed, 26 passed, 88 deselected in 14.75s
```

## F3: the digest back to hex

File: `services/gateway/src/northwind_gateway/checkout.py`. Exit code 1.

```
FAILED services/gateway/tests/sa01_checkout/test_sa01_pr2_tw_checkout_edges.py::test_the_digest_is_stable_between_reads_and_follows_the_bag_and_the_price
1 failed, 46 deselected in 1.19s
```

## F8/N5: `+` no longer folded in the issuer pin

File: `services/backend/tests/test_be05_prerequisites.py`. Exit code 1.

```
FAILED services/backend/tests/test_be05_prerequisites.py::test_a_planted_issuer_is_caught[def f(book):\n    return getattr(book, 'is' + 'sue')('O1', 1)\n]
1 failed, 9 passed, 39 deselected in 0.13s
```

## N5: a backend module writes the book's `_issued` store

File: `services/backend/src/northwind_backend/fakes/fraud.py`. Exit code 1.

```
FAILED services/backend/tests/test_be05_prerequisites.py::test_nothing_in_the_backend_but_confirmations_touches_the_book_store
1 failed, 48 deselected in 0.20s
```

## N4: the portal no longer rereads after `already_placed`

File: `apps/portal/src/checkoutPages.tsx`. Exit code 1.

```
× already placed rereads the bag and the summary, since the gateway removed paid lines (re-review N4) 1080ms
Tests  1 failed | 15 skipped (16)
```

## Condition (3), runtime guard positive control: `pay` calls `anthropic_client()`

File: `services/gateway/src/northwind_gateway/checkout.py`. Exit code 1.

```
FAILED services/gateway/tests/sa01_checkout/test_sa01_pr2_checkout.py::test_a_full_pay_never_reaches_any_llm_entry_point
1 failed, 20 deselected in 1.30s
```

## Condition (3), proxy positive control: `pay` sends one request to the replay proxy

File: `services/gateway/src/northwind_gateway/checkout.py`. Exit code 1.

```
FAILED services/gateway/tests/sa01_checkout/test_sa01_pr2_checkout.py::test_the_replay_proxy_sees_zero_requests_during_pay
1 failed, 20 deselected in 1.37s
```

## Cart writes: the gateway's own card-data check dropped (first run: the old test still passed, because mcp-catalog refuses too; a test that no cart tool is called was added)

File: `services/gateway/src/northwind_gateway/cart_writes.py`. Exit code 1.

```
FAILED services/gateway/tests/sa01_checkout/test_sa01_pr2_security_fixes.py::test_card_data_is_refused_by_the_gateway_before_any_cart_tool_is_called[idempotency_key]
1 failed, 1 passed, 18 deselected in 1.49s
```

## N2, N3: the re-review fixes, against 8d902a2's `checkout.py`

Exit code 1.

```
FAILED services/gateway/tests/sa01_checkout/test_sa01_pr2_security_fixes.py::test_n2_a_failed_placement_is_escalated_shown_on_hold_and_claims_no_payment
FAILED services/gateway/tests/sa01_checkout/test_sa01_pr2_security_fixes.py::test_n3_an_unreadable_bag_after_an_order_is_held_never_called_empty
2 failed, 18 deselected in 1.60s
```
