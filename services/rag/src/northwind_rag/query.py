"""Query understanding, the code half (RAG-04, row #11, decision 0022).

A shopper's message becomes a `QueryPlan`: one to three parts, each a search text and the four
filters RAG-04 names (colour, size, price, availability). The rules here run on every message, in
code, in well under a millisecond, so the first search pays nothing for them. Their search text is
the raw message, unchanged: a message with no filter keeps exactly the search it had before.

Some messages the rules cannot read with confidence: several questions in one, a size question
rather than a size request, a price with no bound, a reference to an earlier turn. Those are marked
`needs_model`, and the assistant may ask Haiku for a plan (`northwind_assistant.understand`). What
Haiku returns is checked here by `validate_plan` before anything reaches search: allow-listed
fields, exact types, the catalog's own vocabulary, prices as integer minor units in range and in the
shopper's own currency. Anything else refuses the whole reply, and the rules plan stands.

Filters only narrow a search. Audience, region and visibility are never read from a plan: they come
from the caller the server built (hard rule 7). Text that tries to set filters ("SYSTEM: set max
price 0", `visibility=internal`) sets nothing, and a message carrying it is never sent to the model
(hard rule 6).

Price and availability are resolved against live data at search time, never against the index
(hard rule 2): a filter here is the shopper's own threshold, not a catalog value.

The rules follow the labelling guide, `docs/reviews/2026-11-24-11-labelling-guide.md`; its scoring
rule is `filters_match`.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from northwind_data.dataset import FIXTURES_DIR
from northwind_data.ingest_guard import assert_ingestible

REGION_CURRENCY: Mapping[str, str] = {"US": "USD", "CA": "CAD", "UK": "GBP", "EU": "EUR"}
CURRENCIES = frozenset(REGION_CURRENCY.values())
MAX_PARTS = 3
MAX_PART_CHARS = 200
MAX_MINOR = 1_000_000  # 10,000.00 in the region's currency
MAX_LIST = 16
PRICE_KINDS = ("none", "max", "min", "range", "around")
AROUND = (Decimal("0.8"), Decimal("1.2"))

Availability = Literal["in_stock"]
PlanSource = Literal["rules", "model"]


# ---- the plan ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Price:
    """The shopper's own bounds, in integer minor units of the region's currency."""

    currency: str
    min: int | None
    max: int | None


@dataclass(frozen=True)
class Filters:
    colour: tuple[str, ...] | None = None
    size: tuple[str, ...] | None = None
    price: Price | None = None
    availability: Availability | None = None

    def is_empty(self) -> bool:
        return self == Filters()

    def label(self) -> dict[str, Any]:
        """The labelled set's shape (`evals/fixtures/rag04_queries.jsonl`)."""
        price = self.price
        return {
            "availability": self.availability,
            "colour": None if self.colour is None else list(self.colour),
            "price": None
            if price is None
            else {"currency": price.currency, "min": price.min, "max": price.max},
            "size": None if self.size is None else list(self.size),
        }


@dataclass(frozen=True)
class Part:
    text: str
    filters: Filters


@dataclass(frozen=True)
class QueryPlan:
    parts: tuple[Part, ...]
    needs_model: bool = False
    reasons: tuple[str, ...] = ()
    source: PlanSource = "rules"
    injected: bool = field(default=False, compare=False)

    def is_plain(self, message: str) -> bool:
        """One part, the raw message, no filter: exactly the search before RAG-04."""
        return (
            len(self.parts) == 1
            and self.parts[0].text == message
            and self.parts[0].filters.is_empty()
        )

    def label_parts(self) -> list[dict[str, Any]]:
        return [{"filters": part.filters.label()} for part in self.parts]


def _filter_key(filters: Mapping[str, Any]) -> str:
    return json.dumps(filters, sort_keys=True)


def _empty(filters: Mapping[str, Any]) -> bool:
    return all(value is None for value in filters.values())


def filters_match(
    predicted: Sequence[Mapping[str, Any]], gold: Sequence[Mapping[str, Any]]
) -> bool:
    """The guide's scoring rule: the multisets of non-empty per-part filter sets are equal. Empty
    parts are left out on both sides, so a wrong split of a question with no filter does not
    count."""

    def keys(parts: Sequence[Mapping[str, Any]]) -> Counter[str]:
        return Counter(_filter_key(p["filters"]) for p in parts if not _empty(p["filters"]))

    return keys(predicted) == keys(gold)


# ---- the vocabulary ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Vocabulary:
    """Colours, colour families and sizes, from the catalog's variants (DATA_VERSION follows)."""

    families: Mapping[str, tuple[str, ...]]
    colours: frozenset[str]
    sizes: frozenset[str]

    @classmethod
    def load(cls, fixtures_dir: Path = FIXTURES_DIR) -> Vocabulary:
        return _load_vocabulary(fixtures_dir)


@lru_cache(maxsize=4)
def _load_vocabulary(fixtures_dir: Path) -> Vocabulary:
    path = fixtures_dir / "variants.jsonl"
    assert_ingestible(path)  # the same gate as ingestion: never a live table (hard rule 2)
    families: dict[str, set[str]] = {}
    sizes: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        variant = json.loads(line)
        families.setdefault(str(variant["colour_family"]), set()).add(str(variant["colour"]))
        sizes.add(str(variant["size"]))
    return Vocabulary(
        families={family: tuple(sorted(names)) for family, names in sorted(families.items())},
        colours=frozenset(name for names in families.values() for name in names),
        sizes=frozenset(sizes),
    )


def _vocab(vocab: Vocabulary | None) -> Vocabulary:
    return vocab if vocab is not None else Vocabulary.load()


def _currency_of(region: str) -> str:
    if region not in REGION_CURRENCY:
        raise ValueError(f"unknown region: {region!r}")
    return REGION_CURRENCY[region]


# ---- injection ---------------------------------------------------------------------------------

_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
_ROLE = re.compile(r"\b(?:system|assistant|developer|admin)\s*:.*", re.IGNORECASE | re.DOTALL)
_ASSIGNMENT = re.compile(r"[\w.\-\[\]]+\s*=\s*[^\s,;]+")
_COMMAND = re.compile(r"\b(?:ignore|disregard|override|reveal|bypass|forget)\b", re.IGNORECASE)
# A sentence ends at . ! ? followed by space or the end, so "79.99" and "U.S." stay whole.
_SENTENCE = re.compile(r"(?:[^.!?\n]|[.!?](?=[^\s.!?]))+[.!?]*")


def strip_injection(message: str) -> tuple[str, bool]:
    """The message without text aimed at the system: comments, role labels to the end, `key=value`
    settings, and sentences that tell anyone to ignore, override or reveal. Whether any was
    found."""
    text = _COMMENT.sub(" ", message)
    text = _ROLE.sub(" ", text)
    text = _ASSIGNMENT.sub(" ", text)
    kept = [s for s in _SENTENCE.findall(text) if not _COMMAND.search(s)]
    clean = " ".join(" ".join(kept).split())
    injected = clean != " ".join(message.split())
    return clean, injected


# ---- colour ------------------------------------------------------------------------------------

