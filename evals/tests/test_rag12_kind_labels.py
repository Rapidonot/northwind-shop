"""Row 32b (RAG-12, kind gating for RAG-08): the user's 44 kind labels for #32's agreement check.

The labels were given by the user on 2026-11-23 without seeing the judge's kinds, and committed
before the key was read. This test checks only that they were transcribed as given (shape,
per-row values, totals). Agreement figures are not pinned here (decision 0021).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LABELS = REPO / "docs/reviews/2026-11-18-32-kind-labels.md"
KINDS = {"product", "policy", "site", "other"}

# The user's labels by row range, as given (inclusive).
GIVEN: list[tuple[int, int, str]] = [
    (1, 3, "product"),
    (4, 4, "policy"),
    (5, 7, "product"),
    (8, 8, "policy"),
    (9, 33, "product"),
    (34, 36, "site"),
    (37, 38, "product"),
    (39, 39, "policy"),
    (40, 44, "product"),
]


def _expected() -> dict[int, str]:
    return {n: kind for lo, hi, kind in GIVEN for n in range(lo, hi + 1)}


def _label_rows() -> dict[int, dict[str, str]]:
    """Rows of the label table, keyed by #, each a header -> cell mapping."""
    lines = LABELS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| # | Item |"))
    header = [c.strip() for c in lines[start].strip().strip("|").split("|")]
    rows: dict[int, dict[str, str]] = {}
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == len(header), f"{len(cells)} cells, header {len(header)}: {line}"
        row = dict(zip(header, cells, strict=True))
        rows[int(row["#"])] = row
    return rows


@pytest.mark.req("RAG-12")
def test_label_table_has_44_rows_numbered_1_to_44() -> None:
    assert sorted(_label_rows()) == list(range(1, 45))


@pytest.mark.req("RAG-12")
def test_every_label_is_a_known_kind() -> None:
    bad = {n: r["your kind"] for n, r in _label_rows().items() if r["your kind"] not in KINDS}
    assert bad == {}


@pytest.mark.req("RAG-12")
def test_each_row_carries_the_users_given_label() -> None:
    got = {n: r["your kind"] for n, r in _label_rows().items()}
    assert got == _expected()


@pytest.mark.req("RAG-12")
def test_label_totals_are_38_product_3_policy_3_site() -> None:
    totals = Counter(r["your kind"] for r in _label_rows().values())
    assert totals == Counter({"product": 38, "policy": 3, "site": 3})
    assert Counter(_expected().values()) == totals
