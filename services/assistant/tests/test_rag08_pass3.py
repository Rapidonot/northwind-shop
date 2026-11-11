"""Row #13, security review pass 3 (docs/reviews/2026-11-11-RAG-08-security-review.md): a word
split by a cut marker (P3-1), cost on invisible runs, held durations and many markers (P3-2), the
answer-side context measured in view characters and failing closed (P3-3 a, b), a chunk-shaped
document (P3-5; since the user's P3-4 decision any public document with the duration vouches), and
the dropping state after a cut (P3-7)."""

from __future__ import annotations

import time

import pytest

from northwind_assistant.output_check import FIXED_SENTENCES, OutputCheck, Source, check_text

pytestmark = pytest.mark.req("NFR-03")

LINK = FIXED_SENTENCES["link"]
POLICY = FIXED_SENTENCES["false_policy"]


def stream(text: str, size: int, sources: list[Source] | None = None) -> tuple[str, str]:
    check = OutputCheck(set(), sources or [])
    sent: list[str] = []
    for i in range(0, len(text), size):
        sent += [p for p in check.feed(text[i : i + size]) if p is not None]
    sent += [p for p in check.finish() if p is not None]
    return "".join(sent), check.shown


def two_deltas(first: str, second: str) -> tuple[str, str]:
    check = OutputCheck(set(), [])
    sent = [p for p in check.feed(first) if p is not None]
    sent += [p for p in check.feed(second) if p is not None]
    sent += [p for p in check.finish() if p is not None]
    return "".join(sent), check.shown


@pytest.mark.parametrize("size", [1, 3, 4])
@pytest.mark.parametrize(
    "text",
    [
        "Visit northwind-deals[ X1].net now.",
        "Write to deals@northwind[ X1].net now.",
        "Open 10.0.0[ X1].5/claim now.",
    ],
)
def test_p3_1_a_word_split_by_a_cut_marker_is_checked_whole(text: str, size: int) -> None:
    sent, shown = stream(text, size)
    joined = sent.replace(" X1", "")
    assert "northwind-deals.net" not in joined and "northwind.net" not in joined
    assert "10.0.0.5" not in joined and LINK in shown


def test_p3_1_the_two_delta_split_from_the_review() -> None:
    sent, shown = two_deltas("Visit northwind-deals[ X", "1].net now.")
    assert "northwind-deals.net" not in sent and LINK in shown


def _timed(text: str, size: int, sources: list[Source] | None = None) -> float:
    check = OutputCheck(set(), sources or [])
    began = time.perf_counter()
    for i in range(0, len(text), size):
        check.feed(text[i : i + size])
    check.finish()
    return time.perf_counter() - began


def test_p3_2a_a_long_invisible_run_is_checked_in_linear_time() -> None:
    assert _timed("Hello " + "​" * 30000 + " there.", 4) < 5.0


def test_p3_2b_held_durations_do_not_rescan_the_answer() -> None:
    assert _timed("It dries in 2 days and stays soft" * 600, 3) < 5.0


def test_p3_2c_many_markers_in_a_held_region_cost_linear_time() -> None:
    assert _timed("x" + "[Q1]" * 3000 + " done.", 4) < 5.0


def test_p3_3a_a_long_list_keeps_its_parent_or_fails_closed() -> None:
    text = (
        "Returns:\n" + "- Soft and warm fleece that holds its shape well.\n" * 6 + "- a full year\n"
    )
    assert POLICY in check_text(text)


def test_p3_3a_a_parent_beyond_the_window_fails_closed() -> None:
    """25 siblings put the parent "Returns:" more than 16L view characters back: unreadable, so
    the duration is policy with no stems, and nothing vouches for it."""
    text = "Returns:\n" + "- Soft and warm fleece that holds its shape well.\n" * 25 + "- 2 days\n"
    shown = check_text(text)
    assert shown.endswith("- " + POLICY + "\n")


def test_p3_3b_a_padded_heading_is_still_a_heading() -> None:
    text = "**Return window" + "​" * 90 + "**\n365 days."
    assert POLICY in check_text(text)
    text = "Returns" + "​" * 300 + ":\n365 days."
    assert POLICY in check_text(text)


def test_p3_5_a_chunk_shaped_public_document_vouches_like_any_other() -> None:
    chunk = Source(
        "POL-SHIPPING-XX",
        "document",
        "Shipping policy: United States > Delivery times\n\n- **Express:** 1–2 days.",
    )
    answer = "Express delivery takes 1–2 days."
    assert check_text(answer, [chunk]) == answer


def test_p3_7_a_cut_right_after_a_full_stop_does_not_drop_the_next_sentence() -> None:
    check = OutputCheck(set(), [])
    out = check.feed("Get a 50% off deal.[X")
    out += check.feed("1] Soft wool.")
    out += check.finish()
    assert check.shown.endswith("Soft wool.")
