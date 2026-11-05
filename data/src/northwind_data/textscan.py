"""Scanners for text that may be ingested (hard rules 2 and 3).

`find_money_or_stock` flags currency amounts and stock counts; `find_real_brands` flags real
brand and trademark names; `find_unsafe_hosts` flags URLs/emails outside reserved test domains.
RAG-01 can reuse these on chunk text.

`find_promotions` flags a stated discount. It runs on source text with a named exemption, because
two planted reviews hold one on purpose (`northwind_rag.sources`). `find_generated_violations`
is the wider check for model-written text, which has no exemption (RAG-02).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

_NUM = r"\d[\d,]*(?:\.\d+)?"
# Comma decimals too: `139,00`. `_NUM` alone would stop at the comma's digits only by luck.
_AMOUNT = r"\d[\d.,]*\d|\d"
_CODES = r"USD|CAD|GBP|EUR|AUD|NZD|CHF|JPY|SEK|NOK|DKK"
_NUMBER_WORDS = (
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a few|a couple|a handful"
)
_COUNTED = r"(?:pairs?|sets?|items?|units?|pcs|pieces|sizes?)"
MONEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[$£€¥]\s?\d"),
    re.compile(r"\b(?:US|CA|C|A|AU|NZ)\$\s?\d"),
    re.compile(rf"\b(?:USD|CAD|GBP|EUR)\s?{_NUM}"),
    re.compile(
        rf"{_NUM}\s?(?:USD|CAD|GBP|EUR|dollars?|bucks|pounds?|quid|euros?|cents?)\b", re.IGNORECASE
    ),
    # RAG-02: currency after the amount, comma decimals included.
    re.compile(rf"(?:{_AMOUNT})\s?[$£€¥]"),
    # Any listed code on either side, in any case, with punctuation between.
    re.compile(rf"\b(?:{_CODES})\b\s*[:\-=]?\s*\(?\s*(?:{_AMOUNT})", re.IGNORECASE),
    re.compile(rf"(?:{_AMOUNT})\s*\(?\s*(?:{_CODES})\b", re.IGNORECASE),
    # A labelled value needs no currency at all.
    re.compile(rf"\b(?:price|cost|msrp|rrp|was|now)\s*[:=]\s*(?:{_AMOUNT})", re.IGNORECASE),
)
STOCK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bonly\s+\d+\s+(?:left|remaining|available|in stock)\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+(?:left|remaining)\b", re.IGNORECASE),
    re.compile(
        r"\b\d+\s+(?:units?|pcs|pieces)\s+(?:left|in stock|available|on hand)\b", re.IGNORECASE
    ),
    re.compile(r"\b\d+\s+(?:in stock|on hand)\b", re.IGNORECASE),
    re.compile(r"\b(?:in stock|stock level|on hand|inventory)\s*[:=]?\s*\d+\b", re.IGNORECASE),
    # RAG-02.
    re.compile(r"\b(?:stock|qty|quantity|available|availability)\s*[:=]\s*\d+\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+available\b", re.IGNORECASE),
    re.compile(rf"\b\d+\s+{_COUNTED}\s+(?:left|remaining|in stock|available)\b", re.IGNORECASE),
    re.compile(
        rf"\b(?:only|just)\s+(?:{_NUMBER_WORDS})\s+(?:\w+\s+)?"
        r"(?:left|remaining|in stock|available)\b",
        re.IGNORECASE,
    ),
)
# A stated discount. Words like "clearance" are not here: the returns policies use it as a term.
PROMOTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{1,3}\s?%\s*(?:off|discount)\b", re.IGNORECASE),
)
# Only for generated text. Source text may use these in plain policy or FAQ wording.
GENERATED_PROMOTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:half[- ](?:price|off)|bogo|buy one get one|free today|on sale|flash sale|"
        r"sale ends|promo(?:tion)?(?:al)?(?: code)?|discount(?:ed|s)?|coupons?|vouchers?|"
        r"member price)\b",
        re.IGNORECASE,
    ),
)
# Instruction-like text in something that should only say where a chunk sits (hard rule 6). Aimed
# at who a planted review addresses and what it asks for, not at ordinary care or policy verbs.
INSTRUCTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:ignore|disregard|forget)\b.{0,40}\b(?:instructions?|rules?|prompts?)\b", re.I
    ),
    re.compile(r"\b(?:system prompt|hidden instructions?|new instructions?|tool list)\b", re.I),
    re.compile(r"\b(?:assistant|the ai|ai|chatbot|bot|model|llm)\b\s*[:,]", re.I),
    re.compile(r"\b(?:to|for) the (?:ai|assistant|model)\b", re.I),
    re.compile(
        r"\btell (?:the |all |every |your )?(?:shoppers?|users?|customers?|everyone)\b", re.I
    ),
    re.compile(r"\b(?:ask|recommend)\b.{0,20}\b(?:shoppers?|users?|customers?)\b", re.I),
    re.compile(r"\b(?:issue_refund|apply_discount|[a-z]+_[a-z]+\(\))", re.I),
    re.compile(
        r"\b(?:authori[sz]e (?:a |the )?(?:full )?refund|approves? refunds?|process it)\b", re.I
    ),
    re.compile(r"\b(?:card number|security code|cvv|home address(?:es)?)\b", re.I),
    re.compile(r"\bfreebot\b|\bno rules\b|\blet'?s play\b", re.I),
)
_ANY_URL = re.compile(r"\bhttps?://|\bwww\.|\]\(", re.IGNORECASE)
_NUMBER = (
    r"\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|a few|a couple|a handful"
)
_AUDIENCE = r"shoppers?|users?|customers?|buyers?|readers?|everyone|people|assistant|ai|model"
# Model-written text has no reason to state any of these, so these reach further than the source
# scan: a sentence describing a planted review writes "says", "tells" and "ninety percent".
GENERATED_MONEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d+[.,]\d{2}\b"),
    re.compile(r"\b(?:costs?|priced(?: at)?|sells? for|was|now)\s+\d", re.I),
    re.compile(r"\b(?:dollars?|euros?|pounds?|quid|bucks|cents?|kr|inr|rupees?)\b", re.I),
    re.compile(r"(?:%|\bper\s?cent)\s*(?:off|discount|cheaper|less)\b", re.I),
    re.compile(
        r"\b(?:for free|is free|are free|free of charge|no charge|charges? nothing|costs? nothing|"
        r"lower price|reduced price|price (?:drop|cut)|cheaper|markdown)\b",
        re.I,
    ),
)
GENERATED_STOCK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:sold out|out of stock|in stock|low stock|stock levels?|restock\w*)\b", re.I),
    re.compile(rf"\b(?:{_NUMBER})\s+(?:\w+\s+)?(?:left|remaining|available)\b", re.I),
    re.compile(rf"\b(?:the )?last (?:{_NUMBER})\b", re.I),
)
# Hard rule 2 names delivery dates and order state too.
GENERATED_DELIVERY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\barriv\w* (?:in|within|by|on|next|tomorrow|today)\b", re.I),
    re.compile(r"\bdeliver\w* (?:in|within|by|on|next|tomorrow|today)\b", re.I),
    re.compile(r"\bships? (?:in|within|by|on|next|tomorrow|today)\b", re.I),
    re.compile(r"\b(?:shipped|dispatched|in transit|tracking number|estimated delivery)\b", re.I),
    re.compile(r"\border (?:status|confirmed|cancel\w*|shipped)\b", re.I),
)
GENERATED_INSTRUCTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:ignor|disregard|forget)\w*\b.{0,40}\b(?:instructions?|rules?|prompts?)\b", re.I
    ),
    re.compile(
        rf"\b(?:tell|ask|urg|instruct|steer|direct|advis|recommend)\w*\b.{{0,30}}\b(?:{_AUDIENCE})\b",
        re.I,
    ),
    re.compile(r"\bauthori[sz]\w*\b", re.I),
    re.compile(r"\bcard details\b|\bwithout asking\b|\bconfirm every\b", re.I),
)
# A bare domain has no scheme for the host checks to find.
_BARE_DOMAIN = re.compile(
    r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|co|example|shop|store)\b", re.I
)
# A sentence about a review describes the product. A review has no standing to state any of this.
REVIEW_CONTEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:polic\w+|refund\w*|returns?|returned|sent back|sale|code|staff|manager|admin|"
        r"support team|instructions?)\b",
        re.I,
    ),
)
# Typographic forms a good sentence may use, and the ASCII it is stored as.
_FOLDS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
    }
)

# Real brands and trademarks that must not appear in synthetic text (hard rule 3).
REAL_BRANDS: tuple[str, ...] = (
    "Patagonia",
    "The North Face",
    "North Face",
    "Columbia Sportswear",
    "Arc'teryx",
    "Arcteryx",
    "Marmot",
    "Mammut",
    "Salomon",
    "Helly Hansen",
    "Fjallraven",
    "Fjällräven",
    "Canada Goose",
    "Mountain Hardwear",
    "Outdoor Research",
    "REI",
    "L.L.Bean",
    "LL Bean",
    "Eddie Bauer",
    "Lululemon",
    "Uniqlo",
    "Zara",
    "H&M",
    "Nike",
    "Adidas",
    "Under Armour",
    "Gore-Tex",
    "GORE-TEX",
    "Polartec",
    "PrimaLoft",
    "Thinsulate",
    "Cordura",
    "Pertex",
    "Sympatex",
    "Smartwool",
    "Icebreaker",
    "IKEA",
    "West Elm",
    "Pottery Barn",
    "Brooklinen",
    "Crate & Barrel",
    "Williams Sonoma",
    "Le Creuset",
    "Coyuchi",
    "Boll & Branch",
    "Frette",
    "Ralph Lauren",
    "Levi's",
    "Carhartt",
    "Barbour",
    "Burberry",
    "Amazon",
    "Walmart",
    "Responsible Down Standard",
    "Bluesign",
    "OEKO-TEX",
)
_BRAND_RE = re.compile(
    r"(?<![\w])(?:" + "|".join(re.escape(b) for b in REAL_BRANDS) + r")(?![\w])",
    re.IGNORECASE,
)
ALLOWED_HOST_SUFFIXES: tuple[str, ...] = (
    ".example",
    ".invalid",
    ".test",
    "example.com",
    "example.org",
)
_URL_HOST = re.compile(r"https?://([^/\s)\]]+)", re.IGNORECASE)
_EMAIL_HOST = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")


def find_money_or_stock(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in (*MONEY_PATTERNS, *STOCK_PATTERNS):
        hits.extend(m.group(0) for m in pattern.finditer(text))
    return hits


def find_promotions(text: str) -> list[str]:
    return [m.group(0) for pattern in PROMOTION_PATTERNS for m in pattern.finditer(text)]


def fold_generated(text: str) -> str:
    """Typographic quotes and dashes as ASCII, so the check and the stored text are the same."""
    return text.translate(_FOLDS).strip()


def is_printable_ascii(text: str) -> bool:
    """Closes homoglyphs, other scripts, control and invisible characters and most currency signs
    in one rule, which no pattern list can do."""
    return all(" " <= ch <= "~" for ch in text)


def find_generated_violations(text: str) -> list[str]:
    """Everything model-written text may not hold, as `category: match`. No exemptions (RAG-02).
    The caller folds and checks for printable ASCII first."""
    found = [f"money-or-stock: {hit}" for hit in find_money_or_stock(text)]
    found += [f"promotion: {hit}" for hit in find_promotions(text)]
    groups = (
        ("promotion", GENERATED_PROMOTION_PATTERNS),
        ("money-or-stock", GENERATED_MONEY_PATTERNS),
        ("money-or-stock", GENERATED_STOCK_PATTERNS),
        ("delivery", GENERATED_DELIVERY_PATTERNS),
        ("instruction", INSTRUCTION_PATTERNS),
        ("instruction", GENERATED_INSTRUCTION_PATTERNS),
    )
    for category, patterns in groups:
        for pattern in patterns:
            found += [f"{category}: {m.group(0)}" for m in pattern.finditer(text)]
    found += [f"link: {m.group(0)}" for m in _ANY_URL.finditer(text)]
    found += [f"link: {m.group(0)}" for m in _BARE_DOMAIN.finditer(text)]
    found += [f"brand: {hit}" for hit in find_real_brands(text)]
    found += [f"host: {hit}" for hit in find_unsafe_hosts(text)]
    return found


def find_review_context_violations(text: str) -> list[str]:
    """The extra limit on a sentence about a review, as `category: match`."""
    return [
        f"review-claim: {m.group(0)}"
        for pattern in REVIEW_CONTEXT_PATTERNS
        for m in pattern.finditer(text)
    ]


def find_real_brands(text: str) -> list[str]:
    return [m.group(0) for m in _BRAND_RE.finditer(text)]


def find_unsafe_hosts(text: str) -> list[str]:
    hosts = [m.group(1) for m in _URL_HOST.finditer(text)]
    hosts += [m.group(1) for m in _EMAIL_HOST.finditer(text)]
    return [h for h in hosts if not h.lower().endswith(ALLOWED_HOST_SUFFIXES)]


def iter_strings(obj: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Yields (json_path, value) for every string inside nested dicts/lists."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for key in sorted(obj):
            yield from iter_strings(obj[key], f"{path}.{key}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from iter_strings(item, f"{path}[{i}]")