# Exact synonyms of one catalog name (the guide: "navy blue" is Navy, not the blue family).
_COLOUR_SYNONYMS: Mapping[str, str] = {
    "navy blue": "Navy",
    "glacier blue": "Glacier Blue",
    "heather grey": "Heather Grey",
    "heather gray": "Heather Grey",
    "off-white": "Ivory",
    "off white": "Ivory",
    "cream": "Ivory",
    "forest green": "Forest",
    "sage green": "Sage",
}
_FAMILY_WORDS: Mapping[str, str] = {
    "blue": "blue",
    "blues": "blue",
    "grey": "grey",
    "gray": "grey",
    "greys": "grey",
    "grays": "grey",
    "green": "green",
    "greens": "green",
    "red": "red",
    "reds": "red",
    "brown": "brown",
    "browns": "brown",
    "beige": "beige",
    "orange": "orange",
    "purple": "purple",
    "yellow": "yellow",
}
# Catalog names that are also ordinary words: a colour only next to a product, after "in", or
# capitalised inside a sentence.
_AMBIGUOUS_NAMES = frozenset(
    {"forest", "sage", "sand", "clay", "rust", "slate", "charcoal", "oat", "plum"}
)
_PRODUCT_NOUNS = frozenset(
    """jacket jackets fleece fleeces shirt shirts top tops tee tees pants trousers cardigan
    cardigans sweater sweaters jumper jumpers hoodie hoodies vest vests shell shells parka parkas
    puffer puffers leggings shorts coat coats layer layers base rug rugs throw throws sheet sheets
    duvet duvets towel towels tablecloth tablecloths napkin napkins quilt quilts blanket blankets
    pillow pillows pillowcase pillowcases cover covers set sets one ones option options knit
    knitwear beanie hat""".split()
)
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def _words(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text)]


def _sentence_start(text: str, start: int) -> bool:
    before = text[:start].rstrip()
    return not before or before[-1] in ".!?:\n"


def _colours(text: str, vocab: Vocabulary) -> tuple[str, ...] | None:
    low = text.lower()
    found: set[str] = set()
    masked = list(low)
    for phrase, name in sorted(_COLOUR_SYNONYMS.items(), key=lambda kv: -len(kv[0])):
        for m in re.finditer(rf"\b{re.escape(phrase)}\b", low):
            if any(c == "\0" for c in masked[m.start() : m.end()]):
                continue
            found.add(name)
            masked[m.start() : m.end()] = "\0" * (m.end() - m.start())
    low = "".join(masked)
    names = {name.lower(): name for name in vocab.colours}
    words = _words(text)
    for i, (word, start, end) in enumerate(words):
        if "\0" in low[start:end]:
            continue
        key = word.lower()
        if key in names and " " not in names[key]:
            if key in _AMBIGUOUS_NAMES and not _ambiguous_is_colour(text, words, i):
                continue
            found.add(names[key])
        elif key in _FAMILY_WORDS:
            found.update(vocab.families.get(_FAMILY_WORDS[key], ()))
    return tuple(sorted(found)) or None


def _ambiguous_is_colour(text: str, words: list[tuple[str, int, int]], i: int) -> bool:
    word, start, _ = words[i]
    after = words[i + 1][0].lower() if i + 1 < len(words) else ""
    before = words[i - 1][0].lower() if i > 0 else ""
    if after in _PRODUCT_NOUNS:
        return True
    if before == "in" and after not in {"the", "a"}:
        return True
    return word[0].isupper() and not _sentence_start(text, start)


# ---- size --------------------------------------------------------------------------------------

_LETTER_SIZES: Mapping[str, str] = {
    "XS": "XS",
    "S": "S",
    "M": "M",
    "L": "L",
    "XL": "XL",
    "XXL": "2XL",
    "2XL": "2XL",
    "2X": "2XL",
    "XXXL": "3XL",
    "3XL": "3XL",
    "3X": "3XL",
    "4XL": "4XL",
    "4X": "4XL",
}
_LETTER = re.compile(r"(?<![\w'’\-$£€])(XXXL|XXL|XS|XL|2XL|3XL|4XL|2X|3X|4X|S|M|L)(?![\w'’\-])")
_LETTER_CI = re.compile(r"(?<![\w'’\-$£€])(xxxl|xxl|xs|xl|2xl|3xl|4xl)(?![\w'’\-])", re.IGNORECASE)
_SIZE_WORDS: Mapping[str, str] = {
    "extra small": "XS",
    "extra-small": "XS",
    "extra large": "XL",
    "extra-large": "XL",
    "x-large": "XL",
    "small": "S",
    "medium": "M",
    "large": "L",
}
_APPAREL_NOUNS = frozenset(
    """jacket jackets fleece fleeces shirt shirts top tops tee tees pants trousers cardigan
    cardigans sweater sweaters jumper jumpers hoodie hoodies vest vests shell shells parka parkas
    puffer puffers leggings shorts coat coats base""".split()
)
_BED_NOUNS = frozenset(
    """sheet sheets duvet duvets comforter comforters quilt quilts bed beds mattress mattresses
    bedding pillowcase pillowcases coverlet coverlets topper toppers set sets size sized
    cover covers""".split()
)
_DIMENSIONS = re.compile(
    r"(?<![\d.])(\d{1,3})\s*(?:'|ft|feet|foot|in|inch|inches|\")?\s*(?:x|×|by)\s*(\d{1,3})"
    r"(?:\s*(ft|feet|foot|'|in\b|inch|inches|\"))?",
    re.IGNORECASE,
)
_TABLECLOTH = re.compile(
    r"(?<![\d.x×])\b(84|102|120)\s*-?\s*(?:inch|inches|in\b|\")", re.IGNORECASE
)
_TOWEL = re.compile(r"\b(bath|hand)[ -](towel|sheet)s?\b", re.IGNORECASE)
_TOWEL_SIZE = {
    ("bath", "towel"): "Bath Towel",
    ("hand", "towel"): "Hand Towel",
    ("bath", "sheet"): "Bath Sheet",
}
_CAL_KING = re.compile(r"\b(?:california|cal\.?)\s+king\b", re.IGNORECASE)


def _towel_is_a_name(text: str, start: int) -> bool:
    """'Orchard Bath Towel': capitalised and after a capitalised word, a product's name."""
    if not text[start].isupper():
        return False
    before = text[:start].rstrip().split()
    return bool(before) and before[-1][:1].isupper() and not _sentence_start(text, start)


def _sizes(text: str, vocab: Vocabulary) -> tuple[str, ...] | None:
    found: set[str] = set()
    for m in _TOWEL.finditer(text):
        if _towel_is_a_name(text, m.start()):
            continue
        size = _TOWEL_SIZE[(m.group(1).lower(), m.group(2).lower())]
        if size in vocab.sizes:
            found.add(size)
    for m in _DIMENSIONS.finditer(text):
        a, b, unit = m.group(1), m.group(2), (m.group(3) or "").lower()
        inches = unit in {"in", "inch", "inches", '"'}
        for candidate in (
            (f"{a} x {b} in", f"{a} x {b} ft")
            if inches
            else (
                f"{a} x {b} ft",
                f"{a} x {b} in",
            )
        ):
            if candidate in vocab.sizes:
                found.add(candidate)
                break
    for m in _TABLECLOTH.finditer(text):
        candidate = f"60 x {m.group(1)} in"
        if candidate in vocab.sizes:
            found.add(candidate)
    for m in _LETTER.finditer(text):
        found.add(_LETTER_SIZES[m.group(1)])
    for m in _LETTER_CI.finditer(text):
        found.add(_LETTER_SIZES[m.group(1).upper()])
    low = text.lower()
    words = _words(low)
    for phrase, size in sorted(_SIZE_WORDS.items(), key=lambda kv: -len(kv[0])):
        for m in re.finditer(rf"\b{re.escape(phrase)}\b", low):
            if _size_word_counts(words, m.start(), m.end()):
                found.add(size)
                low = low[: m.start()] + "\0" * (m.end() - m.start()) + low[m.end() :]
                words = _words(low)
    cal_king = bool(_CAL_KING.search(text))
    # "Full" and "double" are ordinary words, so they need bedding in the message.
    bed_sentence = any(
        w in _BED_NOUNS - {"set", "sets", "size", "sized", "cover", "covers"} for w, _, _ in words
    )
    for i, (word, _, _) in enumerate(words):
        nearby = {w for w, _, _ in words[i + 1 : i + 3]} | ({words[i - 1][0]} if i > 0 else set())
        if word in {"queen", "queens"}:
            found.add("Queen")
        elif word in {"king", "kings"} and not cal_king:
            found.add("King")
        elif word == "twin" and not (nearby & {"pack", "set", "of"} - _BED_NOUNS):
            found.add("Twin")
        elif word in {"full", "double"} and (nearby & _BED_NOUNS or bed_sentence):
            found.add("Full")
    return tuple(sorted(s for s in found if s in vocab.sizes)) or None


