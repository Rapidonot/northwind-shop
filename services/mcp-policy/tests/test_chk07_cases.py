"""CHK-07: "Eligible, ineligible and ambiguous returns each handled correctly on labelled cases."

The cases were written from the POL documents before the evaluator and are pinned: a case the
evaluator disagrees with is a bug or a question for the user (stop 7), never a label edit. RC-061 is
the one label changed since, by the user (2026-11-25, `docs/reviews/2026-11-25-27b-label-review.md`)."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from northwind_common import returns_policy as rp

pytestmark = pytest.mark.req("CHK-07")

CASES_FILE = Path(__file__).with_name("return_cases.jsonl")
CASES_SHA256 = "37e65b7944d2c348a1a96c5df0206e285759d7d1e2901c08742ed02b2908d1aa"
CASES: list[dict[str, Any]] = [
    json.loads(line) for line in CASES_FILE.read_text().splitlines() if line.strip()
]
REGIONS = {"US", "CA", "UK", "EU"}


def decide(case: dict[str, Any]) -> rp.Decision:
    delivered = None if case["days"] is None else rp.AS_OF - timedelta(days=case["days"])
    return rp.evaluate(
        region=case["region"],
        subcategory=case["subcategory"],
        delivered_on=delivered,
        condition=case["condition"],
        clearance=case.get("clearance", False),
        lines=case.get("lines", 1),
        as_of=rp.AS_OF,
    )


def test_the_labels_are_the_ones_written_before_the_evaluator() -> None:
    assert hashlib.sha256(CASES_FILE.read_bytes()).hexdigest() == CASES_SHA256
    assert len(CASES) == len({c["id"] for c in CASES}) == 68
    assert {c["reviewed_by"] for c in CASES} == {"model"}  # only the user marks one human


def test_every_outcome_is_labelled_in_every_region() -> None:
    seen: dict[str, set[str]] = defaultdict(set)
    for case in CASES:
        seen[case["outcome"]].add(case["region"])
    assert seen == {o: REGIONS for o in ("eligible", "ineligible", "ambiguous")}


def test_every_reason_the_evaluator_can_give_is_labelled() -> None:
    assert {c["reason"] for c in CASES} == set(rp.REASONS)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_the_evaluator_matches_the_label(case: dict[str, Any]) -> None:
    found = decide(case)
    assert (found.outcome, found.reason) == (case["outcome"], case["reason"])
    assert found.explanation and found.next_step
    assert found.facts, "an outcome is explained by at least one policy fact"
    assert {f.document_id for f in found.facts} == {f"POL-RETURNS-{case['region']}"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"region": "FR"}, "region"),
        ({"condition": "eligible"}, "condition"),
        ({"lines": 0}, "lines"),
    ],
)
def test_an_unknown_input_is_refused_not_guessed(change: dict[str, Any], message: str) -> None:
    args: dict[str, Any] = {
        "region": "US",
        "subcategory": "pants",
        "delivered_on": date(2026, 11, 1),
        "condition": "unused",
        "as_of": rp.AS_OF,
    }
    with pytest.raises(ValueError, match=message):
        rp.evaluate(**{**args, **change})


def test_the_as_of_date_is_fixed() -> None:
    assert rp.AS_OF == date(2026, 11, 15)  # D5: never the wall clock
    source = Path(rp.__file__).read_text()
    assert "today(" not in source and "now(" not in source and "import time" not in source


# ---- the facts are the documents' own words -----------------------------------------------------

DOCUMENTS = {
    row["doc_id"]: row
    for row in map(
        json.loads,
        (Path(__file__).resolve().parents[3] / "data/fixtures/documents.jsonl")
        .read_text()
        .splitlines(),
    )
}


@pytest.mark.parametrize("fact", rp.ALL_FACTS, ids=lambda f: f"{f.document_id}:{f.key}")
def test_every_fact_is_a_sentence_of_its_public_document(fact: rp.Fact) -> None:
    document = DOCUMENTS[fact.document_id]
    assert document["visibility"] == "public" and fact.document_id.startswith("POL-RETURNS-")
    assert fact.sentence in " ".join(document["body"].split())


def test_the_numbers_used_are_the_numbers_in_the_sentences() -> None:
    for region, days in rp.WINDOW_DAYS.items():
        [window] = [
            f
            for f in rp.ALL_FACTS
            if f.key == "window" and f.document_id == f"POL-RETURNS-{region}"
        ]
        assert f"**{days} days** of delivery" in window.sentence
    for region in rp.STATUTORY_REGIONS:
        [right] = [
            f
            for f in rp.ALL_FACTS
            if f.key == "statutory" and f.document_id == f"POL-RETURNS-{region}"
        ]
        assert f"{rp.STATUTORY_DAYS}-day right" in right.sentence


def test_a_document_that_lost_a_sentence_is_found() -> None:
    bodies = {d: row["body"] for d, row in DOCUMENTS.items()}
    assert rp.missing_facts(bodies) == []
    changed = bodies | {
        "POL-RETURNS-US": bodies["POL-RETURNS-US"].replace("**30 days**", "**90 days**")
    }
    assert [(f.document_id, f.key) for f in rp.missing_facts(changed)] == [
        ("POL-RETURNS-US", "window")
    ]
    del bodies["POL-RETURNS-UK"]
    assert {f.document_id for f in rp.missing_facts(bodies)} == {"POL-RETURNS-UK"}
