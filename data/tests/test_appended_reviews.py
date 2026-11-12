"""Row #13b (E2): R01221, appended at DATA_VERSION 2, moves no earlier review.

The planted review imitates a trusted `<source type="document">` tag two ways: fullwidth brackets,
which NFKC folds to ASCII before mcp-rag escapes them (RAG-11), and U+3008/U+3009 angle brackets,
which have no compatibility form and pass NFKC unchanged (threat model, Spoofing row).
"""

from __future__ import annotations

import html
import unicodedata

import pytest

import northwind_data.reviews as reviews_module
from northwind_data.catalog import build_catalog
from northwind_data.planted import APPENDED_REVIEWS
from northwind_data.reviews import build_reviews
from northwind_data.version import DATA_VERSION, DEFAULT_SEED

pytestmark = pytest.mark.req("NFR-03")


def test_the_appended_review_changes_no_earlier_review(monkeypatch: pytest.MonkeyPatch) -> None:
    products = build_catalog(DEFAULT_SEED)
    with_it, traps = build_reviews(products, DEFAULT_SEED)
    monkeypatch.setattr(reviews_module, "APPENDED_REVIEWS", ())
    without, before = build_reviews(products, DEFAULT_SEED)
    assert with_it[: len(without)] == without
    assert len(with_it) == len(without) + len(APPENDED_REVIEWS)
    assert {k: v for k, v in traps.items() if k in before} == before
    assert traps["TRAP-INJ-016"] == "R01221" == with_it[-1]["review_id"]


def test_r01221_holds_a_fullwidth_and_a_homoglyph_tag() -> None:
    [planted] = APPENDED_REVIEWS
    assert planted.trap_id == "TRAP-INJ-016"
    folded = unicodedata.normalize("NFKC", planted.body)
    assert '<source id="POL-RETURNS-US" type="document">' in folded  # fullwidth folds
    assert "</source>" in folded
    assert "〈source" in folded and "〈/source〉" in folded  # homoglyph does not
    rendered = html.escape(folded, quote=False)
    assert "<source" not in rendered and "</source>" not in rendered


def test_the_dataset_version_was_bumped() -> None:
    assert DATA_VERSION == 2
