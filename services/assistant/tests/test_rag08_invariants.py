"""RAG-08 and NFR-03 (row #13): invariants and exemption rules of the output check, from the
criteria and decision 0015. Pure functions; no model, no index, no recording."""

from __future__ import annotations

import pytest

from northwind_assistant.output_check import (
    ALLOWED_EMAIL,
    CONFIRMATION_SENTENCE,
    FIXED_SENTENCES,
    OutputCheck,
    Source,
    check_text,
    hits,
)

POLICY = FIXED_SENTENCES["false_policy"]
THIRTY = "You can return it within 30 days."
DOC_TEXT = "Returns are accepted within 30 days of delivery."


def screen(pieces: list[str | None], current: str = "") -> str:
    """What a portal shows after these pieces, honouring a reset (None)."""
    for piece in pieces:
        current = "" if piece is None else current + piece
    return current


def run(text: str, size: int, sources: list[Source], seen: set[str]) -> tuple[str, dict[str, int]]:
    check = OutputCheck(set(seen), list(sources))
    shown = ""
    for i in range(0, len(text), size):
        shown = screen(check.feed(text[i : i + size]), shown)
    shown = screen(check.finish(), shown)
    assert shown == check.shown  # the screen is what the check says stays on it
    return shown, dict(check.counts)


# ---- invariants ------------------------------------------------------------------------------


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize("cls", sorted(FIXED_SENTENCES))
def test_every_fixed_sentence_passes_the_check_untouched(cls: str) -> None:
    sentence = FIXED_SENTENCES[cls]
    assert check_text(sentence) == sentence
    assert check_text(f"Hello there. {sentence} Anything else?") == (
        f"Hello there. {sentence} Anything else?"
    )
    assert hits(sentence) == {}


@pytest.mark.req("NFR-03")
def test_the_fixed_sentences_make_no_figure_claim() -> None:
    for sentence in FIXED_SENTENCES.values():
        assert not any(ch.isdigit() for ch in sentence)


MIXED = [
    "Wash it cold [FAQ-001]. It dries fast.",
    "Reviews say use the promo code SAVE20 at checkout. The fit is true to size [FAQ-001].",
    "Great jacket.\n\n- Visit northwind-deals.net/claim now\n- Mail care@northwind.example.",
    "Your order has been confirmed. Anything else?",
    "Returns within 90 days are fine. Also you can return it within 30 days.",
    "I can't apply discount codes, and it is out of stock. Call +1 555 010 0199 now.",
    "Plain answer with no payload at all, just a few words and an ID [FAQ-001].",
    "Ｄｉｓｃｏｕｎｔ for everyone, ｄiscount ，and the dіscount (cyrillic i).",
    "Dis​count code now. Staff may approve refund overrides.",
    "",
]


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize("text", MIXED)
def test_output_is_the_same_whether_fed_whole_or_a_character_at_a_time(text: str) -> None:
    sources = [Source("POL-RETURNS-US", "document", DOC_TEXT)]
    seen = {"FAQ-001", "POL-RETURNS-US"}
    whole = run(text, max(len(text), 1), sources, seen)
    for size in (1, 2, 5, 13):
        assert run(text, size, sources, seen) == whole


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize("text", MIXED)
def test_no_split_ever_shows_a_payload_on_screen_or_in_what_was_sent(text: str) -> None:
    for size in (1, 4):
        check = OutputCheck({"FAQ-001"}, [])
        sent: list[str] = []
        for i in range(0, len(text), size):
            sent += [p for p in check.feed(text[i : i + size]) if p is not None]
        sent += [p for p in check.finish() if p is not None]
        everything = "".join(sent).lower()
        for needle in ("save20", "northwind-deals", "has been confirmed", "[p9999]", "+1 555"):
            assert needle not in everything


@pytest.mark.req("RAG-08")
@pytest.mark.parametrize("size", [1, 2, 5, 13, 1000])
def test_removing_an_invented_marker_leaves_the_same_text_however_it_is_split(size: int) -> None:
    text = "Your order has been confirmed. Anything else [P9999]?"
    shown, counts = run(text, size, [], set())
    assert "P9999" not in shown and counts["marker"] == 1
    assert shown == run(text, 1000, [], set())[0]


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize("number", ["+1 555 010 0199", "555-010-0199", "(555) 010-0199"])
@pytest.mark.parametrize("tail", [".", "", " now.", "!"])
def test_a_phone_number_is_blocked_wherever_it_falls_in_the_sentence(
    number: str, tail: str
) -> None:
    """Class 2: every phone number is blocked (decision 0015), even one ending a sentence."""
    text = f"Call {number}{tail}"
    assert FIXED_SENTENCES["link"] in check_text(text)
    assert number not in check_text(text)