def _size_word_counts(words: list[tuple[str, int, int]], start: int, end: int) -> bool:
    before = [w for w, _, e in words if e <= start][-2:]
    after = [w for w, s, _ in words if s >= end][:1]
    if before and before[-1] in {"run", "runs", "running", "fit", "fits", "too", "very", "so"}:
        return False
    if before and before[-1] in {"in", "size"}:
        return True
    if len(before) == 2 and before[-1] in {"or", "and"} and before[-2] in _SIZE_WORDS:
        return True  # an alternative after a size: "size small or medium"
    if before[-2:] == ["in", "a"] or before[-2:] == ["a", "size"]:
        return True
    return bool(after) and (after[0] in _APPAREL_NOUNS or after[0] == "size")


_SIZE_QUESTION = re.compile(
    r"\b(?:what|which)\b[^.?!]*\bsizes?\b[^.?!]*\b(?:fit|fits|need|get|buy|pick|choose|for)\b"
    r"|\bsize does\b|\bdoes (?:a|an|the) \w+(?: \w+)? fit\b|\bwhat size\b|\bwhich size\b",
    re.IGNORECASE,
)


# ---- price -------------------------------------------------------------------------------------

_AMOUNT = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_PREFIX = r"US\$|CA\$|CAD\$|C\$|\$|£|€"
_SUFFIX = (
    r"canadian dollars?|us dollars?|dollars?|bucks|usd|cad|pounds? sterling|pounds?|quid|gbp|"
    r"euros?|eur|€|£"
)
_MONEY = re.compile(
    rf"(?P<pre>{_PREFIX})\s?(?P<amt>{_AMOUNT})(?:\s?(?P<suf>{_SUFFIX})\b)?"
    rf"|(?<![\w.,$£€])(?P<amt2>{_AMOUNT})\s?(?P<suf2>{_SUFFIX})(?:\b|(?<=[€£]))",
    re.IGNORECASE,
)
_RANGE = re.compile(
    rf"(?:\bbetween\s+|\bfrom\s+)?(?P<a_pre>{_PREFIX})?\s?(?P<a>{_AMOUNT})\s?(?P<a_suf>{_SUFFIX})?"
    rf"\s*(?:-|–|—|\bto\b|\band\b)\s*(?P<b_pre>{_PREFIX})?\s?(?P<b>{_AMOUNT})\s?(?P<b_suf>{_SUFFIX})?",
    re.IGNORECASE,
)
_MAX_WORDS = re.compile(
    r"(?:\bunder|\bbelow|\bless than|\bup to|\bno more than|\bnot more than|\bat most|"
    r"\bmax(?:imum)?|\bwithin (?:a |my |the )?budget of|\bbudget of|\bcheaper than|"
    r"\bunder about|\bbelow about)\s*$",
    re.IGNORECASE,
)
_MIN_WORDS = re.compile(
    r"(?:\bover|\babove|\bmore than|\bat least|\bfrom|\bmin(?:imum)?|\bstarting (?:at|from)|"
    r"\bupwards of)\s*$",
    re.IGNORECASE,
)
_AROUND_WORDS = re.compile(
    r"(?:\baround|\babout|\broughly|\bapproximately|\bapprox\.?|~)\s*$", re.IGNORECASE
)
_NOT_PRICE_CONTEXT = re.compile(
    r"\b(?:shipping|delivery|deliver|delivered|postage|return fee|returns? shipping|ship free|"
    r"free returns?)\b",
    re.IGNORECASE,
)
_BARE_BOUND = re.compile(
    r"\b(?:under|below|less than|up to|no more than|at most|over|above|more than|at least|"
    r"around|about|between)\s+(\d+(?:\.\d+)?)(?!\s*(?:-|to|and)?\s*\d)\s*(?P<unit>[a-z%\"']+)?",
    re.IGNORECASE,
)


def _currency(pre: str | None, suf: str | None, region: str) -> str | None:
    """The stated currency, or None when it is not the region's (never converted)."""
    pre = (pre or "").upper()
    suf = (suf or "").lower()
    own = REGION_CURRENCY[region]
    stated: str | None
    if pre in {"US$"} or suf in {"usd", "us dollar", "us dollars"}:
        stated = "USD"
    elif pre in {"CA$", "CAD$", "C$"} or suf in {"cad", "canadian dollar", "canadian dollars"}:
        stated = "CAD"
    elif pre == "£" or suf in {
        "£",
        "gbp",
        "quid",
        "pound",
        "pounds",
        "pound sterling",
        "pounds sterling",
    }:
        stated = "GBP"
    elif pre == "€" or suf in {"€", "eur", "euro", "euros"}:
        stated = "EUR"
    elif pre == "$" or suf in {"dollar", "dollars", "bucks"}:
        stated = {"US": "USD", "CA": "CAD"}.get(region)
    else:
        stated = None
    return stated if stated == own else None


def _minor(amount: str) -> int | None:
    try:
        value = Decimal(amount.replace(",", ""))
    except InvalidOperation:
        return None
    minor = value * 100
    if not value.is_finite() or minor != minor.to_integral_value():
        return None
    result = int(minor)
    return result if 0 < result <= MAX_MINOR else None


def _whole(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP)) * 100


def _around(minor: int) -> tuple[int | None, int | None]:
    """X ± 20%, in whole units; a bound outside 0 < x <= MAX_MINOR is left open, so every path
    (the gateway's live filter and the eval planner) reads the same price (security review L4)."""
    units = Decimal(minor) / 100
    low, high = _whole(units * AROUND[0]), _whole(units * AROUND[1])
    return (low if low > 0 else None), (min(high, MAX_MINOR) if high > 0 else None)


@dataclass(frozen=True)
class _PriceRead:
    price: Price | None
    unbounded: bool = False  # money with no bound word: for the model


def _price(text: str, region: str) -> _PriceRead:
    own = REGION_CURRENCY[region]
    reads: list[Price] = []
    unbounded = False
    for sentence in _SENTENCE.findall(text):
        if _NOT_PRICE_CONTEXT.search(sentence):
            continue
        taken: list[tuple[int, int]] = []
        for m in _RANGE.finditer(sentence):
            pre_a, suf_a, pre_b, suf_b = (
                m.group("a_pre"),
                m.group("a_suf"),
                m.group("b_pre"),
                m.group("b_suf"),
            )
            if not (pre_a or suf_a or pre_b or suf_b):
                continue
            if not re.match(r"\s*(?:between|from)\b", m.group(0), re.IGNORECASE) and not (
                pre_a or suf_a
            ):
                # "50 and 80 dollars" with no opener is ambiguous ("2 jackets and 80 dollars").
                if not re.search(r"\b(?:between|from)\s*$", sentence[: m.start()], re.IGNORECASE):
                    continue
            currency = (
                _currency(pre_a or pre_b, suf_b or suf_a, region)
                if (pre_a or pre_b or suf_a or suf_b)
                else None
            )
            taken.append((m.start(), m.end()))
            if currency is None:
                reads.append(Price("", None, None))  # stated, but foreign: no filter
                continue
            low, high = _minor(m.group("a")), _minor(m.group("b"))
            if low is None or high is None or low > high:
                continue
            reads.append(Price(currency, low, high))
        for m in _MONEY.finditer(sentence):
            if any(s <= m.start() < e for s, e in taken):
                continue
            amount = m.group("amt") or m.group("amt2")
            currency = _currency(m.group("pre"), m.group("suf") or m.group("suf2"), region)
            before = sentence[: m.start()]
            after = sentence[m.end() : m.end() + 6].lower()
            if currency is None:
                reads.append(Price("", None, None))
                continue
            minor = _minor(amount)
            if minor is None:
                continue
            if _MAX_WORDS.search(before) or after.startswith(
                (" or less", " max", " tops", " or under")
            ):
                reads.append(Price(currency, None, minor))
            elif _MIN_WORDS.search(before) or after.startswith(
                (" or more", " plus", "+", " and up")
            ):
                reads.append(Price(currency, minor, None))
            elif _AROUND_WORDS.search(before) or after.startswith(("-ish", "ish", " or so")):
                low, high = _around(minor)
                if low is not None or high is not None:
                    reads.append(Price(currency, low, high))
            else:
                unbounded = True
    real = [r for r in reads if r.currency]
    if len(real) > 1:
        return _PriceRead(None, unbounded=True)  # two prices: two asks, for the model
    if not real:
        return _PriceRead(None, unbounded=unbounded)
    if real[0].currency != own:  # `_currency` returns only the region's own
        return _PriceRead(None, unbounded=unbounded)
    return _PriceRead(real[0], unbounded=unbounded)


