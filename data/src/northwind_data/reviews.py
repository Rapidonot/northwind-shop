"""Template-built shopper reviews plus planted injection and contradiction reviews (FOUND-1).

Ordinary reviews agree with the spec sheet (fit guidance, water protection) and never mention
exact attributes, prices or stock, so the only review/spec disagreements are the planted ones.
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from northwind_data.catalog import Product
from northwind_data.planted import (
    APPENDED_REVIEWS,
    CONTRADICTION_REVIEWS,
    INJECTION_REVIEWS,
    PlantedReview,
)
from northwind_data.rng import fake, rng
from northwind_data.version import BASE_DATE

Record = dict[str, Any]

OPENINGS: dict[int, tuple[str, ...]] = {
    5: (
        "Absolutely love it.",
        "Exactly what I wanted.",
        "Best purchase this season.",
        "Really impressed.",
    ),
    4: (
        "Very happy overall.",
        "Solid choice.",
        "Does the job well.",
        "Good quality for everyday use.",
    ),
    3: (
        "It's fine, nothing special.",
        "Mixed feelings.",
        "Decent but not perfect.",
        "Okay overall.",
    ),
    2: ("Disappointed.", "Not what I hoped for.", "Expected more."),
    1: ("Would not buy again.", "Really let down.", "Not for me at all."),
}
TITLES: dict[int, tuple[str, ...]] = {
    5: ("Love it", "Fantastic", "Highly recommend", "Great buy"),
    4: ("Really good", "Happy with it", "Solid"),
    3: ("It's okay", "Fine", "Mixed"),
    2: ("Meh", "Disappointing"),
    1: ("Not good", "Sent it back"),
}
POSITIVE: dict[str, tuple[str, ...]] = {
    "rain_shell": (
        "The hood stays put in the wind.",
        "Pockets are well placed.",
        "Breathes better than my old shell.",
    ),
    "insulated_jacket": ("Very warm on cold mornings.", "Great warmth without bulk."),
    "fleece": ("Cosy and warm.", "Great layering piece."),
    "base_layer": ("Doesn't get smelly on multi-day trips.", "Comfortable next to skin."),
    "pants": ("Comfortable on long hikes.", "Lots of useful pockets."),
    "shirt": ("Breathable on hot days.", "Looks smart enough for dinner."),
    "knitwear": ("Lovely soft knit.", "Keeps its shape."),
    "sheets": (
        "Soft from the first wash.",
        "Stays on the mattress all night.",
        "Crisp and cool to sleep on.",
    ),
    "duvet": ("Comfortable all night.", "Fluffed up nicely after a day."),
    "towel": ("Really absorbent.", "Soft even after many washes."),
    "throw": ("So soft on the sofa.", "Great size for curling up."),
    "rug": ("Looks great in the living room.", "Lies flat right away."),
    "table_linen": ("Hangs nicely on the table.", "Washes well."),
}
NEGATIVE: dict[str, tuple[str, ...]] = {
    "apparel": (
        "The zip feels a bit cheap.",
        "Colour was slightly different from the photos.",
        "Pills a little after a while.",
    ),
    "sheets": ("Wrinkles easily.", "Pilled a little after a few washes."),
    "duvet": ("A bit noisy when you move.",),
    "towel": ("Shed lint the first couple of washes.",),
    "throw": ("Sheds a little.",),
    "rug": ("Edges curled at first.",),
    "table_linen": ("Needs ironing.",),
}
WATER: dict[str, tuple[str, ...]] = {
    "waterproof": ("Kept me dry through a proper downpour.", "Stayed dry on a wet hike."),
    "water_resistant": (
        "Fine in light drizzle but not one for downpours.",
        "Handles a passing shower, nothing heavier.",
    ),
}
FIT: dict[str, tuple[str, ...]] = {
    "true_to_size": ("Fits true to size.", "Sizing was spot on."),
    "runs_small": ("Runs small, I'd size up.", "Snug, order one size up."),
    "runs_large": ("Runs a bit large.", "Roomy, could have sized down."),
}
RELAXED_REVIEW = "Plenty of room in the shoulders in the relaxed fit."


def _body(p: Product, rating: int, variant: Record | None, r: random.Random) -> str:
    rec = p.record
    sub = rec["subcategory"]
    a = rec["attributes"]
    neg = NEGATIVE.get(sub) or NEGATIVE["apparel"]
    parts = [r.choice(OPENINGS[rating])]
    if variant is not None:
        if variant["fit"]:
            fit_note = ", relaxed fit" if variant["fit"] == "relaxed" else ""
            parts.append(f"I got the {variant['colour']} in {variant['size']}{fit_note}.")
        else:
            parts.append(f"I bought the {variant['size']} in {variant['colour']}.")
    if rating >= 4:
        parts.extend(r.sample(POSITIVE[sub], k=min(len(POSITIVE[sub]), r.choice([1, 2]))))
        if a.get("water_protection") in WATER and r.random() < 0.6:
            parts.append(r.choice(WATER[a["water_protection"]]))
    elif rating == 3:
        parts.append(r.choice(POSITIVE[sub]))
        parts.append(r.choice(neg))
    else:
        parts.extend(r.sample(neg, k=min(len(neg), r.choice([1, 2]))))
    if "fit_guidance" in a and r.random() < 0.6:
        parts.append(r.choice(FIT[a["fit_guidance"]]))
    if variant is not None and variant["fit"] == "relaxed" and rating >= 4 and r.random() < 0.7:
        parts.append(RELAXED_REVIEW)
    return " ".join(parts)


def _review(
    seed: int,
    p: Product,
    key: str,
    rating: int,
    title: str,
    body: str,
    variant: Record | None,
    r: random.Random,
) -> Record:
    f = fake(seed, "reviewer", p.record["product_id"], key)
    reviewer = f"{f.first_name()} {f.last_name()[0]}."
    return {
        "product_id": p.record["product_id"],
        "variant_id": variant["variant_id"] if variant else None,
        "rating": rating,
        "title": title,
        "body": body,
        "reviewer": reviewer,
        "date": (BASE_DATE - timedelta(days=r.randrange(1, 541))).isoformat(),
        "verified": r.random() < 0.8,
        "visibility": "public",
    }


def build_reviews(products: list[Product], seed: int) -> tuple[list[Record], dict[str, str]]:
    """Returns the reviews (sorted by ID) and a map trap_id -> review_id for planted ones."""
    planted_by_slot: dict[str, list[PlantedReview]] = {}
    for pr in (*INJECTION_REVIEWS, *CONTRADICTION_REVIEWS):
        planted_by_slot.setdefault(pr.slot, []).append(pr)
    known = {p.slot for p in products}
    missing = sorted((set(planted_by_slot) | {pr.slot for pr in APPENDED_REVIEWS}) - known)
    if missing:
        raise ValueError(f"planted reviews target unknown slots: {missing}")

    reviews: list[Record] = []
    trap_review: dict[str, str] = {}
    n = 0
    for p in products:
        if p.record["visibility"] != "public":
            continue
        r = rng(seed, "reviews", p.slot)
        count = r.randint(0, 12)
        batch: list[tuple[Record, str | None]] = []
        for i in range(count):
            rating = r.choices([1, 2, 3, 4, 5], [5, 8, 15, 32, 40])[0]
            variant = r.choice(p.variants) if r.random() < 0.4 else None
            body = _body(p, rating, variant, r)
            batch.append(
                (_review(seed, p, str(i), rating, r.choice(TITLES[rating]), body, variant, r), None)
            )
        for pr in planted_by_slot.get(p.slot, []):
            pos = r.randint(0, len(batch))
            batch.insert(
                pos,
                (_review(seed, p, pr.trap_id, pr.rating, pr.title, pr.body, None, r), pr.trap_id),
            )
        for rec, trap_id in batch:
            n += 1
            rec["review_id"] = f"R{n:05d}"
            reviews.append(rec)
            if trap_id:
                trap_review[trap_id] = rec["review_id"]
    # After every generated review, each from its own stream: no earlier review changes (row #13b).
    by_slot = {p.slot: p for p in products}
    for pr in APPENDED_REVIEWS:
        p = by_slot[pr.slot]
        if p.record["visibility"] != "public":
            raise ValueError(f"appended review {pr.trap_id} targets a non-public product")
        r = rng(seed, "appended", pr.trap_id)
        rec = _review(seed, p, pr.trap_id, pr.rating, pr.title, pr.body, None, r)
        n += 1
        rec["review_id"] = f"R{n:05d}"
        reviews.append(rec)
        trap_review[pr.trap_id] = rec["review_id"]
    return reviews, trap_review