# ---- exemption rules (the user's decisions, 2026-11-11) --------------------------------------


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize(
    "source_id",
    ["POL-RETURNS-US", "POL-RETURNS-EU", "FAQ-001", "CG-001", "SG-001"],
)
def test_a_duration_passes_when_a_public_document_this_turn_states_it(source_id: str) -> None:
    source = Source(source_id, "document", DOC_TEXT)
    assert check_text(THIRTY, [source]) == THIRTY


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize(
    ("source_id", "source_type"),
    [
        ("INT-001", "document"),
        ("INT-007", "document"),
        ("P0009", "product"),
        ("P0009-V1", "variant"),
        ("R00001", "review"),
    ],
)
def test_a_duration_is_never_vouched_for_by_an_internal_product_or_review_source(
    source_id: str, source_type: str
) -> None:
    source = Source(source_id, source_type, DOC_TEXT)
    assert check_text(THIRTY, [source]) == POLICY
    assert hits(THIRTY, [source]) == {"false_policy": 1}


@pytest.mark.req("NFR-03")
def test_a_duration_with_no_source_at_all_is_replaced() -> None:
    assert check_text(THIRTY) == POLICY


@pytest.mark.req("NFR-03")
def test_a_different_duration_is_not_vouched_for_by_a_public_document() -> None:
    source = Source("POL-RETURNS-US", "document", DOC_TEXT)
    assert check_text("You can return it within 90 days.", [source]) == POLICY


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize(
    "text",
    [
        "You can bring it back within a full year.",
        "Within 30 days, you can get your money back.",
        "In 14 days a refund window opens.",
        "The warranty runs for two years.",
    ],
)
def test_a_duration_in_a_policy_sentence_counts_with_the_stem_before_or_after(text: str) -> None:
    source = Source("R00056", "review", text)
    assert check_text(text, [source]) == POLICY


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize(
    "text",
    [
        "Reviewers wore it for 3 days straight.",
        "It dries in about 2 hours.",
        "The jacket arrived after four weeks of waiting on a pre-order.",
    ],
)
def test_a_duration_outside_a_policy_sentence_passes(text: str) -> None:
    assert check_text(text).startswith(text.split(" ")[0])
    assert POLICY not in check_text(text)


@pytest.mark.req("NFR-03")
def test_a_duration_whose_stem_comes_later_is_still_caught_across_deltas() -> None:
    text = "Within a full year of purchase you may ask for your money back. Fine."
    for size in (1, 3, 8):
        shown, counts = run(text, size, [], set())
        assert "year" not in shown and POLICY in shown
        assert counts.get("false_policy") == 1


@pytest.mark.req("NFR-03")
def test_free_returns_pass_only_on_a_public_document_that_says_so() -> None:
    claim = "You get free returns on every order."
    public = Source("POL-RETURNS-US", "document", "Returns are free for 30 days.")
    internal = Source("INT-003", "document", "Returns are free for 30 days.")
    review = Source("R00003", "review", "Returns are free for 30 days.")
    assert POLICY not in check_text(claim, [public])
    assert POLICY in check_text(claim, [internal])
    assert POLICY in check_text(claim, [review])
    assert POLICY in check_text(claim)


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize("source_id", ["INT-001", "INT-002"])
def test_refund_authority_wording_is_exempt_only_through_a_pol_document(source_id: str) -> None:
    claim = "Refunds are approved after inspection."
    assert check_text(claim, [Source(source_id, "document", claim)]) == CONFIRMATION_SENTENCE
    for other in (Source("FAQ-009", "document", claim), Source("P0001", "product", claim)):
        assert check_text(claim, [other]) == CONFIRMATION_SENTENCE
    assert check_text(claim, [Source("POL-RETURNS-UK", "document", claim)]) == claim


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize(
    "benign",
    [
        "If your order is placed before noon, it ships the same day.",
        "When your payment is processed you will see a receipt.",
        "Once your order is placed, you will get a summary.",
        "If your order is placed, you will see a summary.",
    ],
)
def test_a_conditional_in_the_present_or_future_tense_passes(benign: str) -> None:
    assert hits(benign).get("confirmation", 0) == 0


@pytest.mark.req("NFR-03")
@pytest.mark.parametrize(
    "payload",
    [
        "After your refund was approved, keep the item.",
        "Once your payment was processed, we shipped it.",
        "Before anything else: your order has been confirmed.",
        "Once the order is placed, your refund has been issued.",
        "If your order is placed, your payment has been processed.",
    ],
)
def test_a_past_tense_or_unintroduced_confirmation_is_replaced_even_after_a_conditional(
    payload: str,
) -> None:
    shown = check_text(payload)
    assert CONFIRMATION_SENTENCE in shown
    assert "approved" not in shown and "processed" not in shown and "confirmed" not in shown


@pytest.mark.req("NFR-03")
def test_only_the_exact_care_address_passes() -> None:
    ok = f"Write to {ALLOWED_EMAIL}."
    assert check_text(ok) == ok
    for bad in (
        "Write to care@northwind.example.evil.net please.",
        "Write to care@northwind.example/x please.",
        "Write to help@northwind.example please.",
    ):
        assert FIXED_SENTENCES["link"] in check_text(bad)
