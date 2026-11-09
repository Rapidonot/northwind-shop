"""The structured tool error (hard rule 10; CC-10)."""

from __future__ import annotations

import pytest

from northwind_common.errors import CATEGORIES, ENVELOPE_KEYS, ToolError

pytestmark = pytest.mark.req("CC-10")


@pytest.mark.parametrize("category", sorted(CATEGORIES))
def test_every_category_gives_the_four_keys_and_a_code(category: str) -> None:
    error = ToolError(category, category == "transient", "search_failed", "Fixed text.")  # type: ignore[arg-type]
    envelope = error.to_envelope()
    assert set(envelope) == ENVELOPE_KEYS
    assert envelope == {
        "isError": True,
        "errorCategory": category,
        "isRetryable": category == "transient",
        "message": "Fixed text.",
        "code": "search_failed",
    }


def test_the_categories_are_exactly_hard_rule_tens() -> None:
    assert CATEGORIES == {"transient", "validation", "business", "permission"}


@pytest.mark.parametrize(
    ("category", "retryable", "code", "message"),
    [
        ("fatal", False, "x", "m"),
        ("transient", "yes", "x", "m"),
        ("transient", True, "", "m"),
        ("transient", True, "Search-Failed", "m"),
        ("transient", True, "search failed", "m"),
        ("transient", True, "x", "  "),
    ],
)
def test_a_malformed_error_is_refused(
    category: str, retryable: object, code: str, message: str
) -> None:
    with pytest.raises((ValueError, TypeError)):
        ToolError(category, retryable, code, message)  # type: ignore[arg-type]


def test_an_error_is_an_exception_a_handler_can_raise() -> None:
    with pytest.raises(ToolError) as caught:
        raise ToolError("permission", False, "not_allowed", "Not allowed.")
    assert caught.value.to_envelope()["errorCategory"] == "permission"
