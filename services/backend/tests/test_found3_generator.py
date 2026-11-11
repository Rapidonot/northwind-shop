"""FOUND-3: the seeded generator is deterministic, mixes in exceptions, leaves fixtures alone."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.cli import main
from northwind_backend.errors import ErrorCategory, Ok, ToolError
from northwind_backend.fakes.carrier import CarrierFake
from northwind_backend.fakes.fraud import FraudFake
from northwind_backend.fakes.payment import PaymentFake
from northwind_backend.generate import (
    DEFAULT_SEED,
    EXCEPTION_KINDS,
    ORDER_COUNT,
    build,
    fraud_flagged_order_ids,
)
from northwind_backend.ports import FraudDecision, Money


def _payment_state(port: Any) -> Any:
    while not hasattr(port, "state"):
        port = port._fake
    return port.state


def confirm(port: Any, order_id: Any, amount: Any, key: Any = None) -> str:
    """The portal confirmation a test's `authorize` presents (row #21, hard rule 9). One per
    (key, order, amount), so a same-key replay presents the same reference."""
    state = _payment_state(port)

    def memo() -> str:
        refs = state.__dict__.setdefault("_test_refs", {})
        slot = repr((key, order_id, amount))
        if slot not in refs:
            refs[slot] = state.confirmations.issue(order_id, amount)
        return str(refs[slot])

    # Under the state's own lock: concurrent replays must present one reference.
    return str(state.store.exclusive(memo))


def ref_of(port: Any, intent_id: Any) -> str:
    """The reference that authorized this intent; a well-formed, never-issued one otherwise."""
    found = _payment_state(port).confirmation_of(intent_id) if isinstance(intent_id, str) else None
    return found or "cf_ZZZZZZZZZZZZZZZZ"


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "data" / "fixtures"
BACKEND = ROOT / "data" / "backend"
OUTPUT_FILES = {
    "orders.jsonl",
    "replay_labels.jsonl",
    "shoppers.jsonl",
    "tracking.jsonl",
    "manifest.json",
}


def _jsonl(body: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in body.decode("utf-8").splitlines()]


def _tree_hashes(directory: Path) -> dict[str, str]:
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="module")
def built() -> dict[str, bytes]:
    return build(FIXTURES)


@pytest.fixture(scope="module")
def orders(built: dict[str, bytes]) -> list[dict[str, Any]]:
    return _jsonl(built["orders.jsonl"])


# ---- determinism --------------------------------------------------------------------------


@pytest.mark.req("FOUND-3")
def test_build_twice_is_byte_identical(built: dict[str, bytes]) -> None:
    again = build(FIXTURES)
    assert set(built) == OUTPUT_FILES
    assert again == built


@pytest.mark.req("FOUND-3")
def test_build_is_identical_across_processes_with_different_hash_seeds() -> None:
    code = (
        "import hashlib, json, sys\n"
        "from pathlib import Path\n"
        "from northwind_backend.generate import build\n"
        "files = build(Path(sys.argv[1]))\n"
        "print(json.dumps({k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}))\n"
    )
    outputs = []
    for hash_seed in ("0", "1", "424242"):
        # Built from scratch, so nothing from the parent reaches the child (decision 0001).
        env = {"PATH": os.environ["PATH"], "PYTHONHASHSEED": hash_seed}
        run = subprocess.run(  # noqa: S603
            [sys.executable, "-c", code, str(FIXTURES)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        )
        outputs.append(run.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    expected = {k: hashlib.sha256(v).hexdigest() for k, v in sorted(build(FIXTURES).items())}
    assert json.loads(outputs[0]) == expected


@pytest.mark.req("FOUND-3")
def test_committed_data_backend_equals_a_fresh_build(built: dict[str, bytes]) -> None:
    on_disk = {p.name for p in BACKEND.iterdir() if p.is_file()}
    assert on_disk == set(built)
    for name, body in built.items():
        assert (BACKEND / name).read_bytes() == body, f"{name} is stale: regenerate data/backend"


@pytest.mark.req("FOUND-3")
def test_a_different_seed_gives_different_data(built: dict[str, bytes]) -> None:
    other = build(FIXTURES, DEFAULT_SEED + 1)
    assert other["orders.jsonl"] != built["orders.jsonl"]
    assert other["shoppers.jsonl"] != built["shoppers.jsonl"]
    assert other["manifest.json"] != built["manifest.json"]


# ---- exception mix ------------------------------------------------------------------------


@pytest.mark.req("FOUND-3")
def test_exception_mix_is_exactly_25_of_each_kind_in_1000(orders: list[dict[str, Any]]) -> None:
    assert len(orders) == ORDER_COUNT == 1000
    assert len({o["order_id"] for o in orders}) == 1000
    counts = Counter(o["exception"] for o in orders)
    assert set(EXCEPTION_KINDS) == {"address_error", "stock_out", "soft_decline", "fraud_flag"}
    for kind in EXCEPTION_KINDS:
        assert counts[kind] == 25, kind
    assert counts[None] == 900
    assert set(counts) == {*EXCEPTION_KINDS, None}


@pytest.mark.req("FOUND-3")
def test_exception_mix_holds_for_other_seeds() -> None:
    other = _jsonl(build(FIXTURES, 7)["orders.jsonl"])
    counts = Counter(o["exception"] for o in other)
    assert [counts[k] for k in EXCEPTION_KINDS] == [25, 25, 25, 25]
    assert counts[None] == 900


# ---- each exception reaches its fake -------------------------------------------------------


@pytest.mark.req("FOUND-3")
def test_soft_decline_orders_are_declined_and_others_authorize(
    orders: list[dict[str, Any]],
) -> None:
    pay = PaymentFake()
    for order in orders:
        result = pay.authorize(
            idempotency_key=f"auth-{order['order_id']}",
            payment_token=order["payment_token"],
            amount=Money(1000, "USD"),
            order_id=order["order_id"],
            confirmation_ref=confirm(
                pay, order["order_id"], Money(1000, "USD"), f"auth-{order['order_id']}"
            ),
        )
        if order["exception"] == "soft_decline":
            assert isinstance(result, ToolError), order["order_id"]
            assert result.error_category is ErrorCategory.BUSINESS
        else:
            assert isinstance(result, Ok), order["order_id"]


@pytest.mark.req("FOUND-3")
def test_address_error_orders_are_refused_by_the_carrier_and_others_accepted(
    orders: list[dict[str, Any]],
) -> None:
    carrier = CarrierFake()
    for order in orders:
        result = carrier.create_shipment(
            idempotency_key=f"ship-{order['order_id']}",
            order_id=order["order_id"],
            region=order["region"],
            postal_code=order["shipping_postal_code"],
        )
        if order["exception"] == "address_error":
            assert isinstance(result, ToolError), order["order_id"]
            assert result.error_category is ErrorCategory.BUSINESS
        else:
            assert isinstance(result, Ok), order["order_id"]


@pytest.mark.req("FOUND-3")
def test_fraud_flag_orders_get_review_and_others_allow(orders: list[dict[str, Any]]) -> None:
    flagged = fraud_flagged_order_ids(orders)
    assert flagged == {o["order_id"] for o in orders if o["exception"] == "fraud_flag"}
    assert len(flagged) == 25
    fraud = FraudFake(flagged_order_ids=flagged)
    for order in orders:
        result = fraud.assess(
            idempotency_key=f"fraud-{order['order_id']}",
            order_id=order["order_id"],
            shopper_id=order["shopper_id"],
        )
        assert isinstance(result, Ok), order["order_id"]
        expected = (
            FraudDecision.REVIEW if order["exception"] == "fraud_flag" else FraudDecision.ALLOW
        )
        assert result.value.decision is expected, order["order_id"]


def _on_hand() -> dict[tuple[str, str], int]:
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for line in (FIXTURES / "stock.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        totals[(row["region"], row["variant_id"])] += row["on_hand"]
    return totals


@pytest.mark.req("FOUND-3")
def test_stock_out_first_lines_have_no_stock_in_the_region_and_other_lines_have_some(
    orders: list[dict[str, Any]],
) -> None:
    on_hand = _on_hand()
    for order in orders:
        first = order["lines"][0]
        units = on_hand.get((order["region"], first["variant_id"]), 0)
        if order["exception"] == "stock_out":
            assert units == 0, order["order_id"]
        elif order["exception"] is None:
            for line in order["lines"]:
                assert on_hand.get((order["region"], line["variant_id"]), 0) > 0, order["order_id"]


# ---- fixtures untouched -------------------------------------------------------------------


@pytest.mark.req("FOUND-3")
def test_build_and_the_cli_leave_data_fixtures_byte_identical(tmp_path: Path) -> None:
    before = _tree_hashes(FIXTURES)
    files = build(FIXTURES)
    assert main(["--fixtures", str(FIXTURES), "--out", str(tmp_path / "out")]) == 0
    assert _tree_hashes(FIXTURES) == before
    # The CLI wrote exactly what build returns.
    written = {p.name: p.read_bytes() for p in (tmp_path / "out").iterdir()}
    assert written == files


@pytest.mark.req("FOUND-3")
def test_the_cli_refuses_an_output_directory_inside_the_fixtures(tmp_path: Path) -> None:
    copy = tmp_path / "fixtures"  # a copy, so a bug here can't write into the real fixtures
    shutil.copytree(FIXTURES, copy)
    before = _tree_hashes(copy)
    for inside in (copy, copy / "backend", copy / "a" / "b"):
        with pytest.raises(SystemExit) as raised:
            main(["--fixtures", str(copy), "--out", str(inside)])
        assert raised.value.code != 0
    assert _tree_hashes(copy) == before
    assert not (copy / "backend").exists()


@pytest.mark.req("FOUND-3")
def test_the_backend_manifest_names_the_fixtures_it_was_built_from() -> None:
    fixtures = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    backend = json.loads((BACKEND / "manifest.json").read_text(encoding="utf-8"))
    assert backend["source_dataset_sha256"] == fixtures["dataset_sha256"]