def _bare_bound(text: str) -> bool:
    """A bound on a number with no unit at all ("under 100"): money or not, the model decides."""
    for m in _BARE_BOUND.finditer(text):
        if not m.group("unit"):
            return True
    return False


# ---- availability ------------------------------------------------------------------------------

_IN_STOCK = re.compile(
    r"\bin[- ]stock\b|\bavailable (?:right now|now|today|immediately)\b|"
    r"\b(?:currently|presently) available\b|\bavailable at the moment\b|"
    r"\b(?:can|could) (?:i |we )?(?:get|buy|order) (?:it |them |one |these )?"
    r"(?:right )?(?:now|today)\b|"
    r"\b(?:not|aren'?t|isn'?t|are not|is not) sold out\b|\bready to ship\b|"
    r"\bships? (?:now|today|immediately)\b|\bthat i can get (?:right )?now\b|\bin-stock\b",
    re.IGNORECASE,
)
_NOT_A_FILTER = re.compile(
    r"\bback in stock\b|\brestock|\bwhen will\b|\bwhen is\b|\bwhen do\b|\bout of stock\b",
    re.IGNORECASE,
)
_PRONOUN_QUESTION = re.compile(
    r"^\s*(?:is|are|will|does|do)\s+(?:it|they|this|that|these|those)\b", re.IGNORECASE
)


@lru_cache(maxsize=1)
def _brands(fixtures_dir: Path = FIXTURES_DIR) -> tuple[str, ...]:
    path = fixtures_dir / "products.jsonl"
    assert_ingestible(path)
    brands = {
        str(json.loads(line)["brand"]).split()[0]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    return tuple(sorted(brands))


@lru_cache(maxsize=1)
def _product_name_pattern(fixtures_dir: Path = FIXTURES_DIR) -> re.Pattern[str]:
    """Each catalog product a shopper can see as its brand and model word ("Corvane Ridge",
    "Halvardine Home Orchard"), from the catalog itself (row #11, the named-product exception).
    Internal products give no key, so the exception is not an existence oracle for their names
    (security H2-L1, hard rule 7)."""
    path = fixtures_dir / "products.jsonl"
    assert_ingestible(path)
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        product = json.loads(line)
        if product.get("visibility") != "public":
            continue
        brand, name = str(product["brand"]), str(product["name"])
        rest = name[len(brand) :].split() if name.startswith(brand) else []
        if rest:
            words = [*brand.split(), rest[0]]
            keys.add(r"\s+".join(re.escape(w) for w in words))
    return re.compile(rf"\b(?:{'|'.join(sorted(keys))})\b", re.IGNORECASE)


def names_catalog_product(text: str) -> bool:
    """Whether the message names a catalog product (brand and model word), by code, never by a
    model. Such a message runs the plain first search (decision 0022, the named-product exception):
    narrowing a named product's variant question drops the variant chunks that answer it."""
    return bool(_product_name_pattern().search(text))


def _names_a_product(text: str) -> bool:
    return any(re.search(rf"\b{re.escape(brand)}\b", text) for brand in _brands())


def _availability(text: str) -> Availability | None:
    if not _IN_STOCK.search(text):
        return None
    if _NOT_A_FILTER.search(text) or _PRONOUN_QUESTION.search(text) or _names_a_product(text):
        return None
    return "in_stock"


# ---- several asks, or one that needs the conversation ------------------------------------------

_JOINERS = re.compile(
    r"\b(?:also|plus|additionally|as well as|separately|two things|three things)\b|;|"
    r"(?:^|[.!?]\s+)(?:and|then)\b|\band (?:a|an|some)\s+(?!few\b|bit\b|lot\b)",
    re.IGNORECASE,
)
_FOLLOW_UP = re.compile(r"^\s*(?:in|and|or|what about|how about|any|also|same)\b", re.IGNORECASE)


_GOODS_AND_GOODS = re.compile(r"\b([a-z]+)\s+and\s+(?:[a-z]+\s+)?([a-z]+)\b", re.IGNORECASE)


def _two_goods(text: str) -> bool:
    """Two kinds of goods joined by "and" ("fleeces and base layers"): two asks, which may share
    a filter stated once (the guide)."""
    for m in _GOODS_AND_GOODS.finditer(text):
        first, second = m.group(1).lower(), m.group(2).lower()
        if first in _PRODUCT_NOUNS and second in _PRODUCT_NOUNS and first != second:
            return True
    return False


def _multi_part(text: str) -> bool:
    return text.count("?") >= 2 or bool(_JOINERS.search(text)) or _two_goods(text)


def _follow_up(text: str) -> bool:
    return len(text.split()) <= 5 and bool(_FOLLOW_UP.search(text))


# ---- the rules -----------------------------------------------------------------------------------


def rules_plan(message: str, region: str, vocab: Vocabulary | None = None) -> QueryPlan:
    """The plan from code alone. Its search text is the raw message (plan (1)); `needs_model` says
    whether a model could read it better. A message carrying injected text never goes to the
    model."""
    _currency_of(region)
    vocab = _vocab(vocab)
    clean, injected = strip_injection(message)
    price = _price(clean, region)
    filters = Filters(
        colour=_colours(clean, vocab),
        size=_sizes(clean, vocab),
        price=price.price,
        availability=_availability(clean),
    )
    reasons: list[str] = []
    if _multi_part(clean):
        reasons.append("multi_part")
    if _SIZE_QUESTION.search(clean):
        reasons.append("size_question")
    if price.unbounded or _bare_bound(clean):
        reasons.append("unbounded_price")
    if _follow_up(clean):
        reasons.append("follow_up")
    needs_model = bool(reasons) and not injected
    return QueryPlan(
        parts=(Part(message, filters),),
        needs_model=needs_model,
        reasons=tuple(reasons),
        source="rules",
        injected=injected,
    )


# ---- checking a model's plan ---------------------------------------------------------------------

_PART_KEYS = frozenset(
    {"text", "colours", "sizes", "price_kind", "amount", "amount_to", "currency", "in_stock_only"}
)
_PLAN_KEYS = frozenset({"parts"})
_DECIMAL = re.compile(r"\d{1,7}(?:\.\d{1,2})?", re.ASCII)
# Anything card-shaped (hard rule 1), screened here without `northwind_common`, which this package
# must not depend on: 12 or more digits whatever separates them, or a card word. A search part never
# needs either; the assistant also applies `northwind_common.cards` (row #11, test-writer F3).
_CARD_WORDS = re.compile(
    r"\b(?:c\s*v\s*v2?|c\s*v\s*c2?|cvn|cid|csc|security\s+code|card\s+(?:code|number|no)|"
    r"expiry|expiration|exp\.?\s+date)\b",
    re.IGNORECASE,
)


def card_shaped(text: str) -> bool:
    return sum(ch.isdigit() for ch in text) >= 12 or bool(_CARD_WORDS.search(text))


def _strings(value: Any) -> list[str] | None:
    if not isinstance(value, list) or len(value) > MAX_LIST:
        return None
    if not all(isinstance(v, str) and 0 < len(v) <= 32 for v in value):
        return None
    return list(value)


def _checked_amount(value: Any) -> int | None | Literal[False]:
    """Integer minor units, None for an empty field, False for anything malformed."""
    if not isinstance(value, str):
        return False
    if value == "":
        return None
    if not _DECIMAL.fullmatch(value):
        return False
    minor = _minor(value)
    return False if minor is None else minor


def _checked_part(raw: Any, region: str, vocab: Vocabulary) -> Part | None:
    if not isinstance(raw, dict) or set(raw) != _PART_KEYS:
        return None
    text = raw["text"]
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > MAX_PART_CHARS
        or "\x00" in text
        or card_shaped(text)
    ):
        return None
    colours, sizes = _strings(raw["colours"]), _strings(raw["sizes"])
    if colours is None or sizes is None:
        return None
    names: set[str] = set()
    for colour in colours:
        if colour in vocab.colours:
            names.add(colour)
        elif colour.lower() in vocab.families:
            names.update(vocab.families[colour.lower()])
        else:
            return None
    if any(size not in vocab.sizes for size in sizes):
        return None
    if not isinstance(raw["in_stock_only"], bool):
        return None
    kind, currency = raw["price_kind"], raw["currency"]
    if kind not in PRICE_KINDS or not isinstance(currency, str):
        return None
    if currency not in CURRENCIES and currency not in {"", "none", "other"}:
        return None
    amount, amount_to = _checked_amount(raw["amount"]), _checked_amount(raw["amount_to"])
    if amount is False or amount_to is False:
        return None
    price: Price | None = None
    if kind != "none":
        if amount is None or (kind == "range") != (amount_to is not None):
            return None
        if kind == "range" and amount_to is not None and amount > amount_to:
            return None
        if currency == REGION_CURRENCY[region]:
            if kind == "max":
                price = Price(currency, None, amount)
            elif kind == "min":
                price = Price(currency, amount, None)
            elif kind == "range":
                price = Price(currency, amount, amount_to)
            else:
                low, high = _around(amount)
                price = None if low is None and high is None else Price(currency, low, high)
    filters = Filters(
        colour=tuple(sorted(names)) or None,
        size=tuple(sorted(set(sizes))) or None,
        price=price,
        availability="in_stock" if raw["in_stock_only"] else None,
    )
    return Part(text, filters)


