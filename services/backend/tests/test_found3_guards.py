"""FOUND-3: one test per guard that a neighbouring guard would otherwise hide.

Removing each guard in turn showed "refused" is too weak to assert: a capture of a voided payment is
refused by the next check too. So these name the exact error, and the generator's filters run on a
small catalog where each one is the only thing standing.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.errors import Ok, Result, ToolError, transient
from northwind_backend.fakes.carrier import CarrierFake
from northwind_backend.fakes.fraud import FraudFake
from northwind_backend.fakes.payment import SANDBOX_APPROVED, PaymentFake
from northwind_backend.generate import load_catalog
from northwind_backend.idempotency import IdempotencyStore
from northwind_backend.ports import Money

USD = Money(5_000, "USD")
# Not Luhn-valid in any 13 to 16 digit window, so only the long-digit check can refuse it.
LONG_NON_LUHN = "1000000000000100"
WIDE_PAN = "4242 then 4242 then 4242 then 4242"
PAN = "4111111111111111"


def _code(result: Result[Any]) -> str:
    assert isinstance(result, ToolError), result
    return result.code


def _authorized(fake: PaymentFake, key: str = "a") -> str:
    result = fake.authorize(
        idempotency_key=f"auth-{key}",
        payment_token=SANDBOX_APPROVED,
        amount=USD,
        order_id="O000001",
    )
    assert isinstance(result, Ok)
    return result.value.intent_id


def _captured(fake: PaymentFake) -> str:
    intent_id = _authorized(fake)
    assert isinstance(fake.capture(idempotency_key="cap", intent_id=intent_id), Ok)
    return intent_id


def _voided(fake: PaymentFake) -> str:
    intent_id = _authorized(fake)
    assert isinstance(fake.void(idempotency_key="void", intent_id=intent_id), Ok)
    return intent_id


def _authorize_with(fake: PaymentFake, **overrides: Any) -> Result[Any]:
    args: dict[str, Any] = {
        "idempotency_key": "k",
        "payment_token": SANDBOX_APPROVED,
        "amount": USD,
        "order_id": "O000001",
    }
    return fake.authorize(**(args | overrides))


@pytest.mark.req("FOUND-3")
def test_a_transient_result_from_an_effect_is_not_recorded() -> None:
    store = IdempotencyStore()
    calls: list[int] = []

    def effect() -> Result[str]:
        calls.append(1)
        return transient("blip", "try again") if len(calls) == 1 else Ok("done")

    assert isinstance(store.run("k", "h", effect), ToolError)
    assert store.run("k", "h", effect) == Ok("done")
    assert store.run("k", "h", effect) == Ok("done")
    assert len(calls) == 2


@pytest.mark.req("FOUND-3")
def test_the_store_runs_a_slow_effect_once_under_concurrent_replays() -> None:
    store = IdempotencyStore()
    calls: list[int] = []
    start = threading.Barrier(8)

    def effect() -> Result[str]:
        calls.append(1)
        time.sleep(0.05)
        return Ok("done")

    def replay() -> None:
        start.wait()
        store.run("k", "h", effect)

    threads = [threading.Thread(target=replay) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"idempotency_key": ""}, "invalid_idempotency_key"),
        ({"idempotency_key": "has a space"}, "invalid_idempotency_key"),
        ({"idempotency_key": None}, "invalid_idempotency_key"),
        ({"idempotency_key": "x" * 129}, "invalid_idempotency_key"),
        ({"payment_token": "not-a-token"}, "invalid_token"),
        ({"payment_token": "tok_sandbox_ok_1234567"}, "invalid_token"),
        ({"payment_token": "tok_never_issued"}, "unknown_token"),
        ({"amount": Money(0, "USD")}, "invalid_amount"),
        ({"amount": Money(-1, "USD")}, "invalid_amount"),
        ({"amount": Money(True, "USD")}, "invalid_amount"),
        ({"amount": Money(1, "usd")}, "invalid_currency"),
        ({"amount": "5000"}, "invalid_amount"),
    ],
)
def test_authorize_names_the_input_it_refuses(overrides: dict[str, Any], code: str) -> None:
    fake = PaymentFake()
    assert _code(_authorize_with(fake, **overrides)) == code
    assert fake.effects == ()


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize(
    ("overrides"),
    [
        {"idempotency_key": LONG_NON_LUHN},
        {"idempotency_key": "cvv-737"},
        {"order_id": WIDE_PAN},
        {"payment_token": WIDE_PAN},
    ],
)
def test_each_card_check_refuses_what_only_it_can_see(overrides: dict[str, Any]) -> None:
    fake = PaymentFake()
    assert _code(_authorize_with(fake, **overrides)) == "card_data_refused"
    assert fake.effects == ()


@pytest.mark.req("FOUND-3")
def test_read_calls_refuse_card_data_by_name() -> None:
    assert _code(PaymentFake().status(PAN)) == "card_data_refused"
    assert _code(FraudFake().assessments_for(PAN)) == "card_data_refused"
    assert _code(CarrierFake().track(PAN)) == "card_data_refused"


@pytest.mark.req("FOUND-3")
def test_a_malformed_intent_is_not_reported_as_an_unknown_one() -> None:
    fake = PaymentFake()
    assert _code(fake.status("pi_abc")) == "invalid_intent"
    assert _code(fake.status("pi_000099")) == "unknown_intent"


@pytest.mark.req("FOUND-3")
def test_capture_names_why_it_refuses() -> None:
    fake = PaymentFake()
    assert _code(fake.capture(idempotency_key="c", intent_id=_voided(fake))) == "intent_voided"

    fake = PaymentFake()
    intent_id = _authorized(fake)
    over = Money(USD.amount_minor + 1, "USD")
    assert (
        _code(fake.capture(idempotency_key="c1", intent_id=intent_id, amount=over))
        == "over_authorized"
    )
    assert (
        _code(fake.capture(idempotency_key="c2", intent_id=intent_id, amount=Money(100, "EUR")))
        == "currency_mismatch"
    )


@pytest.mark.req("FOUND-3")
def test_void_names_why_it_refuses() -> None:
    fake = PaymentFake()
    assert _code(fake.void(idempotency_key="v", intent_id=_voided(fake))) == "intent_voided"

    fake = PaymentFake()
    assert _code(fake.void(idempotency_key="v", intent_id=_captured(fake))) == "already_captured"


@pytest.mark.req("FOUND-3")
def test_refund_names_why_it_refuses() -> None:
    fake = PaymentFake()
    small = Money(100, "USD")
    assert (
        _code(fake.refund(idempotency_key="r", intent_id=_authorized(fake), amount=small))
        == "not_captured"
    )

    fake = PaymentFake()
    assert (
        _code(fake.refund(idempotency_key="r", intent_id=_voided(fake), amount=small))
        == "not_captured"
    )

    fake = PaymentFake()
    assert (
        _code(fake.refund(idempotency_key="r", intent_id=_captured(fake), amount=Money(1, "EUR")))
        == "currency_mismatch"
    )


@pytest.mark.req("FOUND-3")
def test_changing_the_caps_mapping_afterwards_does_not_move_the_cap() -> None:
    caps = {"USD": 100}
    fake = PaymentFake(refund_caps=caps)
    intent_id = _captured(fake)
    caps["USD"] = 1_000_000
    result = fake.refund(idempotency_key="r", intent_id=intent_id, amount=Money(101, "USD"))
    assert _code(result) == "refund_over_cap"


@pytest.mark.req("FOUND-3")
def test_fraud_names_a_malformed_shopper() -> None:
    result = FraudFake().assess(idempotency_key="k", order_id="O000001", shopper_id="nobody")
    assert _code(result) == "invalid_shopper_id"


def _ship(fake: CarrierFake, **overrides: Any) -> Result[Any]:
    args: dict[str, Any] = {
        "idempotency_key": "k",
        "order_id": "O000001",
        "region": "US",
        "postal_code": "US-1234",
    }
    return fake.create_shipment(**(args | overrides))


@pytest.mark.req("FOUND-3")
def test_a_second_key_cannot_ship_an_order_twice() -> None:
    fake = CarrierFake()
    assert isinstance(_ship(fake, idempotency_key="k1"), Ok)
    assert _code(_ship(fake, idempotency_key="k2")) == "already_shipped"
    assert len(fake.effects) == 1


@pytest.mark.req("FOUND-3")
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"region": "ZZ"}, "invalid_region"),
        ({"region": ["US"]}, "invalid_region"),
        ({"postal_code": "90210"}, "invalid_postal_code"),
        ({"service": "teleport"}, "invalid_service"),
        ({"service": {"standard": 1}}, "invalid_service"),
    ],
)
def test_the_carrier_names_a_bad_destination(overrides: dict[str, Any], code: str) -> None:
    fake = CarrierFake()
    assert _code(_ship(fake, **overrides)) == code
    assert fake.effects == ()


@pytest.mark.req("FOUND-3")
def test_a_malformed_tracking_number_is_not_reported_as_an_unknown_one() -> None:
    fake = CarrierFake()
    assert _code(fake.track("nope")) == "invalid_tracking_number"
    assert _code(fake.track("NLP-AAAAAAAA")) == "unknown_tracking_number"


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _catalog_dir(tmp_path: Path) -> Path:
    """One good variant, and four that each fail exactly one filter."""
    products = [
        {"product_id": "P1", "visibility": "public", "status": "active"},
        {"product_id": "P2", "visibility": "internal", "status": "active"},
        {"product_id": "P3", "visibility": "public", "status": "unreleased"},
    ]
    variants = [
        {"variant_id": "good", "product_id": "P1", "visibility": "public"},
        {"variant_id": "internal-variant", "product_id": "P1", "visibility": "internal"},
        {"variant_id": "internal-product", "product_id": "P2", "visibility": "public"},
        {"variant_id": "unreleased-product", "product_id": "P3", "visibility": "public"},
        {"variant_id": "unpriced", "product_id": "P1", "visibility": "public"},
    ]
    sellable = ["good", "internal-variant", "internal-product", "unreleased-product"]
    prices = [{"variant_id": v, "region": "US"} for v in sellable]
    stock = [{"variant_id": v["variant_id"], "region": "US", "on_hand": 10} for v in variants]
    _write_jsonl(tmp_path / "products.jsonl", products)
    _write_jsonl(tmp_path / "variants.jsonl", variants)
    _write_jsonl(tmp_path / "prices.jsonl", prices)
    _write_jsonl(tmp_path / "stock.jsonl", stock)
    manifest = {"base_date": "2026-10-01", "dataset_sha256": "x"}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


@pytest.mark.req("FOUND-3")
def test_only_public_active_priced_variants_are_sellable(tmp_path: Path) -> None:
    catalog = load_catalog(_catalog_dir(tmp_path))
    assert catalog.in_stock == {"US": [("good", "P1")]}


@pytest.mark.req("FOUND-3")
def test_a_stock_out_means_none_across_every_node(tmp_path: Path) -> None:
    fixtures = _catalog_dir(tmp_path)
    _write_jsonl(
        fixtures / "stock.jsonl",
        [
            {"variant_id": "good", "region": "US", "on_hand": 0},
            {"variant_id": "good", "region": "US", "on_hand": 7},
        ],
    )
    catalog = load_catalog(fixtures)
    assert catalog.out_of_stock == {}
    assert catalog.in_stock == {"US": [("good", "P1")]}
