"""RAG-01: NULL means "doesn't apply" in the model; visibility fails closed."""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

import pytest

from northwind_rag.models import Chunk, ChunkError, ChunkMetadata, parse_visibility

pytestmark = pytest.mark.req("RAG-01")

NINE_KEYS = {
    "category", "brand", "size", "colour", "material", "price_band", "region", "updated",
    "visibility",
}  # fmt: skip


def meta(**changes: Any) -> ChunkMetadata:
    base: dict[str, Any] = {
        "category": "apparel", "brand": "Tidewick", "size": ("M",), "colour": ("Black",),
        "material": "wool", "price_band": {"US": "100-150"}, "region": ("US",),
        "updated": date(2026, 9, 1), "visibility": "public",
    }  # fmt: skip
    base.update(changes)
    return ChunkMetadata(**base)


def test_metadata_has_exactly_the_nine_keys() -> None:
    assert {f.name for f in dataclasses.fields(ChunkMetadata)} == NINE_KEYS


def test_none_is_accepted_for_every_optional_key() -> None:
    m = meta(category=None, brand=None, size=None, colour=None, material=None,
             price_band=None, region=None)  # fmt: skip
    assert m.brand is None


@pytest.mark.parametrize("key", ["category", "brand", "material"])
def test_empty_string_is_refused(key: str) -> None:
    with pytest.raises(ChunkError):
        meta(**{key: ""})


@pytest.mark.parametrize("key", ["size", "colour", "region"])
@pytest.mark.parametrize("empty", [(), ("",), ("M", "")])
def test_empty_array_or_blank_member_is_refused(key: str, empty: tuple[str, ...]) -> None:
    with pytest.raises(ChunkError):
        meta(**{key: empty})


def test_empty_object_is_refused_for_price_band() -> None:
    with pytest.raises(ChunkError):
        meta(price_band={})


@pytest.mark.parametrize("value", [None, "", "Public", "secret", 1, "PUBLIC"])
def test_visibility_other_than_public_or_internal_is_refused(value: object) -> None:
    with pytest.raises(ChunkError):
        meta(visibility=value)
    with pytest.raises(ChunkError):
        parse_visibility(value, "x")


def test_empty_chunk_text_is_refused() -> None:
    with pytest.raises(ChunkError):
        Chunk("product", "P1", 0, "   ", meta())
