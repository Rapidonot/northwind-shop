"""RAG-01: fixtures to validated chunks (no database)."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pytest

from northwind_data.ingest_guard import NotIngestibleError, assert_ingestible, load_manifest
from northwind_rag.models import Chunk, ChunkError
from northwind_rag.sources import build_chunks, load_records

from .conftest import Edit  # noqa: TID252

pytestmark = pytest.mark.req("RAG-01")

SOURCE_FILES = {
    "product": "products.jsonl",
    "variant": "variants.jsonl",
    "review": "reviews.jsonl",
    "document": "documents.jsonl",
}
ID_KEY = {
    "product": "product_id",
    "variant": "variant_id",
    "review": "review_id",
    "document": "doc_id",
}
SHARED = ("category", "brand", "size", "colour", "material")


@pytest.fixture(scope="module")
def chunks() -> list[Chunk]:
    return build_chunks(Path(__file__).resolve().parents[3] / "data" / "fixtures")


def _read(fixtures: Path, name: str) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (fixtures / name).read_text(encoding="utf-8").splitlines()]


# ---- coverage and counts ----------------------------------------------------------------


def test_every_fixture_record_yields_a_chunk_and_counts_tie_to_manifest(
    chunks: list[Chunk], fixtures_dir: Path
) -> None:
    counts = load_manifest(fixtures_dir)["counts"]
    ids: dict[str, set[str]] = defaultdict(set)
    for c in chunks:
        ids[c.source_type].add(c.source_id)
    assert len(ids["product"]) == counts["products"]
    assert len(ids["variant"]) == counts["variants"]
    assert len(ids["review"]) == counts["reviews"]
    assert len(ids["document"]) == counts["documents"]
    for source_type, name in SOURCE_FILES.items():
        expected = {r[ID_KEY[source_type]] for r in _read(fixtures_dir, name)}
        assert ids[source_type] == expected


def test_chunk_ids_are_unique_and_stable_across_builds(
    chunks: list[Chunk], fixtures_dir: Path
) -> None:
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert [c.chunk_id for c in build_chunks(fixtures_dir)] == [c.chunk_id for c in chunks]


# ---- metadata coverage ------------------------------------------------------------------


def test_all_nine_keys_present_on_every_chunk(chunks: list[Chunk]) -> None:
    keys = {"category", "brand", "size", "colour", "material", "price_band", "region", "updated",
            "visibility"}  # fmt: skip
    for c in chunks:
        assert set(vars(c.metadata)) == keys
        assert c.metadata.updated is not None
        assert c.metadata.visibility in ("public", "internal")


def test_per_source_which_keys_must_be_non_null(chunks: list[Chunk]) -> None:
    for c in chunks:
        m = c.metadata
        if c.source_type == "document":
            for name in ("category", "brand", "size", "colour", "material", "price_band"):
                assert getattr(m, name) is None, (c.chunk_id, name)
            assert m.region is not None
            continue
        for name in SHARED:
            assert getattr(m, name) is not None, (c.chunk_id, name)
        if c.chunk_id.split(":")[1] == "P0009" or c.parent_id == "P0009":
            continue  # the named exception, below
        assert m.region is not None, c.chunk_id
        assert m.price_band is not None, c.chunk_id


def test_p0009_is_the_named_exception(chunks: list[Chunk]) -> None:
    p9 = [c for c in chunks if c.source_id == "P0009" or c.parent_id == "P0009"]
    assert p9
    for c in p9:
        assert c.metadata.visibility == "internal"
        assert c.metadata.region is None
        assert c.metadata.price_band is None
    others = [c for c in chunks if c not in p9 and c.source_type != "document"]
    assert all(c.metadata.region is not None and c.metadata.price_band is not None for c in others)


def test_no_empty_string_array_or_object_anywhere_in_the_metadata(chunks: list[Chunk]) -> None:
    for c in chunks:
        for name, value in vars(c.metadata).items():
            if value is None or name in ("updated", "visibility"):
                continue
            assert value != "" and value != () and value != {}, (c.chunk_id, name)
            if isinstance(value, tuple):
                assert all(value), (c.chunk_id, name)
        assert c.doc_type != "" and c.parent_id != ""


def test_variant_and_review_inherit_product_metadata(chunks: list[Chunk]) -> None:
    by_id = {c.chunk_id: c for c in chunks}
    variant = by_id["variant:P0001-BLK-REG-L:0"]
    product = by_id["product:P0001:0"]
    assert variant.parent_id == "P0001"
    assert variant.metadata.size == ("L",) and variant.metadata.colour == ("Black",)
    assert variant.metadata.brand == product.metadata.brand
    assert variant.metadata.price_band == product.metadata.price_band


# ---- visibility fails closed ------------------------------------------------------------

BREAKS = {
    "missing": lambda r: r.pop("visibility", None),
    "null": lambda r: r.__setitem__("visibility", None),
    "unknown": lambda r: r.__setitem__("visibility", "everyone"),
    "empty": lambda r: r.__setitem__("visibility", ""),
}


@pytest.mark.parametrize("how", list(BREAKS))
@pytest.mark.parametrize("source_type", list(SOURCE_FILES))
def test_missing_null_or_unknown_visibility_aborts_the_build(
    edit: Edit, source_type: str, how: str
) -> None:
    def _break(records: list[dict[str, Any]]) -> None:
        BREAKS[how](records[0])

    path = edit(SOURCE_FILES[source_type], _break)
    with pytest.raises(ChunkError, match="visibility"):
        build_chunks(path)


def test_children_of_an_internal_product_are_internal_even_if_they_say_public(
    edit: Edit,
) -> None:
    def hide(products: list[dict[str, Any]]) -> None:
        for p in products:
            if p["product_id"] == "P0001":
                p["visibility"] = "internal"

    path = edit("products.jsonl", hide)
    kids = [c for c in build_chunks(path) if c.source_id == "P0001" or c.parent_id == "P0001"]
    kinds = Counter(c.source_type for c in kids)
    assert kinds["product"] and kinds["variant"] and kinds["review"]
    assert {c.metadata.visibility for c in kids} == {"internal"}
    for name in ("variants.jsonl", "reviews.jsonl"):
        assert any(
            r["visibility"] == "public" for r in _read(path, name) if r["product_id"] == "P0001"
        )


def test_real_p0009_children_are_internal(chunks: list[Chunk]) -> None:
    assert {c.metadata.visibility for c in chunks if c.parent_id == "P0009"} == {"internal"}


# ---- policy regions ---------------------------------------------------------------------


def test_policy_chunks_carry_exactly_their_own_region(
    chunks: list[Chunk], fixtures_dir: Path
) -> None:
    key = json.loads((fixtures_dir / "_answer_key.json").read_text(encoding="utf-8"))
    bodies = {d["doc_id"]: d["body"] for d in _read(fixtures_dir, "documents.jsonl")}
    by_doc: dict[str, list[Chunk]] = defaultdict(list)
    for c in chunks:
        if c.source_type == "document":
            by_doc[c.source_id].append(c)
    verbatim: Counter[str] = Counter()
    for fact in key["region_policy_facts"]:
        for region, doc_id in fact["doc_ids"].items():
            assert by_doc[doc_id], doc_id
            for c in by_doc[doc_id]:
                assert c.metadata.region == (region,), (doc_id, c.metadata.region)
            value = fact["values"][region]
            if value in bodies[doc_id]:
                verbatim[fact["fact"]] += 1
                assert any(value in c.text for c in by_doc[doc_id]), (fact["fact"], region)
    # not vacuous: the two named facts are found verbatim in all four regions
    assert verbatim["return_window_days"] == 4
    assert verbatim["standard_delivery"] == 4


@pytest.mark.parametrize("how", ["ALL", "missing", "null", "empty"])
@pytest.mark.parametrize("doc_id", ["POL-RETURNS-US", "POL-SHIPPING-EU"])
def test_policy_with_region_all_or_no_region_is_refused(edit: Edit, doc_id: str, how: str) -> None:
    def _break(docs: list[dict[str, Any]]) -> None:
        for d in docs:
            if d["doc_id"] == doc_id:
                if how == "missing":
                    del d["region"]
                else:
                    d["region"] = {"ALL": "ALL", "null": None, "empty": ""}[how]

    with pytest.raises(ChunkError, match=doc_id):
        build_chunks(edit("documents.jsonl", _break))


# ---- guard ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["_answer_key.json", "prices.jsonl", "stock.jsonl"])
def test_guard_refuses_answer_key_and_live_tables(fixtures_dir: Path, name: str) -> None:
    with pytest.raises(NotIngestibleError):
        assert_ingestible(fixtures_dir / name, load_manifest(fixtures_dir))


def test_guard_refuses_a_file_not_listed_as_ingestible(fixtures_copy: Path) -> None:
    extra = fixtures_copy / "extra.jsonl"
    extra.write_text("{}\n", encoding="utf-8")
    with pytest.raises(NotIngestibleError):
        assert_ingestible(extra, load_manifest(fixtures_copy))


@pytest.mark.parametrize("name", ["_answer_key.json", "prices.jsonl", "stock.jsonl", "extra.jsonl"])
def test_pipeline_refuses_a_manifest_that_lists_a_forbidden_file(
    fixtures_copy: Path, name: str
) -> None:
    (fixtures_copy / "extra.jsonl").write_text("{}\n", encoding="utf-8")
    manifest_path = fixtures_copy / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["ingestible"] = [*manifest["ingestible"], name]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((NotIngestibleError, ChunkError)):
        load_records(fixtures_copy)


def test_ingestion_never_needs_the_answer_key_or_live_tables(
    fixtures_copy: Path, chunks: list[Chunk]
) -> None:
    for name in ("_answer_key.json", "prices.jsonl", "stock.jsonl"):
        (fixtures_copy / name).unlink()
    assert [c.chunk_id for c in build_chunks(fixtures_copy)] == [c.chunk_id for c in chunks]


# ---- no exact prices (hard rule 2) ------------------------------------------------------


@pytest.mark.parametrize(
    "planted", ["It costs $49.99 today.", "Now only 39.50 USD!", "Only 3 left in stock."]
)
def test_planted_price_or_stock_aborts_the_build(edit: Edit, planted: str) -> None:
    def plant(products: list[dict[str, Any]]) -> None:
        products[1]["description"] += " " + planted

    with pytest.raises(ChunkError, match="price or stock"):
        build_chunks(edit("products.jsonl", plant))


def test_no_amount_from_prices_jsonl_appears_in_any_chunk(
    chunks: list[Chunk], fixtures_dir: Path
) -> None:
    amounts = {r["amount"] for r in _read(fixtures_dir, "prices.jsonl")}
    assert len(amounts) > 500
    number = re.compile(r"(?<![\d.,])\d[\d,]*\.\d+")
    for c in chunks:
        found = {n.replace(",", "") for n in number.findall(c.text)}
        assert not found & amounts, (c.chunk_id, found & amounts)


# Regressions from the security review of the first implementation.


def _first_review_with_variant(fixtures_dir: Path) -> dict[str, Any]:
    return next(r for r in load_records(fixtures_dir)["reviews.jsonl"] if r["variant_id"])


def test_public_product_with_an_internal_variant_is_refused(edit: Edit) -> None:
    """Its colour and size would show in the public product chunk, so the run fails closed."""

    def hide(records: list[dict[str, Any]]) -> None:
        next(r for r in records if r["product_id"] == "P0001")["visibility"] = "internal"

    with pytest.raises(ChunkError, match="internal variant of public product P0001"):
        build_chunks(edit("variants.jsonl", hide))


def test_internal_variant_of_an_internal_product_is_accepted(chunks: list[Chunk]) -> None:
    variants = [c for c in chunks if c.source_type == "variant" and c.parent_id == "P0009"]
    assert len(variants) == 4
    assert {c.metadata.visibility for c in variants} == {"internal"}


def test_review_naming_another_products_variant_is_refused(edit: Edit, fixtures_dir: Path) -> None:
    review = _first_review_with_variant(fixtures_dir)
    foreign = next(
        v["variant_id"]
        for v in load_records(fixtures_dir)["variants.jsonl"]
        if v["product_id"] != review["product_id"]
    )

    def repoint(records: list[dict[str, Any]]) -> None:
        for record in records:
            if record["review_id"] == review["review_id"]:
                record["variant_id"] = foreign

    with pytest.raises(ChunkError, match="another product"):
        build_chunks(edit("reviews.jsonl", repoint))


def test_duplicate_product_cannot_replace_an_internal_one(edit: Edit) -> None:
    def clone_public(records: list[dict[str, Any]]) -> None:
        original = next(r for r in records if r["product_id"] == "P0009")
        records.append({**original, "visibility": "public", "status": "active"})

    with pytest.raises(ChunkError, match="duplicate"):
        build_chunks(edit("products.jsonl", clone_public))


def test_unreleased_product_cannot_be_public(edit: Edit) -> None:
    def publish(records: list[dict[str, Any]]) -> None:
        records[0]["status"] = "unreleased"

    with pytest.raises(ChunkError, match="cannot be public"):
        build_chunks(edit("products.jsonl", publish))


def test_list_valued_region_cannot_slip_past_the_policy_check(edit: Edit) -> None:
    def widen(records: list[dict[str, Any]]) -> None:
        next(r for r in records if r["doc_id"] == "POL-RETURNS-UK")["region"] = ["ALL"]

    with pytest.raises(ChunkError, match="single value"):
        build_chunks(edit("documents.jsonl", widen))


@pytest.mark.parametrize(
    "band", ["139.00", 139, "about 139", "139-139", "139-140", "139+", "150-100"]
)
def test_price_band_that_is_not_a_band_is_refused(edit: Edit, band: Any) -> None:
    def plant(records: list[dict[str, Any]]) -> None:
        records[0]["price_bands"]["US"] = band

    with pytest.raises(ChunkError, match="not a band"):
        build_chunks(edit("products.jsonl", plant))


@pytest.mark.parametrize(
    "key", ["price", "msrp_usd", "stock", "on_hand", "qty", "pricing", "rrp", "available", "units"]
)
def test_attribute_outside_the_allowlist_is_refused(edit: Edit, key: str) -> None:
    def plant(records: list[dict[str, Any]]) -> None:
        records[0]["attributes"][key] = 139

    with pytest.raises(ChunkError, match="unknown attributes"):
        build_chunks(edit("products.jsonl", plant))


def test_nested_attribute_value_is_refused(edit: Edit) -> None:
    def plant(records: list[dict[str, Any]]) -> None:
        records[0]["attributes"]["hood"] = {"US": 139}

    with pytest.raises(ChunkError, match="single values"):
        build_chunks(edit("products.jsonl", plant))


def test_price_band_key_must_be_one_of_the_regions(edit: Edit) -> None:
    def plant(records: list[dict[str, Any]]) -> None:
        records[0]["price_bands"]["$139.00"] = "100-150"

    with pytest.raises(ChunkError, match="not one of the regions"):
        build_chunks(edit("products.jsonl", plant))


@pytest.mark.parametrize("value", ["$139.00", 139])
def test_metadata_text_is_type_checked_and_scanned(edit: Edit, value: Any) -> None:
    def plant(records: list[dict[str, Any]]) -> None:
        records[0]["category"] = value

    with pytest.raises(ChunkError, match="price or stock|expected text"):
        build_chunks(edit("products.jsonl", plant))


@pytest.mark.parametrize(
    ("doc_id", "doc_type"), [("INT-002", "internal"), ("FAQ-001", "something_new")]
)
def test_only_known_doc_types_can_be_public(edit: Edit, doc_id: str, doc_type: str) -> None:
    def expose(records: list[dict[str, Any]]) -> None:
        doc = next(r for r in records if r["doc_id"] == doc_id)
        doc.update(doc_type=doc_type, visibility="public")

    with pytest.raises(ChunkError, match="cannot be public"):
        build_chunks(edit("documents.jsonl", expose))


def test_pipeline_runs_the_ingest_guard(edit: Edit) -> None:
    """Row #3: a file marked never_ingest under an ingestible name stops the pipeline."""

    def mark(records: list[dict[str, Any]]) -> None:
        records.append({"never_ingest": True})

    with pytest.raises(NotIngestibleError, match="never_ingest"):
        build_chunks(edit("reviews.jsonl", mark))


