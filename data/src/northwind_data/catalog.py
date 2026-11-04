"""Products and variants (FOUND-1).

Template-built product copy derived from structured attributes, so text and attributes agree
(the only planted disagreements live in reviews). No prices or stock in any text (hard rule 2).
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from northwind_data.planted import (
    APPAREL_SIZES,
    PLANTED,
    RANDOM_OVERRIDES,
    RANDOM_REMOVALS,
    PlantedProduct,
)
from northwind_data.regions import REGION_BY_CODE, REGION_CODES
from northwind_data.rng import canary, rng
from northwind_data.version import BASE_DATE

Record = dict[str, Any]


@dataclass(frozen=True)
class Colour:
    name: str
    code: str
    family: str


COLOURS: tuple[Colour, ...] = (
    Colour("Black", "BLK", "black"),
    Colour("Navy", "NAV", "blue"),
    Colour("Glacier Blue", "GLB", "blue"),
    Colour("Cobalt", "COB", "blue"),
    Colour("Sage", "SAG", "green"),
    Colour("Forest", "FOR", "green"),
    Colour("Rust", "RST", "orange"),
    Colour("Charcoal", "CHR", "grey"),
    Colour("Heather Grey", "HGY", "grey"),
    Colour("Slate", "SLT", "grey"),
    Colour("Sand", "SND", "beige"),
    Colour("Oat", "OAT", "beige"),
    Colour("Ivory", "IVO", "white"),
    Colour("White", "WHT", "white"),
    Colour("Clay", "CLY", "brown"),
    Colour("Plum", "PLM", "purple"),
    Colour("Ochre", "OCH", "yellow"),
    Colour("Burgundy", "BUR", "red"),
)
COLOUR_BY_NAME: dict[str, Colour] = {c.name: c for c in COLOURS}
FIT_CODES: dict[str, str] = {"regular": "REG", "relaxed": "REL", "athletic": "ATH"}
FIT_ORDER: tuple[str, ...] = ("regular", "relaxed", "athletic")

APPAREL_BRANDS: tuple[str, ...] = ("Northwind Trail", "Tidewick", "Corvane", "Brindlemoor")
HOME_BRANDS: tuple[str, ...] = ("Halvardine Home", "Larchpell", "Emberquay")
PLACE_WORDS: tuple[str, ...] = (
    "Harbor",
    "Ridge",
    "Fell",
    "Cove",
    "Tarn",
    "Moor",
    "Larch",
    "Cinder",
    "Brook",
    "Skerry",
    "Heath",
    "Gale",
    "Drift",
    "Alder",
    "Birch",
    "Quarry",
    "Shoal",
    "Crag",
    "Glen",
    "Marsh",
    "Hollow",
    "Bluff",
    "Kestrel",
    "Wren",
    "Juniper",
    "Cairn",
    "Thistle",
    "Sorrel",
    "Haven",
    "Fen",
    "Spindle",
    "Lantern",
    "Meadow",
    "Orchard",
)
COUNTRIES: tuple[str, ...] = (
    "Portugal",
    "Vietnam",
    "Peru",
    "India",
    "Turkey",
    "Lithuania",
    "Mexico",
    "Sri Lanka",
)
MEMBRANES: tuple[str, ...] = ("StormVeil 2.5-layer", "StormVeil 3-layer", "NW-Shield 2-layer")


def _apparel_common(r: random.Random) -> Record:
    return {
        "fit_guidance": r.choices(["true_to_size", "runs_small", "runs_large"], [70, 15, 15])[0],
        "country_of_origin": r.choice(COUNTRIES),
    }


def _rain_shell(r: random.Random) -> Record:
    a = _apparel_common(r)
    a.update(
        water_protection="waterproof",
        waterproof_rating_mm=r.choice([10000, 15000, 20000, 28000]),
        membrane=r.choice(MEMBRANES),
        seam_sealed=True,
        hood=r.choice(["adjustable", "stowable", "helmet-compatible"]),
        weight_g=r.randrange(280, 521, 10),
        packable=r.random() < 0.5,
        material=r.choice(["recycled nylon ripstop", "recycled polyester", "nylon taffeta"]),
    )
    return a


def _insulated(r: random.Random) -> Record:
    a = _apparel_common(r)
    a["water_protection"] = "water_resistant"
    a["insulation"] = r.choice(["down", "synthetic"])
    if a["insulation"] == "down":
        a["fill_power"] = r.choice([650, 700, 800])
    a["weight_g"] = r.randrange(300, 901, 10)
    a["packable"] = r.random() < 0.4
    a["material"] = r.choice(["recycled nylon", "nylon ripstop"])
    return a


def _fleece(r: random.Random) -> Record:
    a = _apparel_common(r)
    a.update(fleece_weight=r.choice([100, 200, 300]), material="recycled polyester fleece")
    return a


def _base_layer(r: random.Random) -> Record:
    a = _apparel_common(r)
    a.update(
        material=r.choice(["100% merino wool", "merino-nylon blend", "recycled polyester"]),
        gsm=r.choice([150, 200, 250]),
    )
    return a


def _pants(r: random.Random) -> Record:
    a = _apparel_common(r)
    a.update(
        material=r.choice(["nylon-elastane", "organic cotton canvas", "recycled polyester"]),
        inseam_in=r.choice([30, 32, 34]),
        stretch=r.random() < 0.6,
        water_protection=r.choice(["water_resistant", "none"]),
    )
    return a


def _shirt(r: random.Random) -> Record:
    a = _apparel_common(r)
    a["material"] = r.choice(["organic cotton", "linen", "cotton-hemp blend", "recycled polyester"])
    upf = r.choice([None, 30, 50])
    if upf is not None:
        a["upf"] = upf
    return a


def _knitwear(r: random.Random) -> Record:
    a = _apparel_common(r)
    a.update(
        material=r.choice(["lambswool", "100% merino wool", "organic cotton", "alpaca blend"]),
        gauge=r.choice(["fine", "mid", "chunky"]),
    )
    return a


def _sheets(r: random.Random) -> Record:
    # Random sheets are never organic: the organic-cotton query set is fully planted.
    a: Record = {
        "material": r.choice(
            ["long-staple cotton sateen", "cotton percale", "stonewashed linen", "lyocell"]
        )
    }
    if "linen" not in a["material"]:
        a["thread_count"] = r.choice([300, 400, 500])
    a["pocket_depth_in"] = r.choice([12, 14, 16, 18])
    a["country_of_origin"] = r.choice(COUNTRIES)
    return a


def _duvet(r: random.Random) -> Record:
    return {
        "fill": r.choice(["down alternative", "washable wool", "duck down"]),
        "warmth": r.choice(["lightweight", "all-season", "extra warm"]),
        "material": "cotton percale shell",
        "country_of_origin": r.choice(COUNTRIES),
    }


def _towel(r: random.Random) -> Record:
    return {
        "material": r.choice(["Turkish cotton", "organic cotton", "cotton-modal blend"]),
        "gsm": r.choice([500, 600, 700, 800]),
        "quick_dry": r.random() < 0.5,
        "country_of_origin": r.choice(COUNTRIES),
    }


def _throw(r: random.Random) -> Record:
    # Random throws are never wool: the washable-wool query set is fully planted.
    return {
        "material": r.choice(
            [
                "cotton chenille",
                "recycled polyester sherpa",
                "cotton waffle knit",
                "alpaca-acrylic blend",
            ]
        ),
        "care": r.choice(["machine_washable", "dry_clean_only"]),
        "dimensions": r.choice(["50 x 60 in", "50 x 70 in", "60 x 80 in"]),
        "country_of_origin": r.choice(COUNTRIES),
    }


def _rug(r: random.Random) -> Record:
    return {
        "material": r.choice(["jute", "wool", "cotton flatweave", "recycled polyester"]),
        "washable": r.random() < 0.4,
        "pile": r.choice(["flat", "low", "medium"]),
        "country_of_origin": r.choice(COUNTRIES),
    }


def _table_linen(r: random.Random) -> Record:
    return {
        "material": r.choice(["linen", "cotton", "linen-cotton blend"]),
        "care": "machine_washable",
        "country_of_origin": r.choice(COUNTRIES),
    }


@dataclass(frozen=True)
class Subcat:
    key: str
    category: str
    department: str
    count: int  # total, planted included
    nouns: tuple[str, ...]
    price_lo: int
    price_hi: int
    size_guide: str | None
    sizes: tuple[str, ...]
    attrs: Callable[[random.Random], Record]
    intros: tuple[str, ...]


BED_SIZES = ("Twin", "Full", "Queen", "King")
SUBCATS: tuple[Subcat, ...] = (
    Subcat(
        "rain_shell",
        "apparel",
        "Jackets",
        18,
        ("Rain Jacket", "Shell", "Storm Jacket"),
        110,
        320,
        "SG-OUTERWEAR",
        APPAREL_SIZES,
        _rain_shell,
        (
            "The {name} is a rain jacket for wet commutes and long walks.",
            "Built for unsettled weather, the {name} is an everyday shell.",
            "The {name} is a no-fuss rain shell for trail days and city rain alike.",
        ),
    ),
    Subcat(
        "insulated_jacket",
        "apparel",
        "Jackets",
        16,
        ("Down Jacket", "Puffer", "Insulated Jacket"),
        150,
        380,
        "SG-OUTERWEAR",
        APPAREL_SIZES,
        _insulated,
        (
            "The {name} is a warm insulated jacket for cold, dry days.",
            "The {name} brings serious warmth to winter walks and cold commutes.",
        ),
    ),
    Subcat(
        "fleece",
        "apparel",
        "Fleece",
        16,
        ("Fleece Jacket", "Fleece Pullover", "Grid Fleece"),
        60,
        160,
        "SG-OUTERWEAR",
        APPAREL_SIZES,
        _fleece,
        (
            "The {name} is a cosy fleece for layering or wearing on its own.",
            "The {name} is a go-to mid layer for cool mornings.",
        ),
    ),
    Subcat(
        "base_layer",
        "apparel",
        "Base Layers",
        18,
        ("Base Layer", "Thermal Layer"),
        45,
        110,
        "SG-BASELAYERS",
        APPAREL_SIZES,
        _base_layer,
        (
            "The {name} manages moisture on the move.",
            "The {name} is a next-to-skin layer for active days.",
        ),
    ),
    Subcat(
        "pants",
        "apparel",
        "Pants",
        18,
        ("Trail Pants", "Hiking Pants", "Travel Pants"),
        60,
        150,
        "SG-BOTTOMS",
        APPAREL_SIZES,
        _pants,
        (
            "The {name} are versatile pants that move with you.",
            "The {name} are made for long days on the trail and in town.",
        ),
    ),
    Subcat(
        "shirt",
        "apparel",
        "Shirts",
        18,
        ("Shirt", "Tee", "Overshirt"),
        40,
        95,
        "SG-TOPS",
        APPAREL_SIZES,
        _shirt,
        (
            "The {name} is an easy layer for warm days and travel.",
            "The {name} is a breathable everyday top.",
        ),
    ),
    Subcat(
        "knitwear",
        "apparel",
        "Knitwear",
        16,
        ("Crewneck Sweater", "Cardigan", "Half-Zip Sweater"),
        70,
        220,
        "SG-TOPS",
        APPAREL_SIZES,
        _knitwear,
        (
            "The {name} is a soft knit for cool evenings.",
            "The {name} is a classic knit made to be worn for years.",
        ),
    ),
    Subcat(
        "sheets",
        "home",
        "Bedding",
        16,
        ("Sheet Set",),
        90,
        260,
        "SG-BEDDING",
        BED_SIZES,
        _sheets,
        (
            "The {name} brings crisp comfort to everyday sleep.",
            "The {name} is a bedding staple that softens with every wash.",
        ),
    ),
    Subcat(
        "duvet",
        "home",
        "Bedding",
        12,
        ("Duvet", "Comforter"),
        120,
        340,
        "SG-BEDDING",
        BED_SIZES,
        _duvet,
        (
            "The {name} is a duvet for restful nights.",
            "The {name} keeps you comfortable through the night.",
        ),
    ),
    Subcat(
        "towel",
        "home",
        "Bath",
        14,
        ("Towel", "Bath Towel"),
        15,
        60,
        "SG-TOWELS",
        ("Hand Towel", "Bath Towel", "Bath Sheet"),
        _towel,
        (
            "The {name} is a plush, absorbent towel for daily use.",
            "The {name} brings spa comfort to the bathroom.",
        ),
    ),
    Subcat(
        "throw",
        "home",
        "Living",
        14,
        ("Throw", "Blanket Throw"),
        40,
        140,
        None,
        ("One Size",),
        _throw,
        (
            "The {name} is a soft throw for the sofa or the foot of the bed.",
            "The {name} adds warmth and texture to any room.",
        ),
    ),
    Subcat(
        "rug",
        "home",
        "Living",
        12,
        ("Rug", "Area Rug"),
        60,
        420,
        "SG-RUGS",
        ("2 x 3 ft", "5 x 8 ft", "8 x 10 ft"),
        _rug,
        (
            "The {name} adds texture and warmth to living spaces.",
            "The {name} grounds a room with easy, natural texture.",
        ),
    ),
    Subcat(
        "table_linen",
        "home",
        "Dining",
        12,
        ("Tablecloth",),
        35,
        120,
        "SG-TABLE-LINEN",
        ("60 x 84 in", "60 x 102 in", "60 x 120 in"),
        _table_linen,
        (
            "The {name} dresses the table for everyday meals and gatherings.",
            "The {name} brings relaxed texture to the dining table.",
        ),
    ),
)
SUBCAT_BY_KEY: dict[str, Subcat] = {s.key: s for s in SUBCATS}

RELAXED_COPY: tuple[str, ...] = (
    "Cut in a relaxed fit with extra room through the chest and shoulders, it suits broader "
    "builds and layering underneath.",
    "The relaxed fit is roomier through the chest and shoulders, made for broad-shouldered frames.",
    "Relaxed fit: more space across the chest and shoulders for a broader build.",
)
ATHLETIC_COPY = (
    "Athletic cut: tapered through the waist and trim through the chest and shoulders for a "
    "close, streamlined fit."
)
FIT_GUIDANCE_COPY: dict[str, str] = {
    "true_to_size": "Runs true to size.",
    "runs_small": "Runs small; consider sizing up.",
    "runs_large": "Runs slightly large.",
}


def care_guide_for(subcat: str, a: Record) -> str:
    material = str(a.get("material", ""))
    if subcat == "rain_shell":
        return "CG-SHELL"
    if subcat in ("insulated_jacket", "duvet"):
        return "CG-INSULATED"
    if subcat == "towel":
        return "CG-TOWELS"
    if subcat == "rug":
        return "CG-RUGS"
    if subcat == "throw":
        if "wool" in material:
            return "CG-WOOL-WASHABLE" if a.get("care") == "machine_washable" else "CG-DRYCLEAN"
        if a.get("care") == "dry_clean_only":
            return "CG-DRYCLEAN"
    if "merino" in material:
        return "CG-MERINO"
    if "lambswool" in material or "alpaca" in material:
        return "CG-DRYCLEAN"
    if "linen" in material:
        return "CG-LINEN"
    if "cotton" in material or "lyocell" in material:
        return "CG-COTTON"
    return "CG-SYNTHETICS"


def price_band(amount: Decimal) -> str:
    edges = (25, 50, 75, 100, 150, 200, 300)
    lo = 0
    for hi in edges:
        if amount < hi:
            return f"{lo}-{hi}"
        lo = hi
    return f"{lo}+"


def _sentences(
    subcat: Subcat, name: str, a: Record, fits: list[str], r: random.Random
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = [("intro", r.choice(subcat.intros).format(name=name))]
    wp = a.get("water_protection")
    if wp == "waterproof":
        taped = " with taped seams" if a.get("seam_sealed") else ""
        out.append(("water", f"It is fully waterproof{taped}."))
        if "membrane" in a:
            if "waterproof_rating_mm" in a:
                out.append(
                    (
                        "membrane",
                        f"The {a['membrane']} membrane is rated to "
                        f"{a['waterproof_rating_mm']:,} mm.",
                    )
                )
            else:
                out.append(("membrane", f"It uses a {a['membrane']} membrane."))
    elif wp == "water_resistant":
        out.append(
            (
                "water",
                "A DWR finish sheds light showers; it is water-resistant rather than waterproof.",
            )
        )
    if a.get("insulation") == "down":
        fp = f"{a['fill_power']}-fill-power " if "fill_power" in a else ""
        out.append(("insulation", f"{fp}down insulation traps heat without bulk.".capitalize()))
    elif a.get("insulation") == "synthetic":
        out.append(("insulation", "LoftCore synthetic insulation stays warm even when damp."))
    if "hood" in a:
        out.append(("hood", f"The {a['hood']} hood keeps rain off your face."))
    if "fleece_weight" in a:
        out.append(("fleece_weight", f"Made from {a['fleece_weight']}-weight fleece."))
    if "material" in a:
        out.append(("material", f"Made from {a['material']}."))
    if "gsm" in a:
        out.append(("gsm", f"The {a['gsm']} gsm fabric balances warmth, absorbency and feel."))
    if "fill" in a:
        warmth = a.get("warmth", "all-season")
        out.append(("fill", f"Filled with {a['fill']} for {warmth} comfort."))
    if "thread_count" in a:
        out.append(("thread_count", f"Woven at a {a['thread_count']} thread count."))
    if "pocket_depth_in" in a:
        d = int(a["pocket_depth_in"])
        kind = "Deep-pocket fitted sheet" if d >= 16 else "Fitted sheet"
        out.append(("pocket", f"{kind} fits mattresses up to {d} inches deep."))
    if "inseam_in" in a:
        out.append(("inseam", f"Inseam: {a['inseam_in']} in."))
    if a.get("stretch"):
        out.append(("stretch", "Four-way stretch fabric moves with you."))
    if "upf" in a:
        out.append(("upf", f"Offers UPF {a['upf']} sun protection."))
    if "gauge" in a:
        out.append(("gauge", f"A {a['gauge']}-gauge knit."))
    if "weight_g" in a:
        out.append(("weight", f"It weighs {a['weight_g']} g."))
    if a.get("packable"):
        out.append(("packable", "It packs into its own pocket."))
    if a.get("quick_dry"):
        out.append(("quick_dry", "It dries quickly between uses."))
    if "dimensions" in a:
        out.append(("dimensions", f"Measures {a['dimensions']}."))
    if a.get("care") == "machine_washable":
        out.append(("care", "Machine washable on a cold, gentle cycle."))
    elif a.get("care") == "dry_clean_only":
        out.append(("care", "Dry clean only."))
    if "washable" in a:
        out.append(
            ("care", "Machine washable for easy care." if a["washable"] else "Spot clean only.")
        )
    if "pile" in a:
        out.append(("pile", f"{a['pile'].capitalize()} pile."))
    if "relaxed" in fits:
        out.append(("fit", r.choice(RELAXED_COPY)))
    if "athletic" in fits:
        out.append(("fit", ATHLETIC_COPY))
    if "regular" in fits:
        out.append(("fit", "Also available in a regular fit." if len(fits) > 1 else "Regular fit."))
    if "fit_guidance" in a:
        out.append(("fit_guidance", FIT_GUIDANCE_COPY[a["fit_guidance"]]))
    if "country_of_origin" in a:
        out.append(("origin", f"Made in {a['country_of_origin']}."))
    return out


@dataclass
class Product:
    slot: str
    record: Record
    variants: list[Record]
    us_prices: dict[str, Decimal]  # variant_id -> US price
    zero_stock: frozenset[str]  # variant ids forced out of stock (membership checks only)
    planted: bool


def _variant(
    pid: str, sub: Subcat, colour: Colour, fit: str | None, size: str, visibility: str
) -> Record:
    size_code = re.sub(r"[^A-Z0-9]", "", size.upper())
    parts = [pid, colour.code] + ([FIT_CODES[fit]] if fit else []) + [size_code]
    vid = "-".join(parts)
    return {
        "variant_id": vid,
        "product_id": pid,
        "sku": "NW-" + vid[1:],
        "colour": colour.name,
        "colour_code": colour.code,
        "colour_family": colour.family,
        "size": size,
        "fit": fit,
        "visibility": visibility,
    }


def _size_price(sub: Subcat, base: Decimal, size: str) -> Decimal:
    if sub.category == "apparel":
        return base
    step = Decimal("0.20") * sub.sizes.index(size)
    return (base * (1 + step)).quantize(Decimal("1")) + Decimal("0.00")


def _random_sizes(sub: Subcat, r: random.Random) -> tuple[str, ...]:
    if sub.category == "apparel":
        length = r.choice([2, 3, 3, 4, 4])
        start = r.choice([0, 0, 1, 1, 2])
        end = start + length
        cap = 8 if r.random() < 0.12 else 6  # extended sizes (3XL/4XL) are uncommon
        return sub.sizes[start : min(end + (2 if cap == 8 else 0), cap)]
    if len(sub.sizes) == 1:
        return sub.sizes
    length = r.choice([k for k in (1, 2, 2, 3, 3, 4) if k <= len(sub.sizes)])
    start = r.randrange(0, len(sub.sizes) - length + 1)
    return sub.sizes[start : start + length]


def _random_price(sub: Subcat, r: random.Random) -> Decimal:
    whole = r.randrange(sub.price_lo, sub.price_hi + 1)
    cents = r.choice(["00", "00", "95", "50"])
    return Decimal(f"{whole}.{cents}")


def build_catalog(seed: int) -> list[Product]:
    products: list[Product] = []
    used_names = {p.name for p in PLANTED}
    # Random names never reuse a word from a planted name ("Ridge", "Harbor", ...), so a
    # question about a planted product can't be confused with a lookalike.
    reserved = {w for p in PLANTED for w in p.name.split()}
    places = tuple(w for w in PLACE_WORDS if w not in reserved)
    n = 0
    for sub in SUBCATS:
        planted = [p for p in PLANTED if p.subcat == sub.key]
        for spec in planted:
            n += 1
            products.append(_planted_product(seed, f"P{n:04d}", sub, spec))
        for i in range(sub.count - len(planted)):
            n += 1
            slot = f"{sub.key}#{i}"
            products.append(_random_product(seed, f"P{n:04d}", sub, slot, used_names, places))
    return products


def _base_record(
    pid: str,
    sub: Subcat,
    name: str,
    brand: str,
    gender: str | None,
    attrs: Record,
    fits: list[str],
    variants: list[Record],
    regions: list[str],
    description: str,
    r: random.Random,
    visibility: str,
    status: str,
) -> Record:
    colours: list[str] = []
    for v in variants:
        if v["colour"] not in colours:
            colours.append(v["colour"])
    sizes = [s for s in sub.sizes if any(v["size"] == s for v in variants)]
    return {
        "product_id": pid,
        "name": name,
        "brand": brand,
        "category": sub.category,
        "department": sub.department,
        "subcategory": sub.key,
        "gender": gender,
        "description": description,
        "attributes": attrs,
        "fits": fits,
        "colours": colours,
        "sizes": sizes,
        "size_guide_id": sub.size_guide,
        "care_guide_id": care_guide_for(sub.key, attrs),
        "regions": regions,
        "visibility": visibility,
        "status": status,
        "updated": (BASE_DATE - timedelta(days=r.randrange(0, 120))).isoformat(),
    }


def _with_bands(record: Record, us_prices: dict[str, Decimal]) -> Record:
    lowest = min(us_prices.values()) if us_prices else None
    bands: dict[str, str] = {}
    if lowest is not None:
        for code in record["regions"]:
            local = lowest * REGION_BY_CODE[code]["price_multiplier"]
            bands[code] = price_band(local)
    record["price_bands"] = bands
    return record


def _planted_product(seed: int, pid: str, sub: Subcat, spec: PlantedProduct) -> Product:
    r = rng(seed, "product", spec.key)
    attrs = dict(spec.attributes)
    variants: list[Record] = []
    for colour_name, fit, sizes in spec.variants:
        for size in sizes:
            variants.append(
                _variant(pid, sub, COLOUR_BY_NAME[colour_name], fit, size, spec.visibility)
            )
    fits = [f for f in FIT_ORDER if any(v["fit"] == f for v in variants)]
    sentences = _sentences(sub, spec.name, attrs, fits, r)
    text = " ".join(spec.copy.get(kind, s) for kind, s in sentences)
    if spec.visibility == "internal":
        text += f" Internal launch record, not for shoppers. Reference {canary(seed, spec.key)}."
    regions = list(REGION_CODES) if spec.visibility == "public" else []
    us_prices = {v["variant_id"]: _size_price(sub, spec.us_price, v["size"]) for v in variants}
    zero: list[str] = []
    for colour_name, fit, size in spec.zero_stock:
        for v in variants:
            if v["colour"] == colour_name and v["fit"] == fit and size in ("*", v["size"]):
                zero.append(v["variant_id"])
    record = _base_record(
        pid,
        sub,
        spec.name,
        spec.brand,
        spec.gender,
        attrs,
        fits,
        variants,
        regions,
        text,
        r,
        spec.visibility,
        spec.status,
    )
    if spec.visibility != "public":
        us_prices = {}
    return Product(
        spec.key, _with_bands(record, us_prices), variants, us_prices, frozenset(zero), True
    )


def _random_product(
    seed: int, pid: str, sub: Subcat, slot: str, used: set[str], places: tuple[str, ...]
) -> Product:
    r = rng(seed, "product", slot)
    brands = APPAREL_BRANDS if sub.category == "apparel" else HOME_BRANDS
    while True:
        brand = r.choice(brands)
        name = f"{brand} {r.choice(places)} {r.choice(sub.nouns)}"
        if name not in used:
            used.add(name)
            break
    gender = r.choice(["mens", "womens", "unisex"]) if sub.category == "apparel" else None
    attrs = sub.attrs(r)
    attrs.update(RANDOM_OVERRIDES.get(slot, {}))
    for key in RANDOM_REMOVALS.get(slot, ()):
        attrs.pop(key, None)
    n_colours = (
        r.choices([1, 2, 3], [70, 27, 3])[0]
        if sub.category == "apparel"
        else r.choices([1, 2], [60, 40])[0]
    )
    palette = (
        [c for c in COLOURS if c.family not in ("purple",)]
        if sub.category == "home"
        else list(COLOURS)
    )
    colours = r.sample(palette, n_colours)
    fit = "regular" if sub.category == "apparel" else None
    variants: list[Record] = []
    for colour in colours:
        for size in _random_sizes(sub, r):
            variants.append(_variant(pid, sub, colour, fit, size, "public"))
    fits = [fit] if fit else []
    text = " ".join(s for _, s in _sentences(sub, name, attrs, fits, r))
    regions = list(REGION_CODES) if r.random() >= 0.12 else ["US", "CA"]
    base = _random_price(sub, r)
    us_prices = {v["variant_id"]: _size_price(sub, base, v["size"]) for v in variants}
    record = _base_record(
        pid, sub, name, brand, gender, attrs, fits, variants, regions, text, r, "public", "active"
    )
    return Product(slot, _with_bands(record, us_prices), variants, us_prices, frozenset(), False)
