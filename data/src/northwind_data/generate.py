"""Build the whole dataset in memory: filename -> bytes (FOUND-1)."""

from __future__ import annotations

from importlib.metadata import version

from northwind_data.answer_key import build_answer_key
from northwind_data.catalog import build_catalog
from northwind_data.commerce import build_prices, build_stock
from northwind_data.documents import build_documents
from northwind_data.ingest_guard import LIVE_TABLES
from northwind_data.regions import REGIONS
from northwind_data.reviews import build_reviews
from northwind_data.version import BASE_DATE, DATA_VERSION, DEFAULT_SEED
from northwind_data.writer import jsonl, pretty, sha256

INGESTIBLE: tuple[str, ...] = (
    "documents.jsonl",
    "products.jsonl",
    "reviews.jsonl",
    "variants.jsonl",
)
ANSWER_KEY = "_answer_key.json"


def generate(seed: int = DEFAULT_SEED) -> dict[str, bytes]:
    products = build_catalog(seed)
    variants = sorted((v for p in products for v in p.variants), key=lambda v: v["variant_id"])
    prices = build_prices(products)
    stock = build_stock(products, seed)
    reviews, trap_reviews = build_reviews(products, seed)
    documents = build_documents(seed)
    key = build_answer_key(seed, products, prices, stock, trap_reviews)

    files: dict[str, bytes] = {
        "products.jsonl": jsonl(
            sorted((p.record for p in products), key=lambda r: r["product_id"])
        ),
        "variants.jsonl": jsonl(variants),
        "prices.jsonl": jsonl(prices),
        "stock.jsonl": jsonl(stock),
        "reviews.jsonl": jsonl(reviews),
        "documents.jsonl": jsonl(documents),
        "regions.json": pretty(list(REGIONS)),
        ANSWER_KEY: pretty(key),
    }
    hashes = {name: sha256(files[name]) for name in sorted(files)}
    manifest = {
        "data_version": DATA_VERSION,
        "seed": seed,
        "base_date": BASE_DATE.isoformat(),
        "generator": "northwind_data",
        "faker_version": version("faker"),
        "counts": {
            "products": len(products),
            "variants": len(variants),
            "prices": len(prices),
            "stock": len(stock),
            "reviews": len(reviews),
            "documents": len(documents),
        },
        "files": hashes,
        "dataset_sha256": sha256("".join(f"{n} {h}\n" for n, h in hashes.items()).encode()),
        "ingestible": list(INGESTIBLE),
        "live_tables": sorted(LIVE_TABLES),
        "config": ["regions.json"],
        "never_ingest": [ANSWER_KEY],
    }
    files["manifest.json"] = pretty(manifest)
    return files