@pytest.mark.parametrize("mark", ["\u200b", "\u034f", "\ufe0f"])
def test_invisible_character_cannot_hide_a_price(edit: Edit, mark: str) -> None:
    def plant(records: list[dict[str, Any]]) -> None:
        records[0]["body"] = f"A bargain at ${mark}49.99 for sure."

    with pytest.raises(ChunkError, match="price or stock"):
        build_chunks(edit("reviews.jsonl", plant))


def test_amount_cut_across_two_chunks_is_still_caught(edit: Edit) -> None:
    def plant(records: list[dict[str, Any]]) -> None:
        records[0]["body"] = "word " * 190 + "149.99\n\nUSD " + "word " * 50

    with pytest.raises(ChunkError, match="price or stock"):
        build_chunks(edit("reviews.jsonl", plant))


def test_manifest_entry_outside_the_fixtures_is_refused(fixtures_copy: Path) -> None:
    sibling = fixtures_copy.parent / "x"
    sibling.mkdir()
    (sibling / "products.jsonl").write_text("", encoding="utf-8")
    path = fixtures_copy / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["ingestible"].append("../x/products.jsonl")
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ChunkError, match="ingestible files"):
        build_chunks(fixtures_copy)


def test_a_null_never_becomes_the_word_none(chunks: list[Chunk]) -> None:
    assert not [c.chunk_id for c in chunks if re.search(r"\bNone\b", c.text)]


def test_null_attribute_and_rating_are_left_out(edit: Edit) -> None:
    def blank_attribute(records: list[dict[str, Any]]) -> None:
        records[0]["attributes"]["hood"] = None

    def blank_rating(records: list[dict[str, Any]]) -> None:
        records[0]["rating"] = None

    edit("products.jsonl", blank_attribute)
    chunks = build_chunks(edit("reviews.jsonl", blank_rating))
    assert not [c.chunk_id for c in chunks if re.search(r"\bNone\b", c.text)]
