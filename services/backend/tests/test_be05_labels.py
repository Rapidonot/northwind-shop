"""The backend replay set's labels (row #21; PRD §10: expected outcome and escalation flag)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.generate import build

pytestmark = pytest.mark.req("BE-05")

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "data" / "backend"


def _rows(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (BACKEND / name).read_text("utf-8").splitlines()]


def test_every_order_has_one_label_by_rule() -> None:
    orders, labels = _rows("orders.jsonl"), _rows("replay_labels.jsonl")
    assert [o["order_id"] for o in orders] == [label["order_id"] for label in labels]
    assert {label["labelled_by"] for label in labels} == {"rule"}
    for order, label in zip(orders, labels, strict=True):
        assert label["exception"] == order["exception"]
        assert label["escalation"] is (label["expected_outcome"] == "escalated")


def test_the_labels_follow_the_exception_kind() -> None:
    expected = {
        None: ("placed", None, None),
        "address_error": ("placed", None, "address_on_file"),
        "soft_decline": ("placed", None, "soft_decline_retry"),
        "stock_out": ("escalated", "out_of_stock", None),
        "fraud_flag": ("escalated", "fraud_review", None),
    }
    for label in _rows("replay_labels.jsonl"):
        got = (label["expected_outcome"], label["expected_reason"], label["resolution"])
        assert got == expected[label["exception"]], label["order_id"]


def test_950_of_1000_are_expected_to_complete_with_no_human_touch() -> None:
    counts = Counter(label["expected_outcome"] for label in _rows("replay_labels.jsonl"))
    assert counts == {"placed": 950, "escalated": 50}
    assert counts["placed"] / 1000 >= 0.90  # the BE-05 target holds on the labels themselves


def test_the_labels_are_generated_not_hand_written() -> None:
    built = build(ROOT / "data" / "fixtures")
    assert (BACKEND / "replay_labels.jsonl").read_bytes() == built["replay_labels.jsonl"]
    manifest = json.loads(built["manifest.json"])
    assert manifest["backend_data_version"] == 2
    assert manifest["counts"]["replay_labels"] == 1000