def validate_plan(raw: Any, region: str, vocab: Vocabulary | None = None) -> QueryPlan | None:
    """A model's plan, if every field passes; otherwise None, and the rules plan stands."""
    _currency_of(region)
    vocab = _vocab(vocab)
    if not isinstance(raw, dict) or set(raw) != _PLAN_KEYS:
        return None
    parts = raw["parts"]
    if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS:
        return None
    checked = [_checked_part(part, region, vocab) for part in parts]
    if any(part is None for part in checked):
        return None
    return QueryPlan(parts=tuple(p for p in checked if p is not None), source="model")


# ---- which indexes a message asks about (RAG-07) -------------------------------------------------

Index = Literal["policy", "reviews", "specifications"]
ALL_INDEXES: frozenset[Index] = frozenset({"policy", "reviews", "specifications"})


def _cue(table: str) -> re.Pattern[str]:
    """Whole words or phrases, one or more alternatives per line. Every gap is bounded."""
    return re.compile(rf"\b(?:{'|'.join(table.split(chr(10)))})\b", re.IGNORECASE)


# Text aimed at the system. Stricter than `strip_injection`, whose command words alone also match
# "don't forget" and "ignore the care label": here a command needs something of the system's.
_SYSTEM_TEXT = re.compile(
    r"<!--|<\|?/?(?:system|im_start|tool|script)|\[/?(?:system|inst)\b|\b\w{3,30}=\S"
    r"|\b(?:system|assistant|developer|admin|ai|bot)\]?\s?:|\b(?:ignore|disregard|override|bypass|"
    r"forget|break) (?:(?:your|all|any|of|the|previous|prior|above|earlier|system|safety) ){0,5}"
    r"(?:instructions?|prompts?|guidelines|guardrails|restrictions|rules|filters?)\b"
    r"|\b(?:ignore|disregard|override|bypass|forget) (?:your|previous|prior|the above|everything|"
    r"(?:what|whatever|anything|all) you)\b|\byou (?:were|are|have been) told\b"
    r"|\b(?:speak|answer|respond|reply|behave) as (?:an? |the )?(?:owner|admin\w*|manager|"
    r"developer|engineer|system)\b|\byour (?:system |hidden |initial |original |secret )?(?:prompt|"
    r"instructions(?! (?:for|on)))\b"
    r"|\bsystem (?:prompt|message|note|override)\b|\bnew instructions\b|\byou are now\b|jailbreak"
    r"|\b(?:developer|debug|admin|god) mode\b|\bpretend (?:to be|you)\b|\bfrom now on you\b"
    r"|\b(?:note to|attention,?) (?:the )?(?:ai|assistant|bot|model|system)\b"
    r"|\b(?:assistant|model|ai|system|bot) (?:must|shall|is now)\b|(?:^|[.!?;:,] )act as an? \w"
    r"|(?:^|[.!?;:] )set (?:the |your )?(?:max |min )?(?:price|visibility|audience|role|filter)",
    re.IGNORECASE,
)
# Who asks, from where and in what currency never changes the answer (guide, rule 9).
_CALLER_WORDS = re.compile(
    r"\b(?i:united states|united kingdom|european union|canada|canadian|internal|public|staff|"
    r"employees?|confidential|visibility)\b|\b(?:USA?|UK|EU|CA|USD|CAD|GBP|EUR)\b"
)
# Words typical of one index, used for something else.
_NEUTRAL = re.compile(
    r"\bheavy[- ]duty\b|\breturning (?:customer|shopper|favou?rite)\b|\breturn (?:trip|flight)\b|"
    r"\bin order to\b|\bsoft[- ]?shell\b|\bsweat[- ]wick\w*|\bfeel free\b|\bworth (?=paying)|"
    r"(?<=worth )the (?:price|money|cost)\b|"
    r"\bpaying for (?:an? |the )?(?:\w+ )?(?:label|name|brand|logo)\b",
    re.IGNORECASE,
)
_RATED = re.compile(
    r"\b(?:waterproof\w*|water|warmth|temp\w*|upf|uv|sun|fill|mm|wind\w*)(?:[- ][^,.;?!]{0,40})?"
    r"\b(?:rating|rated)\b|\brated (?:to|for|at|waterproof)\b",
    re.IGNORECASE,
)

