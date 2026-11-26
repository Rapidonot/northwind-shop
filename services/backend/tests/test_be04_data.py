"""Row #25 (BE-04), option B: fitting the replay orders to stock changed exactly two orders.

The user's condition: "pin that every other order is byte-identical". The generator is built twice
at this head, with `_fit_to_stock` and with it replaced by the identity (which is version 2's
output), and the two are compared line by line.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from northwind_backend import generate

pytestmark = pytest.mark.req("BE-04")

FIXTURES = Path(__file__).resolve().parents[3] / "data" / "fixtures"
BACKEND = Path(__file__).resolve().parents[3] / "data" / "backend"


def _unfitted(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    monkeypatch.setattr(generate, "_fit_to_stock", lambda orders, *_: orders)
    return generate.build(FIXTURES)


def _orders(body: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in body.decode().splitlines() if line.strip()]


def test_only_o000476_and_o000936_differ_from_the_unfitted_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fitted = generate.build(FIXTURES)
    unfitted = _unfitted(monkeypatch)
    new = fitted["orders.jsonl"].decode().splitlines()
    old = unfitted["orders.jsonl"].decode().splitlines()
    assert len(new) == len(old) == 1000
    changed = [json.loads(n)["order_id"] for n, o in zip(new, old, strict=True) if n != o]
    assert changed == ["O000476", "O000936"]
    for name in ("tracking.jsonl", "replay_labels.jsonl", "shoppers.jsonl"):
        assert fitted[name] == unfitted[name], name


def test_the_two_changes_are_exactly_the_planned_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    fitted = {o["order_id"]: o for o in _orders(generate.build(FIXTURES)["orders.jsonl"])}
    unfitted = {o["order_id"]: o for o in _orders(_unfitted(monkeypatch)["orders.jsonl"])}
    a_new, a_old = fitted["O000476"], unfitted["O000476"]
    assert [(ln["variant_id"], ln["quantity"]) for ln in a_old["lines"]][1] == (
        "P0044-COB-REG-XS",
        3,
    )
    assert [(ln["variant_id"], ln["quantity"]) for ln in a_new["lines"]][1] == (
        "P0044-COB-REG-XS",
        2,
    )
    b_new, b_old = fitted["O000936"], unfitted["O000936"]
    assert b_old["lines"][0]["variant_id"] == "P0047-SND-REG-L"
    assert b_new["lines"][0]["variant_id"] == "P0001-BLK-REG-L"
    for new, old in ((a_new, a_old), (b_new, b_old)):
        assert len(new["lines"]) == len(old["lines"])
        assert {k: v for k, v in new.items() if k != "lines"} == {
            k: v for k, v in old.items() if k != "lines"
        }


def test_the_committed_orders_are_the_fitted_build() -> None:
    assert (BACKEND / "orders.jsonl").read_bytes() == generate.build(FIXTURES)["orders.jsonl"]
