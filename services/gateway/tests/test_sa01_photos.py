"""SA-01 PR 1, security review F5 (hard rule 7): the committed product photos are served by URL
whatever the product's visibility, so none may be named after a product a shopper cannot see.
Also: each photo is a WebP named after a catalog product, in both sizes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_gateway.shop import DEFAULT_PHOTO_DIR, photo_ids

pytestmark = [pytest.mark.req("SA-01"), pytest.mark.req("RAG-11")]

REPO = Path(__file__).resolve().parents[3]
PHOTOS = REPO / "apps/portal/public/products"
PRODUCTS = {
    record["product_id"]: record["visibility"]
    for line in (REPO / "data/fixtures/products.jsonl").read_text().splitlines()
    if line and (record := json.loads(line))
}


@pytest.mark.parametrize("size", ["800", "1600"])
def test_every_photo_is_a_public_catalog_product(size: str) -> None:
    files = sorted((PHOTOS / size).iterdir())
    assert files, "no photos committed"
    for path in files:
        assert path.is_file() and path.suffix == ".webp", path.name
        assert path.read_bytes()[8:12] == b"WEBP", path.name
        assert PRODUCTS.get(path.stem) == "public", path.name


def test_both_sizes_hold_the_same_products_and_the_gateway_reads_the_tile_size() -> None:
    tiles = {p.stem for p in (PHOTOS / "800").iterdir()}
    heroes = {p.stem for p in (PHOTOS / "1600").iterdir()}
    assert tiles == heroes
    assert DEFAULT_PHOTO_DIR == PHOTOS / "800"
    assert photo_ids() == tiles
