"""Row #30 security review, L-1: reading ";" in the check's citation mask let a false return term
through that the base blocked. Failing first (docs/reviews/2026-11-25-30-security-review.raw.md)."""

from __future__ import annotations

import pytest

from northwind_assistant.output_check import FIXED_SENTENCES, OutputCheck, check_text, hits

POLICY = FIXED_SENTENCES["false_policy"]


@pytest.mark.req("NFR-03")
@pytest.mark.req("RAG-08")
@pytest.mark.parametrize(
    "text",
    [
        "You have [x-365​; y-1] days to return it.",
        "You have [ｘ-365; y-1] days to return it.",
        "You have [х-365; y-1] days to return it.",
    ],
)
def test_a_group_that_is_a_citation_only_in_the_view_does_not_hide_a_duration(text: str) -> None:
    assert hits(text) == {"false_policy": 1}
    for cut in range(len(text) + 1):
        check = OutputCheck(set(), [])
        check.feed(text[:cut])
        check.feed(text[cut:])
        check.finish()
        assert check.shown == POLICY, cut


@pytest.mark.req("RAG-08")
def test_a_real_semicolon_group_near_a_policy_word_still_passes() -> None:
    text = "Returns follow the policy [FAQ-019; POL-RETURNS-US] for your region."
    assert check_text(text, (), {"FAQ-019", "POL-RETURNS-US"}) == text
