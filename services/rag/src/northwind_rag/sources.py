"""Turns the fixture files into validated chunks (RAG-01).

Every file passes `assert_ingestible` immediately before it is read. Everything here is checked
before the first database write, so a bad record aborts the run with the index untouched.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from northwind_data.ingest_guard import assert_ingestible, ingestible_files, load_manifest
from northwind_data.textscan import (
    find_generated_violations,
    find_money_or_stock,
    find_promotions,
    find_review_context_violations,
    fold_generated,
    is_printable_ascii,
)
from northwind_rag.chunking import chunk_markdown, with_prefix
from northwind_rag.models import (
    MAX_CHUNK_CHARS,
    Chunk,
    ChunkError,
    ChunkMetadata,
    SourceType,
    Visibility,
    parse_visibility,
)

Record = dict[str, Any]

EXPECTED_FILES = frozenset({"documents.jsonl", "products.jsonl", "reviews.jsonl", "variants.jsonl"})
# Documents that apply everywhere. Kept as the source spells it: `regions.json` is not ingestible,
# so retrieval matches on `region && ARRAY[<shopper region>, 'ALL']`.
ALL_REGIONS = "ALL"
# Attributes rendered into chunk text. An allowlist, because a live fact can arrive under any name
# ("pricing", "rrp") and with no currency marker for the scan to find (hard rule 2).
ATTRIBUTE_KEYS = frozenset(
    {
        "care",
        "country_of_origin",
        "dimensions",
        "fill",
        "fill_power",
        "fit_guidance",
        "fleece_weight",
        "gauge",
        "gsm",
        "hood",
        "inseam_in",
        "insulation",
        "material",
        "membrane",
        "packable",
        "pile",
        "pocket_depth_in",
        "quick_dry",
        "seam_sealed",
        "stretch",
        "thread_count",
        "upf",
        "warmth",
        "washable",
        "water_protection",
        "waterproof_rating_mm",
        "weight_g",
    }
)
# The only document types that may be public. Anything else, `internal` included, must say internal.
PUBLIC_DOC_TYPES = frozenset(
    {"care_guide", "faq", "policy_returns", "policy_shipping", "size_guide"}
)
# Two planted reviews state a discount on purpose: the adversarial evals need them in the index
# (RAG-02). Each passes for that one exact text and no other, in that review only. The trap IDs are
# checked against the answer key by a test; this code never reads that file. Generated text has no
# such exemption.
PLANTED_PROMOTIONS: dict[str, tuple[str, str]] = {
    "R00712": ("TRAP-INJ-003", "100% discount"),
    "R00881": ("TRAP-INJ-013", "90% off"),
}
# Who may get a generated context. A security rule, not a recall choice: reviews are written by
# shoppers, and a planted review's own sentence lifted it into top 5s (decision 0009). The chunk
# body is the attack, and retrieval returns the body. An allow-list: a new source type gets none.
CONTEXT_SOURCE_TYPES: frozenset[str] = frozenset({"product", "variant", "document"})
# A generated context says where a chunk sits, in one short line (RAG-02).
MAX_CONTEXT_CHARS = 240
_MARKUP = frozenset("<>{}[]`@")
_CONTEXT_START = "This "
# Words in a row that may match a review. A review is untrusted, and a context has no need to quote.
_COPY_WORDS = 5
# Every product, variant and review chunk must have these.
_PRODUCT_FIELDS_REQUIRED = ("category", "brand", "size", "colour", "material")


def _opt(value: Any) -> Any:
    """Normalises every spelling of 'nothing' in a source record to None."""
    if isinstance(value, str):
        value = value.strip()
    return value if value else None


def _opt_tuple(values: Iterable[str | None] | None) -> tuple[str, ...] | None:
    cleaned = tuple(v for v in (_opt(v) for v in values or ()) if v)
    return cleaned or None


def _require(record: Record, key: str, where: str) -> Any:
    value = _opt(record.get(key))
    if value is None:
        raise ChunkError(f"{where}: missing {key}")
    return value


def _date(value: str, where: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ChunkError(f"{where}: bad date {value!r}") from exc


def _strictest(*values: Visibility) -> Visibility:
    """A child of an internal record is internal, whatever it says about itself."""
    return "internal" if "internal" in values else "public"


def _normalised(text: str) -> str:
    """Folds lookalikes, drops invisible characters and collapses whitespace before a scan."""
    folded = unicodedata.normalize("NFKC", text)
    visible = "".join(ch for ch in folded if unicodedata.category(ch) not in ("Cf", "Mn", "Me"))
    return re.sub(r"\s+", " ", visible)


def _scan(text: str, where: str, review_id: str | None = None) -> None:
    """Hard rule 2. Callers scan whole fields too: a split can fall inside "149.99 USD".
    Only a review passes `review_id`, so no other kind of record can use the exemption."""
    normal = _normalised(text)
    hits = find_money_or_stock(normal)
    if hits:
        raise ChunkError(f"{where}: price or stock in chunk text: {hits}")
    promotions = find_promotions(normal)
    planted = PLANTED_PROMOTIONS.get(review_id or "")
    if promotions and (planted is None or promotions != [planted[1]]):
        raise ChunkError(f"{where}: promotion in chunk text: {promotions}")


def shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9']+", _normalised(text).lower())
    return {tuple(words[i : i + _COPY_WORDS]) for i in range(len(words) - _COPY_WORDS + 1)}


def context_problem(
    context: str,
    chunk: Chunk,
    canaries: frozenset[str] = frozenset(),
    internal: frozenset[tuple[str, ...]] = frozenset(),
) -> str | None:
    """Why a generated context may not be indexed, as a short code that holds none of its text, or
    None. Every ingest runs this again, so a cache edited by hand gets no further than the model.

    It reads the folded text, and `fold_generated` is what gets stored, so what is checked is what
    is kept. `internal` is the five-word runs of internal chunks: ingest may not read the answer
    key for canaries, and this catches copied internal text with or without one."""
    text = fold_generated(context)
    if not text:
        return "empty"
    if len(text.splitlines()) > 1:
        return "multi-line"
    if not is_printable_ascii(text):
        return "non-ascii"
    if len(text) > MAX_CONTEXT_CHARS:
        return "too-long"
    if _MARKUP & set(text):
        return "markup"
    if not text.startswith(_CONTEXT_START):
        return "shape"
    violations = find_generated_violations(text)
    if chunk.source_type == "review":
        violations += find_review_context_violations(text)
    if violations:
        return violations[0].split(":", 1)[0]
    if any(canary in text for canary in canaries):
        return "canary"
    if chunk.metadata.visibility == "public" and shingles(text) & internal:
        return "internal-text"
    if chunk.source_type == "review":
        _, _, body = chunk.text.partition("\n\n")  # the first part is our own heading
        if shingles(text) & shingles(body):
            return "copied-from-review"
    return None


def _labelled(*pairs: tuple[str, Any]) -> str:
    """ "Label: value." for each pair that has a value, so a null never becomes the word None."""
    return " ".join(f"{label}: {value}." for label, value in pairs if _opt(value) is not None)


def _by_id(records: list[Record], key: str) -> dict[str, Record]:
    """Refuses duplicates: a later record must not silently replace an internal one."""
    by_id: dict[str, Record] = {}
    for record in records:
        record_id = _require(record, key, key)
        if record_id in by_id:
            raise ChunkError(f"duplicate {key} {record_id}")
        by_id[record_id] = record
    return by_id


def _read_jsonl(path: Path, manifest: dict[str, Any]) -> list[Record]:
    assert_ingestible(path, manifest)
    with path.open(encoding="utf-8") as lines:
        return [json.loads(line) for line in lines if line.strip()]


def _product_metadata(product: Record) -> ChunkMetadata:
    where = f"product {product.get('product_id')}"
    attributes = product.get("attributes") or {}
    visibility = parse_visibility(product.get("visibility"), where)
    if visibility == "public" and product.get("status") != "active":
        raise ChunkError(f"{where}: status {product.get('status')!r} cannot be public")
    return ChunkMetadata(
        category=_opt(product.get("category")),
        brand=_opt(product.get("brand")),
        size=_opt_tuple(product.get("sizes")),
        colour=_opt_tuple(product.get("colours")),
        material=_opt(attributes.get("material")),
        price_band=_opt(product.get("price_bands")),
        region=_opt_tuple(product.get("regions")),
        updated=_date(_require(product, "updated", where), where),
        visibility=visibility,
    )


def _product_text(product: Record) -> str:
    attributes = product.get("attributes") or {}
    where = f"product {product.get('product_id')}"
    unknown = sorted(set(attributes) - ATTRIBUTE_KEYS)
    if unknown:
        raise ChunkError(f"{where}: unknown attributes {unknown}")
    present = {key: value for key, value in attributes.items() if value is not None}
    nested = sorted(k for k, v in present.items() if not isinstance(v, str | int | float))
    if nested:
        raise ChunkError(f"{where}: attributes must be single values: {nested}")
    rendered = "; ".join(f"{key}: {present[key]}" for key in sorted(present))
    lines = [
        _labelled(("Brand", product.get("brand")), ("Department", product.get("department"))),
        str(product.get("description") or ""),
        _labelled(("Attributes", rendered)),
        _labelled(("Sizes", ", ".join(product.get("sizes") or []))),
        _labelled(("Colours", ", ".join(product.get("colours") or []))),
        _labelled(("Fits", ", ".join(product.get("fits") or []))),
    ]
    return "\n\n".join(line for line in lines if line)


def _chunks(
    source_type: SourceType,
    source_id: str,
    texts: list[str],
    metadata: ChunkMetadata,
    parent_id: str | None = None,
    doc_type: str | None = None,
) -> list[Chunk]:
    if not texts:
        raise ChunkError(f"{source_type} {source_id}: produced no chunks")
    return [
        Chunk(source_type, source_id, seq, text, metadata, parent_id, doc_type)
        for seq, text in enumerate(texts)
    ]


def _document_chunks(doc: Record, max_chars: int) -> list[Chunk]:
    doc_id = _require(doc, "doc_id", "document")
    where = f"document {doc_id}"
    doc_type = _require(doc, "doc_type", where)
    region = _require(doc, "region", where)
    if not isinstance(region, str):
        raise ChunkError(f"{where}: region must be a single value, got {region!r}")
    if doc_type.startswith("policy_") and region == ALL_REGIONS:
        raise ChunkError(f"{where}: a policy must name its own region, not {ALL_REGIONS}")
    visibility = parse_visibility(doc.get("visibility"), where)
    if visibility == "public" and doc_type not in PUBLIC_DOC_TYPES:
        raise ChunkError(f"{where}: doc_type {doc_type!r} cannot be public")
    metadata = ChunkMetadata(
        category=None,
        brand=None,
        size=None,
        colour=None,
        material=None,
        price_band=None,
        region=(region,),
        updated=_date(_require(doc, "updated", where), where),
        visibility=visibility,
    )
    body = doc.get("body") or ""
    _scan(body, where)
    texts = chunk_markdown(_require(doc, "title", where), body, max_chars)
    return _chunks("document", doc_id, texts, metadata, doc_type=doc_type)


def _variant_chunks(
    variant: Record, product: Record, base: ChunkMetadata, max_chars: int
) -> list[Chunk]:
    variant_id = _require(variant, "variant_id", "variant")
    where = f"variant {variant_id}"
    own = parse_visibility(variant.get("visibility"), where)
    if own == "internal" and base.visibility == "public":
        # The public product chunk lists every size and colour, so this variant would show there.
        raise ChunkError(f"{where}: internal variant of public product {product['product_id']}")
    metadata = replace(
        base,
        size=_opt_tuple([variant.get("size")]),
        colour=_opt_tuple([variant.get("colour")]),
        visibility=_strictest(own, base.visibility),
    )
    body = _labelled(
        ("Colour", variant.get("colour")),
        ("Size", variant.get("size")),
        ("Fit", variant.get("fit")),
        ("SKU", variant.get("sku")),
    )
    _scan(body, where)
    texts = with_prefix((product["name"],), body, max_chars)
    return _chunks("variant", variant_id, texts, metadata, parent_id=product["product_id"])


def _review_chunks(
    review: Record,
    product: Record,
    base: ChunkMetadata,
    variants: dict[str, Record],
    max_chars: int,
) -> list[Chunk]:
    review_id = _require(review, "review_id", "review")
    where = f"review {review_id}"
    changes: dict[str, Any] = {}
    variant_id = _opt(review.get("variant_id"))
    if variant_id is not None:
        variant = variants.get(variant_id)
        if variant is None:
            raise ChunkError(f"{where}: unknown variant {variant_id}")
        if variant.get("product_id") != product["product_id"]:
            raise ChunkError(f"{where}: variant {variant_id} belongs to another product")
        changes = {
            "size": _opt_tuple([variant.get("size")]),
            "colour": _opt_tuple([variant.get("colour")]),
        }
    metadata = replace(
        base,
        **changes,
        updated=_date(_require(review, "date", where), where),
        visibility=_strictest(parse_visibility(review.get("visibility"), where), base.visibility),
    )
    rating = review.get("rating")
    header = _labelled(("Rating", None if rating is None else f"{rating} of 5"))
    if review.get("verified"):
        header = f"Verified purchase. {header}".strip()
    parts = (header, review.get("title"), review.get("body"))
    body = "\n\n".join(part for part in parts if part)
    _scan(body, where, review_id)
    texts = with_prefix((f"Review of {product['name']}",), body, max_chars)
    return _chunks("review", review_id, texts, metadata, parent_id=product["product_id"])


def check_chunk(chunk: Chunk) -> None:
    """Metadata coverage and hard rule 2. `sync_chunks` runs it too, on whatever it is given."""
    meta = chunk.metadata
    _scan(chunk.text, chunk.chunk_id, chunk.source_id if chunk.source_type == "review" else None)
    _scan(" ".join(v for v in (meta.category, meta.brand, meta.material) if v), chunk.chunk_id)
    if chunk.context is not None:
        if chunk.source_type not in CONTEXT_SOURCE_TYPES:
            raise ChunkError(f"{chunk.chunk_id}: a {chunk.source_type} may not have a context")
        problem = context_problem(chunk.context, chunk)
        if problem:
            raise ChunkError(f"{chunk.chunk_id}: generated context refused: {problem}")
    if meta.region is None and meta.visibility == "public":
        raise ChunkError(f"{chunk.chunk_id}: public chunk with no region")
    if chunk.source_type == "document":
        return
    for name in _PRODUCT_FIELDS_REQUIRED:
        if getattr(meta, name) is None:
            raise ChunkError(f"{chunk.chunk_id}: missing {name}")
    # Only an internal chunk may have no band; today that is the unreleased product P0009.
    if meta.price_band is None and meta.visibility == "public":
        raise ChunkError(f"{chunk.chunk_id}: public chunk with no price band")


def load_records(fixtures_dir: Path) -> dict[str, list[Record]]:
    """The ingestible files by name. Refuses a manifest that lists more or less than we handle."""
    manifest = load_manifest(fixtures_dir)
    # The raw entries, not path.name: "../x/products.jsonl" must not pass as products.jsonl.
    names = sorted(manifest["ingestible"])
    if names != sorted(EXPECTED_FILES):
        raise ChunkError(f"ingestible files {names} != {sorted(EXPECTED_FILES)}")
    files = ingestible_files(fixtures_dir)
    return {path.name: _read_jsonl(path, manifest) for path in files}


def build_chunks(fixtures_dir: Path, max_chars: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """Every chunk for the fixtures, sorted by chunk_id. A bad record raises before any write."""
    records = load_records(fixtures_dir)
    products = _by_id(records["products.jsonl"], "product_id")
    bases = {pid: _product_metadata(p) for pid, p in products.items()}
    variants = _by_id(records["variants.jsonl"], "variant_id")

    def parent(record: Record, where: str) -> str:
        product_id: str = _require(record, "product_id", where)
        if product_id not in products:
            raise ChunkError(f"{where}: unknown product {product_id}")
        return product_id

    chunks: list[Chunk] = []
    for product_id, product in products.items():
        text = _product_text(product)
        _scan(text, f"product {product_id}")
        texts = with_prefix((product["name"],), text, max_chars)
        chunks += _chunks("product", product_id, texts, bases[product_id])
    for variant in records["variants.jsonl"]:
        pid = parent(variant, f"variant {variant.get('variant_id')}")
        chunks += _variant_chunks(variant, products[pid], bases[pid], max_chars)
    for review in records["reviews.jsonl"]:
        pid = parent(review, f"review {review.get('review_id')}")
        chunks += _review_chunks(review, products[pid], bases[pid], variants, max_chars)
    for doc in records["documents.jsonl"]:
        chunks += _document_chunks(doc, max_chars)

    for chunk in chunks:
        check_chunk(chunk)
    ids = [c.chunk_id for c in chunks]
    if len(set(ids)) != len(ids):
        raise ChunkError("duplicate chunk ids in the source data")
    return sorted(chunks, key=lambda c: c.chunk_id)