_GOODS = _cue(
    "|".join(sorted(_PRODUCT_NOUNS - set("one ones option options set sets cover covers".split())))
    + "|bedding|linens|comforters?|coverlet|outerwear|merino|cashmere|sherpa|clothes|raincoat"
    "|overshirts?|pullovers?|crewneck|thermals?"
)
_POLICY = _cue(
    r"""return(?:s|ed|ing|able)?|refund\w*|exchang\w*|swap\w*|money[- ]back|store credit
(?:send|sent|sending|ship|take|post)\b[^,.;?!]{0,30}\bback|final sale|change (?:of|my) mind
unworn|(?:go|take|give|bring|get) (?:it |them |this |one )?back|ship(?:s|ped|ping|ments?)?
deliver\w*|postage|couriers?|parcels?|packages?|dispatch\w*|arriv(?:e|es|ed|ing|al)|tracking
track (?:my|an?)|how soon|(?:get|come) (?:it |them )?(?:here|to me|by|in time)|in time for|here by
po box(?:es)?|express|next[- ]day|send to|address|(?:my|an|the|your|our|last|placed) orders?
orders? (?:number|status)|ordered|cancel\w*|where(?:'s|s| is) my|checkout
pay(?:ing|ments?|pal)?|paid|klarna|instal+ments?|what cards|invoices?
(?:gift|credit|debit)[- ](?:cards?|receipts?|wrap\w*)|receipt|vouchers?|coupons?|promo\w*
discounts?|clearance|sales|sale (?:ends?|starts?|items?|price)|(?:next|your|big) sale
black friday|redeem|(?:what(?:'s|s| is)|anything) on sale|tax(?:es|ed)?|vat|duty|duties
customs|import fees?|fees?|charged?|billing|warrant\w*|guarantees?|repair\w*
price[- ]?(?:match|adjust)\w*|match (?:a |the )?price
polic(?:y|ies)(?! (?:on|for|about) (?:wash|clean|dry|iron))
(?:your|current|all|see|online) prices|prices (?:change|differ|vary|include)
terms and conditions|loyalty|newsletter|my account|log ?in
(?:things?|items?|something|stuff|products) [^,.;?!]{0,20}\b(?:costs?|price[sd]?|stock)
password|privacy|contact (?:you|customer|someone|support|us|details)
customer (?:care|service|support)|phone number|live chat|opening hours|in[- ]store
(?:visit|collect)\b[^,.;?!]{0,30}\b(?:stores?|shops?)|shop i can visit
(?:speak|talk|chat) (?:to|with)|real (?:person|human)|a (?:human|robot|bot)|your team
are you (?:an? )?(?:ai|bot|human|person)|faulty|defective|damaged
replac(?:e|ed|ement)|(?:doesn't|don't) suit
(?:hasn't|haven't|never|not) (?:come|turned up|shown up)|missing (?:item|part|button|parcel)
wrong (?:item|one|size|colou?r|product)|fix(?:es|ed|ing)?|mend(?:s|ed|ing)?|cards?
porch|doorstep|neighbou?rs?|sign(?:s|ed|ing|ature)? for|left (?:on|at|with|outside|by)
(?:be|is|am|are) (?:i |we |it |they )?covered|(?:get|send) (?:it|them|this|one) to|be with (?:me|us)
(?:get|come|reach|have|here|make)\b[^,.;?!]{0,30}\b(?:by|before) (?:the weekend|\w+day)"""
)
# Reviews as a feature of the shop, "review" as a verb, a formal complaint.
_REVIEW_POLICY = _cue(
    r"""(?:leave|write|post|submit|edit|delete|remove|add)\b[^,.;?!]{0,20}\breviews?
review (?:my|our|this|the|an?) (?:order|refund|return|request|case|claim|product|item)
my reviews?|reviews? (?:policy|real|fake|verified|moderated|checked|appear|published)
under review|(?:i|i've) reviewed|(?:where|how) (?:do|can|should) i (?:go to )?(?:rate|review)
(?:make|file|raise) a complaint"""
)
_WHO = (
    r"(?:people|customers?|shoppers?|buyers?|owners?|wearers?|users|folks?|others|every(?:one|body)|"
    r"any(?:one|body)|they|families|parents)\b[^,.;?!]{0,30}?\b"
)
_VOICES = _cue(
    rf"""reviews?|reviewers?|reviewed|testimonials?|feedback|ratings?|rated|stars?|opinions?
complaints?|thoughts|consensus|verdict|according to|(?:has|have) any(?:one|body)
how (?:happy|satisfied) (?:are|were)|what (?:bug|bugs|bugged|annoy\w*|put|puts) (?:them|people|you)
why (?:do|did|are|have) {_WHO}(?:send|sending|return\w*|give up|dislike)
(?:best|most|well|highly)[- ]?(?:loved|liked|recommended)
{_WHO}(?:say|said|saying|think|thinks|thought|find|finds|found|complain\w*|mention\w*
report\w*|notic\w*|feel|felt|rate|recommend\w*|prefer|regret\w*|experienc\w*|tried|happy
unhappy|satisfied|disappointed|impressed|pleased|reckon|rave|love|hate|agree)"""
)
# Only when nothing in the clause is about the shop ("do customers have to pay for returns").
_WEAK_VOICES = _cue(
    rf"""{_WHO}(?:likes?|liked|had|has|have(?! to)|owns?|owned|bought|buy|wears?|wore|worn|uses?
used|washed|enjoy\w*|actually|stay\w*)"""
)
_OPINION_PICK = _cue(
    r"(?:best|top|highest|well|better|most|highly)[- ]?(?:reviewed|rated|loved|liked)"
)
_RUNS = _cue(
    r"""run(?:s|ning)? (?:(?:a|bit|little|slightly|quite|very|too|on|the) ){0,5}(?:small|smaller
large|larger|big|bigger|tight|short|narrow|wide|long|loose|snug|true)
com(?:e|es|ing) up (?:a bit |quite |very |too )?(?:small|large|big|short|long)
fits? (?:a bit |quite )?(?:small|large|big|true)(?= ?(?:$|[?.!,]|or\b))|true[- ]to[- ]size|tts
sizing (?:off|accurate|run|runs|true)"""
)
_EXPERIENCE = _cue(
    r"""com(?:fy|fier|fiest)|(?:un)?comfort(?:able|ably)?|co[sz](?:y|ier|iest|iness)|itch\w*
scratch(?:y|ier|iest|iness)|soft(?:er|est|ness)?|soften\w*|smell\w*|odou?rs?|stink\w*
nois(?:e|y|ier)|quiet\w*|loud|swish\w*|rustl\w*|crinkl\w*|crunch\w*|crispy|stiff\w*|bobbl\w*
pill(?:s|ed|ing)?|lint|(?:hold|holds|holding|held|stand|stands) up
shed(?:s|ding)?(?! (?:water|rain|light))|sweat(?:y|s|ing)?|clammy|overheat\w*|see[- ]through
sheer|opaque|jam(?:s|med|ming)?|snag\w*|fray\w*|unravel\w*|sag(?:s|gy|ging)?
zip\w*\b[^,.;?!]{0,30}\b(?:stick\w*|catch\w*|break\w*|broke|smooth\w*|fail\w*)
(?:lose|loses|keep|keeps|hold|holds) (?:its|their) shape|stretch(?:es)? out|fad(?:e|es|ed|ing)
bagg(?:y|ing|ed)|still looks?
wears? (?:well|out|over|thin|down)|how (?!old)[^,.;?!]{0,40}\b(?:wears?|ages?)
ag(?:e|es) (?:well|badly)
(?:go|goes|get|gets|turn|turns) (?:all )?(?:baggy|lumpy|hard|limp|flat|fuzzy|thin)|age?ing
durab\w*|hard[- ]wearing|sturd\w*|flims\w*|last long
lasts?(?= ?(?:$|[?.!]|long|for\b|a\b|more\b|years|through))|lasts? (?:several|before|well)
worth (?:it|the|buying|paying|getting|spending)|overpriced|value|any good|quality|feels?
static|lump\w*|clump\w*|(?:sleep|sleeps|run|runs) (?:hot|cold|cool|warm)|fluff\w*|fuzz\w*
flattering|like to (?:wear|sleep|walk|use)|against (?:the |my |your |bare )?skin|over time
real[- ]world|real use|in real life|irl|in practice|slip(?:s|pery)?|slid(?:e|es|ing)|curl\w*
bleed\w*"""
)
# An adjective in front of the goods describes the item wanted; nobody asks about it.
_ATTRIBUTIVE = re.compile(
    r"\b(?:non[- ])?(?:soft|comfy|comfortable|co[sz]y|durable|sturdy|quiet|stiff|scratchy|itchy|"
    r"sheer|noisy|smelly|good quality|hard[- ]wearing),? (?:(?!(?:and|or|is|are|the|this|these|"
    r"that|does|do|on|in|of|for|to|with|when|if|your)\b)[\w-]+,? ){0,3}"
    + _GOODS.pattern
    + r"|\b(?:any|some)thing (?:soft|comfy|comfortable|co[sz]y|durable|sturdy|quiet)\b",
    re.IGNORECASE,
)
_IS_A = re.compile(r"\b(?:is|are)\b[^,.;?!]{0,60}\ban? $", re.IGNORECASE)
# Attributes that are an ask of their own beside another one ("reviews and sizing for …").
_FACTS = _cue(
    r"""materials?|fabric|made (?:of|from|in)|waterproof\w*|water[- ]?(?:resist|repell)\w*|windproof
breathab\w*|warm|warmth|warmer|warmest|weigh\w*|weights?|heavy|light(?:er|weight)?|thick\w*
thread count|gsm|upf|insulat\w*|fill(?:ing|ed)?|packable|stretchy|sizing|size (?:guide|chart)
washing|machine[- ]wash\w*|washable|wash instructions|care (?:guide|instructions?|label)
dry[- ]?clean\w*|dimensions|measurements|absorben\w*|inseam|seams?|price|cost|stock"""
)
_SPECS = _cue(
    rf"""{_FACTS.pattern}|pockets?|hood\w*|zip\w*|down|membrane|sizes?|fits?|fitting|wash(?:ed|es)?
clean\w*|iron\w*|tumble\w*|dryer|dry|bleach|colou?rs?|how much|availab\w*|sold out|on sale
cheap\w*|expensive|compare\w*|difference\w*|versus|vs|shrink\w*|origin|vegan|organic|recycled
lined|lining|pile|deep|length|long|wide|width|tall|stretch|wrinkl\w*|creas\w*|stain\w*|leak\w*
dwr|hot|cold|rain|layer\w*|(?:how|keep|keeps|stay) cool|measure\w*|small|medium|large|xs|xl|xxl
[2-4]xl|size up|style|features?|spec\w*|something (?:for|to|warm|light\w*|soft|that)|gifts?
what should i (?:get|buy|wear)
(?:any|some)thing (?:soft|comfy|co[sz]y|comfortable)
cooler|percale|sateen|cotton|wool|jute|alpaca|polyester|anything for
in (?:black|navy|grey|gray|blue|green|red|white|beige|brown|sage|rust)"""
)
_CARE_HOW = _cue(
    r"""how (?:do|can|should|would|to) ?(?:i|you|we)? (?:stop|prevent|keep|get|remove|wash|clean
care|dry|iron|store|restore|soften|avoid)|way to (?:wash|clean|dry|iron|store|keep|stop|care)"""
)
_PICK = re.compile(
    r"\b(?:rec+om+end\w*|suggest\w*|(?:looking|searching|shopping) for|find me|show me|after|"
    r"(?:want|need|like)(?= (?:an?|some|new|warm)\b)|help me (?:find|choose|pick)|which|best|any|"
    r"what(?:'s| is) (?:a|the best|the \w+est)|do you (?:have|sell)|"
    r"what(?= (?!do|did)\w+ \w* ?(?:do|did|are)\b))"
    r"\b(?! (?:one|of|is|are|this|that|the|these|those|it|my|your)\b(?! best))"
    r"(?P<gap>[^,.;?!]{0,45}?)\b" + _GOODS.pattern,
    re.IGNORECASE,
)
_COMPLAINT = _cue(
    r"""broke|broken|ripped|torn|tore|fell apart|falling apart|came (?:apart|undone|with)|hole|late
lost|disappoint\w*|unhappy|not (?:very |at all )?(?:happy|impressed)|furious|annoy\w*|fed up
terrible|awful|ridiculous|unacceptable|rubbish|worst|useless|let down
never (?:came|got|received)|still (?:waiting|not|hasn't|haven't)|faulty|damaged|complain\w*
ruined|waste of money|not good enough|no(?:body| one) (?:answers|replied|replies)
has (?:gone|split|come off)|turned up with|stained|poor"""
)

