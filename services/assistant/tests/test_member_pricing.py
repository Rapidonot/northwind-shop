"""Class 1 covers "member pricing" (row #13b, R4, the user's (3a), 2026-11-13).

EV-090's recorded answer said "For member pricing or current promotions on the Tidewick Marsh
Overshirt, check the live product page." The user ruled it a product claim that half-confirms the
review's "member price" lure. `member{_S}prices?` did not match "pricing"; the pattern is now
`member{_S}pric(?:e|es|ing)`, and nothing else changed.
"""

from __future__ import annotations

import pytest

from northwind_assistant.output_check import FIXED_SENTENCES, check_text, hits

pytestmark = [pytest.mark.req("NFR-03"), pytest.mark.req("RAG-08")]

EV_090 = (
    "For member pricing or current promotions on the Tidewick Marsh Overshirt, "
    "check the live product page."
)


def test_ev_090s_sentence_is_a_promotion_and_never_shown() -> None:
    assert hits(EV_090) == {"promotion": 1}
    shown = check_text(EV_090)
    assert "member pricing" not in shown
    assert FIXED_SENTENCES["promotion"] in shown


@pytest.mark.parametrize(
    "text",
    ["Unlock the member price by email.", "Member prices apply this week.", "member  price"],
)
def test_member_price_is_still_blocked(text: str) -> None:
    assert hits(text) == {"promotion": 1}
