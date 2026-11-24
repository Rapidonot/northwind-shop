"""Edge cases for source cards (row #17, SA-02; hard rules 2 and 6). Gaps beside
test_sa02_source_cards.py: odd source text, unknown types, and the prompt's cart rules."""

from __future__ import annotations

import dataclasses

import pytest

from northwind_assistant.output_check import Source
from northwind_assistant.prompt import system_prompt
from northwind_assistant.source_cards import SourceCard, source_cards

pytestmark = pytest.mark.req("SA-02")


def test_a_title_is_the_first_line_only_so_later_lines_never_reach_a_card() -> None:
    src = Source(
        "P0001",
        "product",
        "Tidewick Jacket\n\nBrand: Tidewick. Department: Men.\n\nNow $49.99, 3 left.",
    )
    [card] = source_cards(("P0001",), [src])
    blob = repr(dataclasses.asdict(card))
    assert card.title == "Tidewick Jacket"
    assert "49.99" not in blob and "left" not in blob


def test_control_characters_in_a_title_become_spaces() -> None:
    src = Source("FAQ-001", "document", "Returns\x00\x07\x1b[31m policy\r\nbody")
    [card] = source_cards(("FAQ-001",), [src])
    assert card.title is not None
    assert not any(ord(c) < 32 or ord(c) == 127 for c in card.title)


def test_html_in_a_trusted_title_is_kept_as_inert_text_not_dropped_or_executed() -> None:
    src = Source("FAQ-002", "document", "<img src=x onerror=alert(1)>Returns")
    [card] = source_cards(("FAQ-002",), [src])
    assert card.title == "<img src=x onerror=alert(1)>Returns"  # the portal escapes it


def test_an_empty_first_line_gives_no_title() -> None:
    [card] = source_cards(("FAQ-003",), [Source("FAQ-003", "document", "\nbody only")])
    assert card.title is None


def test_an_unknown_source_type_is_untrusted_and_carries_no_text() -> None:
    src = Source("X-1", "mystery", "Planted title\n\nBrand: Evil. Department: Bad.")
    [card] = source_cards(("X-1",), [src])
    assert card == SourceCard("X-1", "mystery")


def test_a_review_with_a_brand_line_still_carries_no_brand() -> None:
    src = Source("R00001", "review", "Title\n\nBrand: Evil. Department: Bad.")
    [card] = source_cards(("R00001",), [src])
    assert (card.title, card.brand, card.department) == (None, None, None)


def test_only_a_product_gets_brand_and_department() -> None:
    text = "Name\n\nBrand: Acme. Department: Living."
    cards = source_cards(("V", "D"), [Source("V", "variant", text), Source("D", "document", text)])
    assert all(c.brand is None and c.department is None for c in cards)


def test_a_product_without_a_brand_line_has_a_title_and_no_brand() -> None:
    [card] = source_cards(("P1",), [Source("P1", "product", "Just a name\n\nBody.")])
    assert (card.title, card.brand, card.department) == ("Just a name", None, None)


def test_overlong_brand_and_department_are_cut() -> None:
    text = f"Name\n\nBrand: {'B' * 500}. Department: {'D' * 500}."
    [card] = source_cards(("P1",), [Source("P1", "product", text)])
    assert card.brand is not None and len(card.brand) <= 120
    assert card.department is not None and len(card.department) <= 120


def test_no_sources_and_no_citations_give_no_cards() -> None:
    assert source_cards((), [Source("P1", "product", "Name")]) == ()
    assert source_cards(("P1",), []) == ()


def test_a_cited_id_that_looks_like_a_path_gets_a_card_only_if_it_has_source_text() -> None:
    assert source_cards(("../x",), [Source("P1", "product", "Name")]) == ()


@pytest.mark.req("CHK-01")
def test_the_prompt_keeps_the_cart_rules_after_the_d8_change() -> None:
    prompt = system_prompt("US")
    assert "offer only the alternatives that result lists" in prompt
    assert "each by its product name and variant ID" in prompt
    assert "without describing them further or saying whether they are in stock" in prompt
    assert "If the shopper asks to place an order or check out" in prompt
    assert "Never state the cart's line totals or total" in prompt