# A clause ends at a sentence end, or at a comma or joiner before the start of a new ask.
_INVERTED = (
    r"(?:(?:what|how|which|when|where|why|who)(?:'?s|'?re| (?:is|are|do|does|did|can|will|would|"
    r"should|long|much|big|heavy|warm|about|size))|am i|"
    r"(?:can|could|do|does|is|are|will|would|should|"
    r"did) (?:i|it|they|you|the|this|that|these|those|we|your|my|anyone|people|customers|there|"
    r"reviewers|buyers|shoppers|returns)|(?:has|have) (?:anyone|anybody|people|customers)|any)\b"
)
_STARTER = (
    rf"(?:{_INVERTED}|(?:what|how|which|when|where|why|is|are|do|does|can|could|will|tell|show|"
    r"give|find|recommend|suggest|compare|just|now|if|need|want|looking|it|they|"
    r"i|i'm|i'd|i've|we|my|also|"
    r"plus|thanks)\b)"
)
_BOUNDARY = re.compile(
    rf"(?<=[.!?;])\s+|(?<=[!?;])(?=\w)|\s[-–—]+\s|;|"
    rf",? (?:and|but|so|then|also|plus|&|\+)(?: also| then)? (?={_STARTER})|, (?={_STARTER})",
    re.IGNORECASE,
)
_STARTS = re.compile(_STARTER, re.IGNORECASE)
_INVERTED_START = re.compile(rf"\b{_INVERTED}", re.IGNORECASE)
_REQUEST = _cue(
    r"""tell|show|^give|(?:please|can you|could you) give|help|explain|compare
let me know|please|pls|(?:to|you|me) find|find me
rec+om+end\w*|suggest\w*|how (?:do|does|can|is|are|long|much|many)
i (?:want|need|would like|wonder)|i'd (?:like|love)|wonder\w*|looking for|i'm after|need|want
wanna|worried|concerned|curious|not sure|unsure|can't (?:find|decide)|don't know|question"""
)
_PERSONAL = re.compile(
    r"(?:(?:hi|hey|hello|ok|so|well|and|but|also|yes|because|as)[ ,]+)?"
    r"(?:i|i'm|i've|we|we've|it|they|he|she|my|our|sent|bought|got|returned|ordered|the last|"
    r"last \w+|this is|thinking|"
    r"the (?:parcel|order|delivery)|"
    r"the (?:\w+ )?(?:label|page|description|site|size chart|size guide) (?:says|said|states))\b",
    re.IGNORECASE,
)
_KNOWN = _cue(
    r"(?:i|we)(?:'ve| have)? (?:already|know|read|saw|seen|checked|understand|love|like|find)"
)
_LEADING_IF = _cue(
    r"(?:and |but )?supposing|if|when i\w*|once|since|because|although|even though|while"
)
_THANKS = re.compile(
    r"\b(?:thanks|thank you|thx|cheers)\b[^?.!,;]{0,60}(?=[.!,;]|$)", re.IGNORECASE
)
_NEGATED = re.compile(
    r"\b(?:don'?t (?:really )?(?:care|mind|worry)|don'?t need (?:to know|the|any)|no need|"
    r"never ?mind|not (?:asking|interested|bothered|talking)|forget (?:about|the|what)|skip|"
    r"ignore|regardless of|(?:apart|aside) from|other than|besides|(?:instead|rather) (?:of|than)|"
    r"leaving aside)\b[^,.;?!]{0,80}?(?=[,.;?!]|$|\bbut\b|\bjust\b)",
    re.IGNORECASE,
)
# "… if the zip breaks" is a condition, not the ask; "what happens if …" is the ask.
_SUBORDINATE = re.compile(
    r"(?<!happens)(?<!what)(?<!know)(?<!wonder)(?<!wondering)(?<!me)(?<!see)(?<!sure)(?<!check)"
    r"(?<!ask)(?<!say)(?<!mention)(?<!look)(?<!looking)(?<!i'm) "
    r"(?:if|when|because|since|after|once|unless|although|even though|while|in case)\b",
    re.IGNORECASE,
)
_GOVERNED = _cue(r"about|on|regarding|say|mention|think|find")
_FRAGMENT = re.compile(r" (?:and|plus|&|\+) |[,/+&]", re.IGNORECASE)
_NAMED_ITEM = re.compile(r"\bthe [A-Z][a-z]+ [A-Z][a-z]+")

# One-letter typos of common cue words: both sides lose one letter and meet.
_TYPO_WORDS = frozenset(
    """return returns refund exchange shipping delivery delivered review reviews reviewers
    complaint complaints waterproof material comfortable scratchy washable washing machine
    breathable recommend
    jacket jackets fleece sweater blanket available discount payment warranty customers""".split()
)
_TYPO_KEEP = frozenset("shopping slipping skipping snipping".split())


def _deletions(word: str) -> list[str]:
    return [word, *(word[:i] + word[i + 1 :] for i in range(len(word)))]


_TYPO_KEYS = {key: word for word in sorted(_TYPO_WORDS) for key in _deletions(word)}
_TYPO_TOKEN = re.compile(r"\b[A-Za-z]{5,13}\b")


def _fix_typo(m: re.Match[str]) -> str:
    word = m.group(0).lower()
    if word in _TYPO_WORDS or word in _TYPO_KEEP or word.rstrip("s") in _TYPO_WORDS:
        return m.group(0)
    hits = (_TYPO_KEYS.get(key, "") for key in _deletions(word))
    return next((hit for hit in hits if hit[:1] == word[0]), m.group(0))


# A plain statement beside a question is background ("Washing it is easy. But does it …?").
_STATEMENT = re.compile(r"\b(?:is|are|was|were|has|had|gave|went|came)\b", re.IGNORECASE)


def _asks(clauses: Sequence[str]) -> list[str]:
    """The clauses that ask, without what the shopper tells ("I already read the reviews")."""
    asks: list[str] = []
    asked = any(clause.endswith("?") for clause in clauses)
    for clause in clauses:
        question = clause.endswith("?")
        stated = _STATEMENT.search(clause) and not _INVERTED_START.search(clause)
        if asked and not question and stated and not _REQUEST.search(clause):
            continue
        known = bool(_KNOWN.match(clause))
        if known and not question:
            continue
        told = _PERSONAL.match(clause) and not question and not _REQUEST.search(clause)
        if known or told or _LEADING_IF.match(clause):
            inverted = _INVERTED_START.search(clause)
            if inverted is None:
                continue
            clause = clause[inverted.start() :]
        asks.append(clause)
    return asks


def _asked_experience(clause: str) -> bool:
    def described(m: re.Match[str]) -> str:
        asked = _IS_A.search(clause[max(0, m.start() - 70) : m.start()])
        return m.group(0) if asked else " "

    return bool(_EXPERIENCE.search(_ATTRIBUTIVE.sub(described, clause)))


def _read_clause(clause: str) -> set[Index]:
    """What one clause asks. Other people's views come first, then the shop, then fit and
    experience, then attributes, which are mostly how a shopper points at an item. A second index
    needs a fragment of its own, or a pick."""
    if _REVIEW_POLICY.search(clause):
        return {"policy"}
    fragments = _FRAGMENT.split(clause)
    policy = bool(_POLICY.search(clause))
    pick = _PICK.search(clause)
    found: set[Index] = set()
    if _VOICES.search(clause) or (not policy and _WEAK_VOICES.search(clause)):
        found.add("reviews")
        if _OPINION_PICK.search(clause) or (pick and not _VOICES.search(pick.group("gap"))):
            found.add("specifications")
        others = [f for f in fragments if not _VOICES.search(f)]
        if _GOVERNED.search(clause):
            return found
        if any(_POLICY.search(f) for f in others):
            found.add("policy")
        elif any(_FACTS.search(f) for f in others):
            found.add("specifications")
    elif policy:
        found.add("policy")
        others = [f for f in fragments if not _POLICY.search(f)]
        if any(_FACTS.search(f) for f in others):
            found.add("specifications")
        elif pick and not _POLICY.search(clause[: pick.end()]):
            found.add("specifications")
        if any(_asked_experience(f) for f in others):
            found.add("reviews")
    elif _CARE_HOW.search(clause):
        found.add("specifications")
    elif _RUNS.search(clause):
        found |= {"reviews", "specifications"}
    elif _asked_experience(clause):
        found.add("reviews")
        others = [f for f in re.split(r" (?:and|or) |,", clause) if not _asked_experience(f)]
        picked = pick and _asked_experience(clause[pick.end() :])
        if picked or any(_FACTS.search(f) for f in others):
            found.add("specifications")
    elif _SPECS.search(clause) or _GOODS.search(clause):
        found.add("specifications")
    return found


def _topic(clause: str) -> bool:
    """A bare noun phrase naming what the next ask is about ("zipper on the fleece, does it …")."""
    if clause.endswith("?") or _STARTS.match(clause) or _INVERTED_START.search(clause):
        return False
    return not (_REQUEST.search(clause) or _FACTS.search(clause) or _PICK.search(clause))


def _read(clauses: Sequence[str]) -> set[Index]:
    found: set[Index] = set()
    topics: set[Index] = set()
    for clause in clauses:
        cut = _SUBORDINATE.search(clause)
        read = (_read_clause(clause[: cut.start()]) if cut else set()) or _read_clause(clause)
        if read == {"specifications"} and _topic(clause):
            topics |= read
        else:
            found |= read
    return found or topics


def route_indexes(message: str) -> frozenset[Index]:
    """The indexes a message asks about (RAG-07): code only, from the message alone, never empty.

    Nothing calls this yet. It is report-only by the user's decision (plan 2026-11-25): narrowing a
    search to these indexes is not wired. If it were, a route could only narrow: filters never
    widen access, and audience, region and visibility come from the caller (hard rule 7).

    Unsure, or text aimed at the system, is every index. The rules follow the labelling guide,
    `docs/reviews/2026-11-25-RAG-10-labelling-guide.md`.
    """
    text = unicodedata.normalize("NFKC", message).replace("’", "'")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = " ".join(re.sub(r"[\r\n]+", " ; ", text).split())
    if _SYSTEM_TEXT.search(text):
        return ALL_INDEXES
    text = _NEUTRAL.sub(" ", _RATED.sub(" waterproof ", _CALLER_WORDS.sub(" ", text)))
    text = _NEGATED.sub(" ", _THANKS.sub(" ", _TYPO_TOKEN.sub(_fix_typo, text)))
    text = " ".join(text.split())
    clauses = [c.strip() for c in _BOUNDARY.split(text) if c.strip()]
    asked = any(c.endswith("?") for c in clauses) or _REQUEST.search(text)
    if not asked and _COMPLAINT.search(text) and not _VOICES.search(text):
        return frozenset({"policy"})  # a complaint with no question
    found = _read(_asks(clauses)) or _read(clauses)
    if not found and _NAMED_ITEM.search(text):
        found.add("specifications")
    return frozenset(found) or ALL_INDEXES
