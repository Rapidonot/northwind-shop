"""The output check: what the model writes is checked before any of it reaches the shopper
(NFR-03, RAG-08; decision 0015).

Retrieved text can carry an attacker's words to every shopper who asks about a product, and a prompt
rule only shapes what the model does. This check is the control: it blocks by payload class,
deterministically, whatever the model meant. It runs inside `stream_answer`, between the model and
every caller, so the gateway and the eval adapter both get checked text.

How it holds text back (row #13: a short lookahead from a candidate start, not whole sentences):
- the trailing word, a run of non-whitespace, is always held, so a word split across deltas, or a
  bare domain, is checked whole;
- from the earliest trigger in the last `L` characters, text is held, and every class pattern is at
  most `L` wide (a test holds that), so a match that starts at a trigger has completed or failed by
  then;
- text is released in the model's own deltas, so nothing changes shape when nothing is blocked.

On a match, the sentence holding it is replaced by one fixed sentence for its class. If part of the
sentence was already released, the check sends `Reset` and then the released text up to the
sentence start, so only text that passed is ever sent twice. A source ID in brackets that no search
returned this turn is removed (hard rule 5). Nothing here logs text: the counts per class are all
that leave.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache

L = 64
ALLOWED_EMAIL = "care@northwind.example"

# The text a shopper sees in place of a blocked sentence. Each makes no product or policy claim and
# states no figure, so it needs no source; a test holds that each passes every class (approved by
# the user with the row #13 plan).
CONFIRMATION_SENTENCE = "I can't confirm orders, payments or refunds in chat."
FIXED_SENTENCES: Mapping[str, str] = {
    "promotion": "I can't help with offers or codes in chat.",
    "link": "I've left out a link or contact detail here.",
    "confirmation": CONFIRMATION_SENTENCE,
    "refund_authority": CONFIRMATION_SENTENCE,
    # Row #12 (the user's D4): the assistant now has `check_stock`, so "I can't check stock in chat"
    # stopped being true; a stock claim the live check did not return gets this instead.
    "stock": "I can only give stock from the live stock check.",
    "false_policy": "I couldn't match that policy term to Northwind's policy for your region.",
    # Row #12 (RAG-09): a price the live price check did not return this turn.
    "money": "I can only give a price from the live price check.",
}
CLASSES = tuple(FIXED_SENTENCES)
COUNTED = (*CLASSES, "marker")

# Cyrillic and Greek letters that look like Latin ones. NFKC leaves these alone (RAG-11 open item).
_CONFUSABLES = str.maketrans(
    {
        "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
        "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d",
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
        "С": "C", "Т": "T", "У": "Y", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
        "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t",
        "υ": "u", "χ": "x", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I",
        "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
        "ı": "i", "ɑ": "a", "օ": "o", "ꓲ": "I", "。": ".",
    }
)  # fmt: skip
# Default-ignorable code points that are not format characters (Cf), such as variation selectors and
# the combining grapheme joiner: they render as nothing, so they must not split a word (security
# review pass 1, H3).
_IGNORABLE = re.compile(
    "[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e"
    "\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8\U0001bca0-\U0001bca3"
    "\U0001d173-\U0001d17a\U000e0000-\U000e0fff]"
)
_DROPPED = frozenset({"Cf", "Mn", "Me"})


def _fold_char(char: str) -> str:
    """NFKD, then combining marks, format characters and other default-ignorables dropped, then
    NFKC and the confusables fold. So `dïscount`, `d\ufe0fiscount` and `ｄｉｓｃｏｕｎｔ` all read
    `discount`, and an ideographic full stop reads as a dot."""
    if _IGNORABLE.match(char):
        return ""
    decomposed = unicodedata.normalize("NFKD", char)
    kept = "".join(c for c in decomposed if unicodedata.category(c) not in _DROPPED)
    return unicodedata.normalize("NFKC", kept).translate(_CONFUSABLES)


@dataclass(frozen=True)
class View:
    """`text` is the casefolded matching view, `exact` the same without casefolding; each has a
    map from its positions back to positions in the raw text."""

    text: str
    index: tuple[int, ...]
    exact: str
    exact_index: tuple[int, ...]

    def raw_span(self, start: int, end: int, *, exact: bool = False) -> tuple[int, int]:
        index = self.exact_index if exact else self.index
        return index[start], index[end - 1] + 1


def view_of(raw: str, offset: int = 0) -> View:
    text: list[str] = []
    index: list[int] = []
    exact: list[str] = []
    exact_index: list[int] = []
    for position, char in enumerate(raw, start=offset):
        folded = _fold_char(char)
        exact.append(folded)
        exact_index.extend([position] * len(folded))
        lowered = folded.casefold()
        text.append(lowered)
        index.extend([position] * len(lowered))
    return View("".join(text), tuple(index), "".join(exact), tuple(exact_index))


@lru_cache(maxsize=512)
def normal(text: str) -> str:
    """The matching view with whitespace collapsed: how a span is compared with a source."""
    return re.sub(r"\s+", " ", view_of(text).text)


_S = r"\s{1,3}"
_GAP = r"[\s,]{1,2}"
_WORDS = rf"(?:[^\W\d_]{{1,10}}{_GAP}){{0,3}}"  # at most three short words in between
_SUBJECT = r"(?:order|payment|refund|discount|code)"
# One adverb may sit between the verb and its participle ("is now confirmed", M3). A negation is not
# one of them, so "is not confirmed" still passes.
_ADVERB_WORD = r"(?:now|already|just|also|since|fully|finally|successfully)"
_ADVERB = rf"(?:{_ADVERB_WORD}\s{{1,3}})?"
_UNIT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9,
}  # fmt: skip
_TEEN_WORDS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS_WORDS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}  # fmt: skip
_NUMBER_WORDS = {"a": 1, "an": 1, **_UNIT_WORDS, **_TEEN_WORDS, **_TENS_WORDS}


def _either_words(words: Mapping[str, int]) -> str:
    return "|".join(sorted(words, key=len, reverse=True))


_UNITS = _either_words(_UNIT_WORDS)
_TENS_UNITS = (
    rf"(?:(?:{_either_words(_TENS_WORDS)})(?:[\s-]{{1,2}}(?:{_UNITS}))?"
    rf"|{_either_words(_TEEN_WORDS)}|{_UNITS})"
)
# A number read whole (pass 4, P4-1, P4-4): "sixty-five", "three hundred sixty-five", "a
# hundred", "1000", "1,000". Reading only the word next to the unit let a document's "5 business
# days" vouch for "sixty-five days".
_SPELLED = (
    rf"(?:(?:a|an|{_UNITS})[\s-]{{1,2}}hundred(?:[\s-]{{1,2}}(?:and[\s-]{{1,2}})?{_TENS_UNITS})?"
    rf"|{_TENS_UNITS}|an|a)"
)
_NUMERAL = rf"(?:\d{{1,3}}(?:,\d{{3}}){{1,2}}|\d{{1,4}}|{_SPELLED})"
# A spelled count without the bare article: "three", "a hundred", "twenty-one", never "a" or "an"
# alone, so "an available size" is not a count (row #12).
_SPELLED_COUNT = (
    rf"(?:(?:a|an|{_UNITS})[\s-]{{1,2}}hundred(?:[\s-]{{1,2}}(?:and[\s-]{{1,2}})?{_TENS_UNITS})?"
    rf"|{_TENS_UNITS})"
)
# Any run of digits and separators, read whole (security review pass 1, finding 4): "12345 left" and
# "1,200 units available" are counts too.
_COUNT = rf"(?:\d[\d,]{{0,9}}|{_SPELLED_COUNT})"
# An amount of money as written, greedily: digits and separators, ending on a digit (so a sentence's
# full stop stays outside it), and a trailing "k". `money_value` reads it exactly or the figure is
# blocked (fail closed; security review pass 1, finding 1), so "€9,99" or "$1.5k" is never a
# partial match that slips through.
_AMOUNT = r"\d(?:[\d.,]{0,13}\d)?k?"
# Markdown that can sit around an amount: "$**279**", "**279** USD".
_MK = r"[*_~`]{0,4}"
_CURRENCY_WORD = r"(?:usd|gbp|eur|aud|cad|dollars?|bucks?|quid|euros?)"
_CURRENCY_CODE = r"(?:usd|gbp|eur|aud|cad)"
# Size words the parser does not read as part of a number: "fifteen hundred dollars", "a couple
# hundred bucks", "one thousand dollars". Before a currency word they are always blocked.
_MAGNITUDE = r"(?:hundred|thousand|grand|million|dozen|couple)"
# Up to two hedge words between a price word and its amount ("costs just 279", re-review N6).
# ("starting at" and "as low as" would push the pattern past L: a logged residual.)
_HEDGE = (
    r"(?:\s(?:now|just|only|about|around|roughly|approx\.?|under|over|nearly|currently|from))"
    r"{0,2}"
)
# Currencies the catalog never returns (re-review N2): their signs and codes or words.
_OTHER_SIGN = r"[¥₹₩₽₺₪฿₫₱₦₴₸¢₿₡₲₵₭₮₼₾﷼]"
_OTHER_CODE = (
    r"(?:chf|jpy|yen|nzd|hkd|sgd|sek|nok|dkk|kr|francs?|rupees?|pesos?|yuan|rmb|cny|inr|zar|mxn|"
    r"brl|rand|reais|won|rubles?|roubles?|lira|zloty|pln)"
)
_NUMBER = _either_words(_NUMBER_WORDS)
DURATION = re.compile(
    rf"\b(?P<n>{_NUMERAL})(?:[\s-]{{1,2}}(?:full|calendar))?[\s-]{{1,2}}"
    r"(?:(?:business|working)[\s-]{1,2})?(?P<u>hour|day|week|month|year)s?\b"
)
# A number joined to the one a duration starts with: "3–5 days", "3 to 5 days", "365 or 30 days".
# The two are read as one value, so neither half alone vouches for it.
_JOINED = re.compile(rf"(?:^|[^\w-])(?P<n>{_NUMERAL})\s{{0,2}}(?:[-–—]|to|or|and)\s{{0,2}}$")


# Fail closed on numbers (the user's decision, 2026-11-11): every duration unit word is looked at
# with the three tokens before it. A number word or numeral form there that the parser did not
# consume exactly ("a dozen", "twenty-something", "365-ish", "1.5") makes the span unvouched.
_UNIT_WORD = re.compile(r"\b(?:hour|day|night|week|fortnight|month|year)s?\b")
_NUMBERISH_WORDS = frozenset(
    (_NUMBER_WORDS.keys() - {"a", "an"}) | {"hundred", "thousand", "dozen", "score", "couple"}
)


# Markdown and brackets, removed anywhere in a token before it is tested (pass 5, P5-1): `**36**5`
# is `365`, `(365)-` is `365-`. A token is never skipped for its shape.
_MARKUP = re.compile(r"[*_~\"'()\[\]{}“”‘’`]")


_FILLER = frozenset(
    {"business", "working", "calendar", "full", "or", "so", "and", "to", "odd", "about",
     "around", "roughly", "approximately", "some"}
)  # fmt: skip


def numberish(token: str) -> bool:
    if re.search(r"\d", token) or "fortnight" in token:
        return True
    return any(part in _NUMBERISH_WORDS for part in re.split(r"[^a-z]+", token))


def number_value(text: str) -> int:
    digits = text.replace(",", "")
    if digits.isdigit():
        return int(digits)
    total = 0
    for word in re.split(r"[\s-]+", text):
        if word == "hundred":
            total = (total or 1) * 100
        elif word in _NUMBER_WORDS:
            total += _NUMBER_WORDS[word]
    return total


def duration_value(text: str, match: re.Match[str]) -> tuple[tuple[int, ...], str]:
    """A duration's value: its number, with a number joined before it as part of the value."""
    value: tuple[int, ...] = (number_value(match["n"]),)
    joined = _JOINED.search(text[max(0, match.start() - 45) : match.start()])
    if joined:
        value = (number_value(joined["n"]), *value)
    return value, match["u"]


@dataclass(frozen=True)
class Pattern:
    """One payload shape. `exempt` names how a match can pass: `duration` when its sentence holds
    no policy stem, or a public document returned this turn states the same duration;
    `free_terms` when a public document says returns are free; `policy_span` when a public policy
    document holds the same words; `conditional` when an `if`-like word directly introduces its
    subject in the present or future tense; `phone` when the digits are too few or form
    a date.
    `exact` patterns run on the view before casefolding."""

    cls: str
    regex: re.Pattern[str]
    exempt: str | None = None
    exact: bool = False


def _p(cls: str, pattern: str, exempt: str | None = None, *, exact: bool = False) -> Pattern:
    return Pattern(cls, re.compile(pattern), exempt, exact)


# What "available in …" names when it is the range offered rather than stock (row #12).
# Only a plural range is the range offered ("available in sizes XS-XL", "in three colours", "in a
# range of …"). A specific size, colour or fit is stock and needs `check_stock` (second re-review
# H2, the user's decision).
# A time word later in the sentence makes even a plural range a stock claim ("available in sizes M
# and L right now"; second re-review H2).
_TIMELY = (
    r"[^.!?\n]{0,40}\b(?:now|today|tonight|at\s{1,3}the\s{1,3}moment|currently|still|"
    r"in\s{1,3}stock)\b"
)
_RANGE = (
    r"(?:(?:sizes|colou?rs|fits|lengths|widths)\b|(?:\d{1,2}|two|three|four|five|six|seven|eight|"
    r"nine|ten|several|many|all|most)\s{1,3}(?:sizes|colou?rs|fits|lengths|widths)\b|"
    r"a\s{1,3}range\s{1,3}of\b)"
)

# Fixed with the row #13 plan before any recording; a change after one is stop 7 (decision 0015).
# Row #12 extends them, approved with its plan (decision 0015 §7 and its #12 amendment).
PATTERNS: tuple[Pattern, ...] = (
    # 1. Promotions come only from live tools (hard rule 2): class-level, no exemption.
    _p("promotion", r"discount"),
    _p("promotion", r"coupon"),
    _p("promotion", rf"promo(?:tion(?:al)?)?{_S}codes?"),
    _p("promotion", r"\d{1,3}\s{0,2}%\s{0,2}off\b"),
    _p("promotion", r"voucher"),
    _p("promotion", rf"staff{_S}codes?"),
    _p(
        "promotion", rf"member{_S}pric(?:e|es|ing)"
    ),  # "pricing": EV-090, row #13b (the user's (3a))
    _p("promotion", rf"free{_S}today"),
    _p("promotion", rf"(?:\d{{1,3}}|\b(?:{_NUMBER}))\s?(?:percent|per{_S}cent){_S}off\b"),  # M3
    _p("promotion", r"\bhalf[\s-]{1,3}price"),
    _p("promotion", rf"\bclearance{_S}(?:sale|price|event)s?\b"),
    _p("promotion", rf"\b[Cc]ode{_S}[A-Z0-9]{{4,12}}\b", exact=True),
    # 2. Links and contact details. Bare domains and emails are the word check, below.
    _p("link", r"https?://"),
    _p("link", r"\bwww\."),
    _p("link", r"://"),  # a URL with its scheme split off still carries the address
    # A sentence's full stop may end it; a decimal point may not (`2.5`).
    _p("link", r"(?<![\w.-])\+?\(?\d[\d\s().-]{7,15}\d(?![\w-])(?!\.\d)", "phone"),
    # 3. Order or payment state, said as done.
    _p(
        "confirmation",
        rf"\b{_SUBJECT}(?:{_S}(?P<cop>is|has{_S}(?:{_ADVERB_WORD}{_S})?been|was)|['’]s{_S}been){_S}{_ADVERB}"
        r"(?:confirmed|processed|approved|authori[sz]ed|applied|placed|issued)\b",
        "conditional",
    ),
    _p(
        "confirmation",
        rf"\bi(?:['’]ve|{_S}have){_S}(?:confirmed|processed|applied|placed|issued|authori[sz]ed)\b",
    ),
    _p("confirmation", rf"\border{_S}confirmed\b"),
    _p("confirmation", rf"\bwe(?:['’]ve|{_S}have){_S}(?:processed|placed|confirmed|issued)\b"),
    # 4. Payload subjects: refund authority (passes only as a public policy's own words) and stock.
    _p("refund_authority", rf"\bauthori[sz]\w{{0,6}}{_GAP}{_WORDS}refunds?\b", "policy_span"),
    _p("refund_authority", rf"\brefunds?{_GAP}{_WORDS}(?:authori[sz]ed|approved)\b", "policy_span"),
    _p("refund_authority", rf"\bapprov\w{{0,4}}{_GAP}{_WORDS}refunds?\b", "policy_span"),
    _p("refund_authority", r"issue_refund|apply_discount"),
    _p("stock", rf"\bout{_S}of{_S}stock\b", "live_stock"),
    _p("stock", rf"\bsold{_S}out\b", "live_stock"),
    _p("stock", rf"\bin{_S}stock\b", "live_stock"),
    _p("stock", rf"\bonly{_S}\d{{1,4}}{_S}left\b", "live_stock"),
    _p("stock", rf"\blow{_S}stock\b", "live_stock"),
    _p("stock", rf"\bjust{_S}\d{{1,4}}{_S}left\b", "live_stock"),
    _p("stock", rf"\bstock{_S}is{_S}(?:running{_S})?low\b", "live_stock"),
    # 5. False policy: a duration in a policy sentence, or a free-returns term, that no public
    #    document returned this turn states.
    Pattern("false_policy", DURATION, "duration"),
    _p("false_policy", rf"\bfree{_S}returns?\b", "free_terms"),
    _p(
        "false_policy",
        rf"\bfree{_S}(?:prepaid{_S})?return{_S}(?:shipping{_S})?labels?\b",
        "free_terms",
    ),
    _p("false_policy", rf"\bprepaid{_S}(?:return{_S})?labels?\b", "free_terms"),
    # 6. Row #12 (RAG-09, decision 0015 §7). Money: any amount passes only when a `get_price` result
    #    this turn has the same amount and currency (`live_price`); a figure from a source, from
    #    memory or from the shopper's own message does not (the user's D5).
    #    Every way round (security review pass 1, finding 1): sign or code before the amount, sign
    #    or word after it, markdown inside; an amount `money_value` cannot read exactly is blocked.
    _p("money", rf"[$£€]{_MK}\s{{0,2}}{_MK}{_AMOUNT}", "live_price"),
    _p("money", rf"\b{_CURRENCY_CODE}{_MK}\s{{0,2}}{_MK}{_AMOUNT}", "live_price"),
    _p(
        "money",
        rf"(?<![\w.,]){_AMOUNT}{_MK}\s{{0,2}}{_MK}(?:[$£€]|{_CURRENCY_WORD}\b)",
        "live_price",
    ),
    _p("money", rf"\b{_SPELLED_COUNT}[\s-]{{1,2}}(?:dollars?|euros?|bucks?|quid)\b", "live_price"),
    #    "pounds" is money unless the sentence is about weight ("weighs 2 pounds").
    _p("money", rf"(?<![\w.,]){_AMOUNT}{_MK}\s{{0,2}}pounds?\b", "pounds"),
    _p("money", rf"\b{_SPELLED_COUNT}[\s-]{{1,2}}pounds?\b", "pounds"),
    #    A word or two between the amount and the currency ("279 Canadian dollars", "279 US$"), or
    #    the code in brackets: read through a closed adjective map (re-review N2).
    _p(
        "money",
        rf"(?<![\w.,]){_AMOUNT}{_MK}\s{{1,2}}(?:[a-z.]{{1,12}}\s{{0,2}}){{1,2}}"
        rf"(?:{_CURRENCY_WORD}\b|pounds?\b|[$£€](?!\s{{0,2}}\d))",
        "qualified",
    ),
    _p(
        "money",
        rf"(?<![\w.,]){_AMOUNT}\s{{0,2}}\(\s{{0,2}}{_CURRENCY_CODE}\s{{0,2}}\)",
        "live_price",
    ),
    #    Any currency the catalog never returns, next to a digit: always blocked (a converted price
    #    comes from memory).
    _p("money", rf"{_OTHER_SIGN}\s{{0,2}}\d|\d\s{{0,2}}{_OTHER_SIGN}"),
    _p("money", rf"\b{_OTHER_CODE}\s{{0,2}}\d|\d[\d.,]{{0,12}}\s{{0,2}}{_OTHER_CODE}\b"),
    #    A size word before a currency word, read by no parser here: always blocked.
    _p("money", rf"\b{_MAGNITUDE}{_MK}\s{{0,2}}(?:{_CURRENCY_WORD}|pounds?)\b"),
    #    A bare amount said to be a price or cost: "The price is 279.00", "It costs 279".
    _p(
        "money",
        rf"\b(?:prices?|costs?)(?:\s{{1,3}}(?:is|was|are|of|at|comes{_S}to|will{_S}be)|\s{{0,2}}[:=])"
        rf"{_HEDGE}\s{{0,3}}{_MK}{_AMOUNT}",
        "bare_price",
    ),
    _p(
        "money",
        rf"\b(?:priced(?:{_S}at)?|costing|costs?){_HEDGE}\s{{1,3}}{_MK}{_AMOUNT}",
        "bare_price",
    ),
    #    Stock counts and availability (#15 L5, L9): pass only as a `check_stock` result this turn
    #    says (`live_stock`). Only a plural range is the range offered (`_RANGE`).
    _p(
        "stock",
        rf"\b{_COUNT}\s{{1,3}}(?:(?:units?|items?|pieces?|pairs?){_S})?"
        rf"(?:left|remaining|in{_S}stock|available|on{_S}hand)\b",
        "live_stock",
    ),
    #    "available" after a copula, in every spelling the second re-review found (H3): is, are,
    #    's, 're, remains, be, and a negation (n't, not). No question passes as such any more (H1,
    #    the user's decision): a hedge gets the stock sentence, which says the same thing, and a
    #    claim agreeing with every `check_stock` result this turn passes (D3).
    _p(
        "stock",
        rf"(?<=\bis\s)(?:(?:currently|still){_S})?available\b(?!{_S}in{_S}{_RANGE}(?!{_TIMELY}))",
        "live_stock",
    ),
    _p(
        "stock",
        rf"(?<=\bare\s)(?:(?:currently|still){_S})?available\b(?!{_S}in{_S}{_RANGE}(?!{_TIMELY}))",
        "live_stock",
    ),
    _p(
        "stock",
        rf"(?<=['’]s\s)(?:(?:currently|still){_S})?available\b(?!{_S}in{_S}{_RANGE}(?!{_TIMELY}))",
        "live_stock",
    ),
    _p(
        "stock",
        rf"(?<=['’]re\s)(?:(?:currently|still){_S})?available\b(?!{_S}in{_S}{_RANGE}(?!{_TIMELY}))",
        "live_stock",
    ),
    _p(
        "stock",
        rf"(?<=\bremains\s)(?:(?:currently|still){_S})?available\b(?!{_S}in{_S}{_RANGE}(?!{_TIMELY}))",
        "live_stock",
    ),
    _p(
        "stock",
        rf"(?<=\bbe\s)(?:(?:currently|still){_S})?available\b(?!{_S}in{_S}{_RANGE}(?!{_TIMELY}))",
        "live_stock",
    ),
    _p(
        "stock",
        rf"(?<=n['’]t\s)(?:(?:currently|still){_S})?available\b(?!{_S}in{_S}{_RANGE}(?!{_TIMELY}))",
        "live_stock",
    ),
    _p(
        "stock",
        rf"(?<=\bnot\s)(?:(?:currently|still){_S})?available\b(?!{_S}in{_S}{_RANGE}(?!{_TIMELY}))",
        "live_stock",
    ),
    _p("stock", rf"\b(?:currently|still){_S}available\b", "live_stock"),
    _p("stock", rf"\bavailable{_S}(?:now|today|to{_S}order)\b", "live_stock"),
    #    Quantities no tool returns (security review pass 1, finding 4): always blocked.
    _p(
        "stock",
        rf"\b(?:a{_S})?(?:dozen|few|handful|couple|several|plenty|lots?|loads|tons|hundreds|"
        rf"thousands|many|some)(?:{_S}of)?(?:{_S}(?:units?|items?|pieces?|pairs?|them|those|sizes|"
        rf"colou?rs))?{_S}(?:left|remaining|in{_S}stock|available|on{_S}hand)\b",
    ),
    _p("stock", rf"\blimited{_S}(?:stock|availability|quantities|numbers)\b"),
    #    More member-price wordings (#13b R4 left these to #12), class 1: no exemption.
    _p("promotion", rf"\bmembers?[\s-]{{1,3}}only{_S}(?:rates?|prices?|pricing|deals?|offers?)\b"),
    _p("promotion", rf"\bmember{_S}(?:rates?|deals?|offers?|savings?|exclusives?)\b"),
    _p("promotion", rf"\bloyalty{_S}(?:prices?|pricing|rates?|discounts?|rewards?|points?)\b"),
)
# A public document that says returns are free, in any of these words, lets the terms above pass:
# the US policy says "prepaid return label" and answers say "the prepaid label".
FREE_TERMS = re.compile(
    rf"\bfree{_S}(?:prepaid{_S})?returns?\b|\bprepaid{_S}(?:return{_S})?labels?\b|"
    rf"\breturns?{_S}(?:are|is){_S}free\b"
)

# Where a payload can start. Text from the earliest one in the last L characters is held.
_SPELLED_WORDS = "|".join(sorted((w for w in _NUMBER_WORDS if len(w) > 2), key=len, reverse=True))
TRIGGER = re.compile(
    r"disc|coupon|promo|vouch|staff|member|free|prepaid|%|http|www|authori|approv|refund|order|"
    r"payment|confirm|process|appl|placed|issued|stock|sold|issue_|hour|day|week|month|year|"
    r"\$|£|€|avail|currently|still|loyal|usd|gbp|eur|aud|cad|price|cost|pound|grand|thousand|"
    r"million|few|handful|several|plenty|lots|loads|many|some|tons|limited|"
    rf"code|\d|\bi['’\s]|\bwe['’\s]|\[|\+|\(|\b(?:{_SPELLED_WORDS}|hundred|dozen|night|out|only|low|half|clearance)\b"
)
# A duration is a policy term only in a sentence that holds one of these stems (the user's list,
# row #13): a reviewer's "after an hour of steady rain" is an experience, not a policy.
POLICY_STEMS = re.compile(
    r"\b(return|refund|exchange|cancel|withdraw|deliver|ship|dispatch|warrant|guarantee|back|"
    r"money|policy|window|period|label|replace|repair)"
)
# A line that heads what follows it: a markdown heading, a wholly bold line, or one ending in ":".
_HEADING = re.compile(r"\s*(?:#{1,6}\s.*|\*\*[^*\n]{1,80}\*\*:?|[^\n]{1,80}:)\s*")
_LIST_ITEM = re.compile(r"(\s*)(?:[-*+]|\d{1,3}[.)])\s+")
_MARKER_ONLY = re.compile(r"\s*(?:[-*+]|\d{1,3}[.)])\s*")
_ABBREVIATION = re.compile(r"\b(?:e\.g|i\.e|etc|approx|vs|incl|no)\.$")
# A policy sentence whose stems could not be read: non-empty, so the policy-word gate holds, and the
# duration must be vouched (fail closed).
UNREAD = frozenset({"?"})
# Present and future only (the user's decision): "If your order is placed" passes, "After your
# refund was approved" does not. Of these, the confirmation pattern can only match `is`.
PRESENT_COPULAS = frozenset({"is", "are", "gets", "will be"})
PUBLIC_DOCUMENT_PREFIXES = ("POL-", "FAQ-", "CG-", "SG-")
_CONDITIONAL = re.compile(rf"\b(?:if|when|once|after|before){_S}(?:(?:your|the|an?){_S})?$")
# A word holding a dot followed by two letters is a domain (change 3): `northwind-deals.net/x`.
_DOMAIN = re.compile(r"[\w-]\.[^\W\d_]{2}|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
_EDGE = "\"'*_`()[]{}<>.,;:!?"
_BOUNDARY = re.compile(r"[.!?](?=\s)|\n")
# Only the list marker is kept in front of a fixed sentence: a bold lead-in can hold the payload
# itself (security review pass 1, H1).
_BULLET = re.compile(r"[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+")
_MARKER = re.compile(r"\[([^\[\]\n]{1,62})\]")
_ID = r"[A-Za-z]{1,4}(?:\d|-[A-Za-z0-9])[A-Za-z0-9-]*"
_ID_LIKE = re.compile(_ID)
# A citation marker, every part ID-shaped: `[FAQ-019, POL-RETURNS-US]`.
_CITATION = re.compile(rf"\[\s*{_ID}(?:\s*,\s*{_ID})*\s*\]")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_phone(text: str) -> bool:
    digits = sum(c.isdigit() for c in text)
    return digits >= 9 and not _ISO_DATE.fullmatch(text.strip())


def word_is_link(word: str) -> bool:
    """A whole word that is a bare domain or an email, other than the care address."""
    bare = view_of(word).text.strip(_EDGE)
    if bare == ALLOWED_EMAIL:
        return False
    return bool(_DOMAIN.search(bare))


@dataclass(frozen=True)
class Source:
    """One source returned this turn, as rendered by mcp-rag (text unescaped)."""

    source_id: str
    source_type: str
    text: str

    @property
    def public_document(self) -> bool:
        """A public policy, FAQ, care or size guide: what may vouch for a duration or a
        free-returns term. Internal documents are `document` too, and so are trusted by type, so
        trust by type alone would let an internal leak vouch for itself (the user, row #13)."""
        return self.source_type == "document" and self.source_id.startswith(
            PUBLIC_DOCUMENT_PREFIXES
        )

    @property
    def public_policy(self) -> bool:
        """For refund authority only: a public policy document (POL-), as approved."""
        return self.source_type == "document" and self.source_id.startswith("POL-")


@dataclass
class LiveValues:
    """Price and stock values the catalog tools returned this turn (row #12, RAG-09; hard rule 2).

    Filled only from successful `get_price` and `check_stock` results for the shopper's own region
    (`tools.record_live`), never from text: the model cannot write one. The check reads it live, as
    it reads `seen`. `stock` keeps the last reading per variant."""

    prices: set[tuple[Decimal, str]] = field(default_factory=set)
    stock: dict[str, int] = field(default_factory=dict)


_DOLLARS = frozenset({"USD", "CAD", "AUD"})
_CURRENCY_OF_WORD = {
    "usd": frozenset({"USD"}),
    "cad": frozenset({"CAD"}),
    "aud": frozenset({"AUD"}),
    "gbp": frozenset({"GBP"}),
    "quid": frozenset({"GBP"}),
    "eur": frozenset({"EUR"}),
    "euro": frozenset({"EUR"}),
    "euros": frozenset({"EUR"}),
}
_DOLLAR_PREFIX = re.compile(r"(?:^|[^a-z])([a-z]{1,3})$")
# `$` with a prefix: these narrow it; any other prefix (NZ$, HK$, R$) is no live currency here.
_NARROWED = {"us": "USD", "ca": "CAD", "c": "CAD", "a": "AUD", "au": "AUD"}
_CENT = Decimal("0.01")
_MARKS = re.compile(r"[*_~`]")
_STRICT_AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?")
_SPELLED_WHOLE = re.compile(rf"{_SPELLED_COUNT}")
_SIGNS = {"£": frozenset({"GBP"}), "€": frozenset({"EUR"})}


def _amount(number: str) -> Decimal | None:
    """An amount read exactly, or None. Digits: thousands commas only, at most two decimals, one
    trailing full stop or comma taken as punctuation. Words: a whole spelled count."""
    number = number.strip()
    if number[-1:] in ".," and not number[-2:-1].isspace():
        trimmed = number[:-1]
        if _STRICT_AMOUNT.fullmatch(trimmed):
            number = trimmed
    if _STRICT_AMOUNT.fullmatch(number):
        return Decimal(number.replace(",", ""))
    if _SPELLED_WHOLE.fullmatch(number):
        value = number_value(number)
        return Decimal(value) if value > 0 else None
    return None


def money_value(span: str, before: str) -> tuple[Decimal, frozenset[str]] | None:
    """The amount and the currencies a money match can mean, from the matching view (casefolded).
    `$` alone is any dollar; `US$`, `C$`/`CA$` and `A$`/`AU$` narrow it, and any other prefix
    means no currency the catalog returns. None if the amount cannot be read exactly (fail
    closed: an unread amount is never vouched)."""
    text = re.sub(r"\s+", " ", re.sub(r"[()]", " ", _MARKS.sub("", span))).strip()
    if not text:
        return None
    code_first = re.fullmatch(rf"({_CURRENCY_CODE}) ?(.+)", text)
    sign_after = re.fullmatch(r"(.+?) ?([$£€])", text)
    if text[0] in "$£€":
        number, sign = text[1:], text[0]
    elif code_first:
        number, sign = code_first.group(2), ""
        currencies = _CURRENCY_OF_WORD[code_first.group(1)]
    elif sign_after:
        number, sign = sign_after.group(1), sign_after.group(2)
    else:
        found = re.fullmatch(rf"(.+?)[\s-]*({_CURRENCY_WORD})", text)
        if found is None:
            return None
        number, sign = found.group(1), ""
        currencies = _CURRENCY_OF_WORD.get(found.group(2), _DOLLARS)  # dollars, bucks
    if sign in _SIGNS:
        currencies = _SIGNS[sign]
    elif sign == "$":
        prefix = _DOLLAR_PREFIX.search(before) if text[0] == "$" else None
        if prefix:
            narrowed = _NARROWED.get(prefix.group(1))
            currencies = frozenset({narrowed}) if narrowed else frozenset()
        else:
            currencies = _DOLLARS
    amount = _amount(number)
    if amount is None:
        return None
    return amount.quantize(_CENT), currencies


# A word between an amount and its currency, read as the currency it names (re-review N2). Anything
# else names no currency the catalog returns, so the figure is blocked.
_ADJECTIVE = {
    "us": "USD",
    "u.s.": "USD",
    "american": "USD",
    "canadian": "CAD",
    "ca": "CAD",
    "australian": "AUD",
    "au": "AUD",
    "british": "GBP",
    "uk": "GBP",
    "sterling": "GBP",
}
_UNIT_CURRENCY = {
    "dollar": {"USD", "CAD", "AUD"},
    "dollars": {"USD", "CAD", "AUD"},
    "$": {"USD", "CAD", "AUD"},
    "pound": {"GBP"},
    "pounds": {"GBP"},
    "£": {"GBP"},
    "euro": {"EUR"},
    "euros": {"EUR"},
    "€": {"EUR"},
}


def qualified_value(span: str) -> tuple[Decimal, frozenset[str]] | None:
    """ "279 Canadian dollars", "199 British pounds", "279 US$": the amount, and the one currency
    the qualifier and the unit agree on. None (blocked) for any other wording."""
    text = re.sub(r"\s+", " ", _MARKS.sub("", span)).strip()
    found = re.fullmatch(r"(\S+) (.+?) ?(dollars?|pounds?|euros?|[$£€])", text)
    if found is None:
        return None
    amount = _amount(found.group(1))
    qualifier = _ADJECTIVE.get(found.group(2).strip())
    unit = _UNIT_CURRENCY.get(found.group(3), set())
    if amount is None or qualifier is None or qualifier not in unit:
        return None
    return amount.quantize(_CENT), frozenset({qualifier})


# Only a weight verb or noun right before the figure makes "N pounds" a weight (re-review N5): not
# light, lightweight or heavy, which describe a product whose price may follow.
_WEIGHT_GOVERNS = re.compile(
    r"\b(?:weigh(?:s|ing|ed)?(?:\s{1,3}in\s{1,3}at)?|weight(?:\s{1,3}(?:is|of))?)\s{1,3}"
    r"(?:(?:about|around|approximately|roughly|just|only|under|over|nearly)\s{1,3})?$"
)


def _about_weight(view: str, start: int) -> bool:
    """Whether "N pounds" at `start` is a weight: a weight verb or noun directly before it."""
    return bool(_WEIGHT_GOVERNS.search(view[max(0, start - 40) : start]))


_COUNT_IN = re.compile(rf"\d[\d,]*|{_SPELLED_COUNT}")
_NEGATIVE = re.compile(rf"\bout{_S}of{_S}stock\b|\bsold{_S}out\b")
_LOW = re.compile(r"\blow\b")


def stock_vouched(span: str, live: LiveValues, negated: bool | None = False) -> bool:
    """A stock claim passes only as this turn's `check_stock` results say (the user's D3): a count
    must equal a variant's units on hand; "out of stock" or "sold out" needs every checked variant
    at zero, and "in stock" or "available" every checked variant above zero; "low" never passes, as
    no tool says it. A negation before the phrase flips its polarity ("isn't in stock" needs every
    variant at zero; security review pass 1, finding 3), and a negated count never passes. With
    nothing checked, nothing passes."""
    if not live.stock or negated is None:  # a negation elsewhere in the clause: unvouched
        return False
    text = re.sub(r"\s+", " ", span)
    if _LOW.search(text):
        return False
    count = _COUNT_IN.search(text)
    if count is not None:
        return not negated and number_value(count.group()) in set(live.stock.values())
    positive = not _NEGATIVE.search(text)
    if negated:
        positive = not positive
    if positive:
        return all(n > 0 for n in live.stock.values())
    return all(n == 0 for n in live.stock.values())


_NEGATION = re.compile(r"\b(?:not|no|never|none|nothing|nowhere|neither|nor)\b|n['’]t\b")
# A negation that governs the stock phrase right after it: "isn't in stock", "is not available",
# "no longer in stock" (re-review N3).
_GOVERNING_NEGATION = re.compile(
    r"(?:\b(?:is|are|was|were)\s{1,3}|['’][sr]e?\s{0,3})?(?:n['’]t|\bnot|\bno\s{1,3}longer|\bnever)"
    r"\s{1,3}(?:(?:currently|still|yet)\s{1,3})?$"
)
# What ends a clause for the negation rule, dashes included.
_NEGATION_CUT = re.compile(r"[.!?;,:\n()]|\s[-‐‑]\s|[‒–—―]")


# A governing negation separated from its phrase by an aside: "is not (yet) in stock", "is not —
# for now — in stock", "is not, sadly, in stock" (second re-review M2). Read before any cut.
_NEGATED_ASIDE = re.compile(
    r"(?:n['’]t|\bnot|\bno\s{1,3}longer|\bnever)\s{0,3}"
    r"(?:\([^()]{0,30}\)|,[^,]{0,30},|[‒–—―][^‒–—―]{0,30}[‒–—―]|\s-\s[^-]{0,30}\s-)"
    r"\s{0,3}(?:(?:currently|still|yet)\s{1,3})?$"
)


def _negated(view: str, start: int) -> bool | None:
    """True when a negation governs the phrase at `start`; None when another negation sits earlier
    in the same clause ("there's no doubt the M is in stock"), which makes the claim unvouched;
    False when the clause holds none. A clause ends at punctuation or a dash, at most 40
    characters back."""
    before = view[max(0, start - 40) : start]
    if _NEGATED_ASIDE.search(before):
        return None  # a negation across an aside: unvouched, blocked at any stock level
    for cut in _NEGATION_CUT.finditer(before):
        before = before[cut.end() :]
    if _GOVERNING_NEGATION.search(before):
        return True
    return None if _NEGATION.search(before) else False


def stems_of(text: str) -> frozenset[str]:
    return frozenset(POLICY_STEMS.findall(normal(text)))


@lru_cache(maxsize=512)
def durations(text: str) -> frozenset[tuple[tuple[int, ...], str]]:
    view = view_of(text).text
    return frozenset(duration_value(view, match) for match in DURATION.finditer(view))


@dataclass
class Match:
    cls: str
    start: int  # raw positions
    end: int


_WORD = re.compile(r"\S+")


@dataclass
class OutputCheck:
    """One turn's check. `seen` and `sources` are read live: refinement searches add to them.

    Every window is measured in matching-view characters, not raw ones, because invisible
    characters fold to nothing: 130 zero-width spaces must not push a payload's start out of view
    (security review pass 2, N1). The folded form of each raw character is cached, with prefix
    sums of its length, so each delta costs time in proportion to its own length (M4)."""

    seen: set[str]
    sources: list[Source]
    counts: Counter[str] = field(default_factory=Counter)
    live: LiveValues = field(default_factory=LiveValues)  # row #12: read live, as `seen` is
    _raw: str = ""
    _deltas: list[tuple[int, int]] = field(default_factory=list)
    _consumed: int = 0  # raw text before this is decided: sent, or dropped and replaced
    _plain_since: int = 0  # raw text since here was sent unchanged, so `shown` ends with it
    _dropping: Match | None = None
    _lead: str = ""
    _drop_from: int = 0  # where the dropping state's boundary search goes on from
    _shown: str = ""
    _undecided: int | None = None  # a duration whose sentence has not shown yet if it is policy
    _scan: tuple[int, int, int] = (0, 0, 0)  # (from, scanned to, sentence start): a cache
    _folds: list[str] = field(default_factory=list)  # casefolded view of each raw character
    _exacts: list[str] = field(default_factory=list)  # the same before casefolding
    _vlen: list[int] = field(default_factory=lambda: [0])  # view length of raw[:i]
    _visible: list[int] = field(default_factory=list)  # raw positions that fold to something
    _headings: list[tuple[int, frozenset[str]]] = field(default_factory=list)  # (line start, stems)
    _line_start: int = 0  # where the line being written starts
    _matched_to: int = 0  # raw text before this has been matched (patterns are at most L wide)
    _words_to: int = 0  # every complete word before this was checked and is not a link
    _markers_to: int = 0  # every marker starting before this was dealt with
    _ws_start: int | None = None  # the whitespace run before the trailing word, if any
    _word_start: int | None = None  # the trailing word, if the text does not end in whitespace

    @property
    def shown(self) -> str:
        """What stays on screen: sent since the last `Reset`."""
        return self._shown

    # -- events in --------------------------------------------------------------------------

    # Each method returns what to send, in order: text, or `None` for a reset of the screen.

    def feed(self, text: str) -> list[str | None]:
        start = len(self._raw)
        self._raw += text
        self._deltas.append((start, len(self._raw)))
        self._index(start)
        return self._advance(final=False)

    def finish(self) -> list[str | None]:
        """The model finished: check and release the rest."""
        return self._advance(final=True)

    def reset(self, *, discard: bool = False) -> list[str | None]:
        """The model's attempt was cut: release what passes of it, then pass the reset on. A word
        cut off is dropped, not checked as if it were whole (`northwind-deals.n`), and with
        `discard` (no sources behind the text) nothing held is sent at all (security review, L2)."""
        out: list[str | None] = []
        if not discard:
            if self._word_start is not None and self._word_start >= self._consumed:
                self._cut(self._word_start, len(self._raw))
            out = self._advance(final=True)
        self._clear()
        return [*out, None]

    def abort(self) -> None:
        """A failed turn: held text is discarded (fail closed)."""
        self._clear(keep_shown=True)

    # -- positions and views ----------------------------------------------------------------

    def _clear(self, *, keep_shown: bool = False) -> None:
        self._raw, self._deltas, self._consumed, self._plain_since = "", [], 0, 0
        self._scan = (0, 0, 0)
        self._dropping, self._lead, self._drop_from = None, "", 0
        self._folds, self._exacts, self._vlen, self._visible = [], [], [0], []
        self._headings, self._line_start = [], 0
        self._matched_to = self._words_to = self._markers_to = 0
        self._ws_start = self._word_start = None
        if not keep_shown:
            self._shown = ""

    def _index(self, start: int) -> None:
        """Fold raw[start:], already appended, and track the trailing word and whitespace."""
        for position in range(start, len(self._raw)):
            exact = _fold_char(self._raw[position])
            folded = exact.casefold()
            self._exacts.append(exact)
            self._folds.append(folded)
            self._vlen.append(self._vlen[-1] + len(folded))
            if folded:
                self._visible.append(position)
            if self._raw[position] == "\n":
                self._heading_line(position)
            self._track(position)

    def _heading_line(self, end: int) -> None:
        """A line has ended at `end`: if it is a heading, keep its stems for the lines below."""
        start, self._line_start = self._line_start, end + 1
        line = self._view(start, end).text.strip()
        if len(line) <= 100:
            if _HEADING.fullmatch(line):
                self._headings.append((start, stems_of(line)))
        elif line.startswith(("#", "**")):
            self._headings.append((start, UNREAD))  # too long to read as a heading: fail closed

    def _track(self, position: int) -> None:
        if self._raw[position].isspace():
            if self._word_start is not None or self._ws_start is None:
                self._ws_start, self._word_start = position, None
        elif self._word_start is None:
            self._word_start = position

    def _reindex(self, start: int) -> None:
        """After raw text from `start` (not yet sent) changed: refold it and find the tail."""
        del self._folds[start:], self._exacts[start:], self._vlen[start + 1 :]
        del self._visible[bisect_left(self._visible, start) :]
        self._line_start = self._raw.rfind("\n", 0, start) + 1
        del self._headings[bisect_left(self._headings, (self._line_start,)) :]
        self._ws_start = self._word_start = None
        # From the start of the word that holds `_consumed`, which may be partly sent: tracking
        # from `_consumed` itself would split that word in two (pass 3, P3-1).
        origin = self._word_before(self._consumed)
        for position in self._visible[
            bisect_left(self._visible, origin) : bisect_left(self._visible, start)
        ]:
            self._track(position)
        if self._word_start is None and origin < start and not self._raw[origin].isspace():
            self._word_start = origin
        self._index(start)

    def _word_before(self, position: int) -> int:
        """Where the word holding `position` starts, looking back over at most 32 visible
        characters: past that, a domain, an email or an IP has its dot within reach anyway, and
        the walk stays bounded however long the word is (pass 4, P4-5)."""
        at = bisect_left(self._visible, position)
        for k in range(at - 1, max(-1, at - 33), -1):
            here = self._visible[k]
            if here < self._plain_since:
                return self._plain_since
            if self._raw[here].isspace():
                return here + 1
        if at == 0:
            return self._plain_since
        return max(self._plain_since, self._visible[max(0, at - 32)])

    def _back(self, position: int, width: int) -> int:
        """The raw position `width` view characters before `position`."""
        target = self._vlen[position] - width
        if target <= 0:
            return 0
        return bisect_right(self._vlen, target) - 1

    def _wide(self, start: int, end: int) -> int:
        """How many view characters raw[start:end] folds to."""
        return self._vlen[end] - self._vlen[start]

    def _view(self, start: int, end: int) -> View:
        text: list[str] = []
        index: list[int] = []
        exact: list[str] = []
        exact_index: list[int] = []
        # Only positions that fold to something: a long invisible run costs nothing (P3-2).
        lo, hi = bisect_left(self._visible, start), bisect_left(self._visible, end)
        for position in self._visible[lo:hi]:
            folded, unfolded = self._folds[position], self._exacts[position]
            text.append(folded)
            index.extend([position] * len(folded))
            exact.append(unfolded)
            exact_index.extend([position] * len(unfolded))
        return View("".join(text), tuple(index), "".join(exact), tuple(exact_index))

    def _sentence_start(self, position: int) -> int:
        """Where the sentence holding `position` starts. Scans only what it has not scanned
        before, so a long answer costs linear time (security review, M4)."""
        origin, scanned, start = self._scan
        if origin != self._plain_since or position < scanned:
            scanned = start = self._plain_since
        # A boundary is `[.!?]` and then whitespace, so the last character scanned is re-read.
        for boundary in _BOUNDARY.finditer(self._raw, max(start, scanned - 1), position):
            start = boundary.end()
        self._scan = (self._plain_since, position, start)
        while start < position and self._raw[start] in " \t\r\n":
            start += 1
        return start

    def _cut(self, start: int, end: int, new: str = "") -> None:
        """Replace raw text [start, end), none of it sent yet, with `new`, and keep every position
        in step (pass 2, N3: `new` may be longer than what it replaces)."""
        self._raw = self._raw[:start] + new + self._raw[end:]
        shift = len(new) - (end - start)

        def moved(position: int) -> int:
            if position <= start:
                return position
            if position >= end:
                return position + shift
            return min(position, start + len(new))

        self._deltas = [(moved(a), moved(b)) for a, b in self._deltas]
        if self._scan[1] > start:
            self._scan = (-1, 0, 0)  # the text the cache scanned has changed
        if self._dropping is not None:
            self._dropping.end = moved(self._dropping.end)
            # A full stop just before the cut must still be found (pass 3, P3-7).
            self._drop_from = min(moved(self._drop_from), max(self._dropping.end, start - 1))
        self._matched_to = min(self._matched_to, start)
        # The word holding the cut is checked again from its start, even if part of it was sent:
        # the repair takes it back (pass 3, P3-1).
        self._words_to = min(self._words_to, self._word_before(start))
        self._markers_to = min(self._markers_to, start)
        self._reindex(start)

    # -- the checks -------------------------------------------------------------------------

    def _exempt(self, pattern: Pattern, view: View, found: re.Match[str], raw: str) -> bool:
        start, end = found.start(), found.end()
        if pattern.exempt is None:
            return False
        if pattern.exempt == "phone":
            return not _is_phone(raw)
        if pattern.exempt == "conditional":
            copula = re.sub(r"\s+", " ", found.groupdict().get("cop") or "")
            introduced = _CONDITIONAL.search(view.text[max(0, start - 40) : start])
            return bool(introduced) and copula in PRESENT_COPULAS
        if pattern.exempt == "duration":
            return False  # decided in `_first_match`: the policy-word gate, then `_vouched`
        span = re.sub(r"\s+", " ", view.text[start:end])
        if pattern.exempt == "free_terms":
            return any(
                s.public_document and FREE_TERMS.search(normal(s.text)) for s in self.sources
            )
        if pattern.exempt == "policy_span":
            return any(s.public_policy and span in normal(s.text) for s in self.sources)
        if pattern.exempt == "live_price":
            value = money_value(span, view.text[max(0, start - 4) : start])
            if value is None:
                return False
            amount, currencies = value
            return any((amount, currency) in self.live.prices for currency in currencies)
        if pattern.exempt == "pounds":
            if _about_weight(view.text, start):
                return True
            words = re.fullmatch(r"(.+?)[\s-]*pounds?", span.strip())
            sterling = _amount(words.group(1)) if words else None
            return sterling is not None and (sterling.quantize(_CENT), "GBP") in self.live.prices
        if pattern.exempt == "bare_price":
            figure = re.search(rf"{_AMOUNT}$", _MARKS.sub("", span).strip())
            bare = _amount(figure.group()) if figure else None
            return bare is not None and any(
                bare.quantize(_CENT) == have for have, _currency in self.live.prices
            )
        if pattern.exempt == "qualified":
            qualified = qualified_value(span)
            return qualified is not None and any(
                (qualified[0], currency) in self.live.prices for currency in qualified[1]
            )
        if pattern.exempt == "live_stock":
            return stock_vouched(span, self.live, _negated(view.text, start))

        raise ValueError(f"unknown exemption: {pattern.exempt}")  # fail closed on a new kind

    def _vouched(self, text: str, match: re.Match[str]) -> bool:
        """A policy duration passes if a public, non-internal document returned this turn states
        the same duration; retrieval returns only the shopper's region or `ALL` (RAG-11). Class 5
        stops relayed or injected numbers. A real policy number on the wrong term ("return any
        item within two years", from the repair warranty) is a grounding error left to #13b's
        judge (the user's decision, 2026-11-11)."""
        wanted = duration_value(text, match)
        have: set[tuple[tuple[int, ...], str]] = set()
        for source in self.sources:
            if source.public_document:
                have |= durations(source.text)
        return wanted in have

    def _policy_context(self, start: int, end: int, final: bool) -> frozenset[str] | None:
        """Whether the sentence holding a duration is about policy; None while it cannot be told.
        The stem may come after the duration, so this waits for the sentence's end, but at most L
        view characters past the duration: a sentence still open there counts as policy (fail
        closed). So does one that starts more than 4L back, or that pads either side with more
        than 16L raw characters, so the region looked at stays bounded."""
        sentence = self._sentence_start(start)
        if self._wide(sentence, start) > 4 * L or start - sentence > 16 * L:
            return UNREAD
        found = _BOUNDARY.search(self._raw, end)
        # At the end of the stream the text's own end closes the sentence.
        boundary = found.start() if found else (len(self._raw) if final else None)
        if boundary is not None and self._wide(end, boundary) <= L:
            if boundary - end > 16 * L:
                return UNREAD
            region = self._view(sentence, boundary).text
        elif self._wide(end, len(self._raw)) > L + 1:  # a boundary at L needs the next character
            # Still open L past the duration: policy (fail closed), with the stems read so far.
            cut = bisect_right(self._vlen, self._vlen[end] + L) - 1
            if cut - end > 16 * L:
                return UNREAD  # the window itself is padded with invisible characters
            region = self._view(sentence, max(end, cut)).text
        elif len(self._raw) - end > 16 * L:
            return UNREAD  # still undecided after this much padding
        else:
            return None
        stems = frozenset(POLICY_STEMS.findall(region)) | self._context_stems(sentence)
        return stems if boundary is not None and self._wide(end, boundary) <= L else stems or UNREAD

    def _parent_stems(self, sentence: int) -> frozenset[str]:
        """For a list item, the stems of its parent line. Measured in view characters (pass 3,
        P3-3): a parent more than 16L back counts as policy with unreadable stems (fail closed)."""
        line_start = self._raw.rfind("\n", 0, sentence) + 1
        back = self._back(line_start, 16 * L)
        truncated = back > 0 and self._raw[back - 1] != "\n"
        lines = self._view(back, line_start).text.split("\n")
        marked = [(not (truncated and i == 0), line) for i, line in enumerate(lines)]
        complete = [(whole, line) for whole, line in marked if line.strip()]
        item = _LIST_ITEM.match(self._raw, line_start)
        if item:
            depth = len(item.group(1))
            for whole, line in reversed(complete):
                if not whole:
                    return UNREAD
                above = _LIST_ITEM.match(line)
                if above is None or len(above.group(1)) < depth:
                    return stems_of(line)
            return UNREAD if truncated else frozenset()
        return frozenset()

    def _context_stems(self, sentence: int) -> frozenset[str]:
        """Policy context for a sentence (M2 and P3-3, the user's decisions): its list parent,
        the nearest heading above it, and the previous sentence in the same paragraph."""
        line_start = self._raw.rfind("\n", 0, sentence) + 1
        stems = self._parent_stems(sentence)
        at = bisect_left(self._headings, (line_start,)) - 1
        if at >= 0:
            stems |= self._headings[at][1]
        return stems | self._previous_sentence_stems(sentence)

    def _previous_sentence_stems(self, sentence: int) -> frozenset[str]:
        """The sentence before this one, unless a blank line comes between. One longer than 16L
        view characters counts as policy with unreadable stems (fail closed)."""
        back = self._back(sentence, 16 * L)
        before = self._view(back, sentence).text
        paragraph = re.split(r"\n[ \t]*\n", before)
        if len(paragraph) > 1 and not paragraph[-1].strip():
            return frozenset()  # the paragraph starts here
        text = paragraph[-1].rstrip()
        if not text:
            return frozenset()
        parts = [part for part in re.split(r"(?<=[.!?])\s+|\n", text) if part.strip()]
        if not parts:
            return frozenset()
        # A list number or an abbreviation is not a sentence (pass 4, P4-2): join back over it.
        previous = parts.pop()
        while parts and (_MARKER_ONLY.fullmatch(previous) or _ABBREVIATION.search(parts[-1])):
            previous = f"{parts.pop()} {previous}"
        if not parts and len(paragraph) == 1 and back > 0:
            return UNREAD
        return stems_of(previous)

    def _clean_markers(self, final: bool) -> None:
        """An invented citation marker comes out of the held text before anything is matched, so
        what is checked is exactly what is sent (security review pass 1, H2): `dis[X1]count` is
        matched as `discount`. A space before a removed marker goes with it when a full stop or
        other closing punctuation, then whitespace, follows."""
        position = max(self._consumed, self._markers_to)
        while (found := _MARKER.search(self._raw, position)) is not None:
            fixed = self._fix_marker(found)
            if fixed == found.group():
                position = found.end()
                continue
            start, end = found.start(), found.end()
            after = self._raw[end : end + 2]
            if not fixed and len(after) < 2 and not final:
                break  # whether the space before it goes depends on what follows
            self.counts["marker"] += 1
            if fixed:
                self._cut(start, end, fixed)
            else:
                closing = re.match(r"[.,;:!?](?:\s|$)", after)
                if closing and start > self._consumed and self._raw[start - 1] in " \t":
                    start -= 1
                self._cut(start, end)
            # A cut can close a marker that began before it: `[[X1]Y2]` (pass 2, N4).
            opened = self._raw.rfind("[", self._consumed, start)
            position = opened if opened >= 0 else start
        # A marker is at most 64 characters, so one starting earlier than this is complete.
        self._markers_to = max(self._consumed, min(position, len(self._raw) - 64))

    def _first_match(self, final: bool) -> Match | None:
        """The earliest blocking match from the current sentence on, not wholly sent already.
        Every pattern is at most L view characters wide, so this looks back at most 2L before
        what is unsent, and 3L before what was matched last time (M4, N1)."""
        origin = max(
            self._sentence_start(self._consumed),
            self._back(self._consumed, 2 * L),
            self._back(self._matched_to, 3 * L),
        )
        view = self._view(origin, len(self._raw))
        self._matched_to = len(self._raw)
        best: Match | None = None
        self._undecided = None
        for pattern in PATTERNS:
            haystack = view.exact if pattern.exact else view.text
            for found in pattern.regex.finditer(haystack):
                start, end = view.raw_span(found.start(), found.end(), exact=pattern.exact)
                if end <= self._consumed:
                    continue
                if end >= len(self._raw) and not final:
                    continue  # it may go on: it is held, and is checked when it ends
                if pattern.cls == "money" and not final and re.fullmatch(r"[.,]", self._raw[end:]):
                    continue  # "$263." at the end of a delta may go on with its decimals (W1)
                if pattern.exempt == "duration":
                    stems = self._policy_context(start, end, final)
                    if stems is None:  # hold it until its sentence shows whether it is policy
                        if self._undecided is None or start < self._undecided:
                            self._undecided = start
                        continue
                    if not stems or self._vouched(view.text, found):
                        continue
                if self._exempt(pattern, view, found, self._raw[start:end]):
                    continue
                if best is None or start < best.start:
                    best = Match(pattern.cls, start, end)
                break
        unparsed = self._unparsed_duration(view, final)
        if unparsed is not None and (best is None or unparsed.start < best.start):
            best = unparsed
        checked = max(self._plain_since, self._words_to)
        # Only complete words: the trailing one is still being written (unless the stream ended).
        if final or self._word_start is None:
            complete = len(self._raw)
        else:
            complete = self._word_start
        for word in _WORD.finditer(self._raw, checked, max(checked, complete)):
            start, end = word.start(), word.end()
            if word_is_link(word.group()):
                if best is None or start < best.start:
                    best = Match("link", start, end)
                break
            checked = end
        self._words_to = checked
        return best

    def _unparsed_duration(self, view: View, final: bool) -> Match | None:
        """The earliest duration span in a policy sentence holding a number the parser did not
        consume exactly (the user's fail-closed rule). Such a span is never vouched."""
        consumed: list[tuple[int, int]] = []
        for match in DURATION.finditer(view.text):
            lead = max(0, match.start() - 45)
            joined = _JOINED.search(view.text[lead : match.start()])
            consumed.append((lead + joined.start("n") if joined else match.start(), match.end()))
        for unit in _UNIT_WORD.finditer(view.text):
            first = None
            base = max(0, unit.start() - 60)
            # Only the unit's own sentence: a citation or a number before a full stop is not part
            # of the span (found by the whole-answer read, EV-062).
            for boundary in _BOUNDARY.finditer(view.text, base, unit.start()):
                base = boundary.end()
            if "fortnight" in unit.group():
                first = unit.start()  # the unit is itself a number
            # A real citation marker is masked: neither a number nor a counted word. Any other
            # bracket is markup round a token (pass 5, P5-1: "[365-ish days]" is not a citation).
            text = _CITATION.sub(lambda m: " " * len(m.group()), view.text[base : unit.end()])
            tokens = [
                (base + t.start(), base + t.end(), t.group()) for t in re.finditer(r"\S+", text)
            ]
            # The unit's own token counts up to the unit word: `**365**-day`, `30-day`.
            if tokens and tokens[-1][0] < unit.start():
                lo, _, word = tokens[-1]
                tokens[-1] = (lo, unit.start(), word[: unit.start() - lo])
            elif tokens:
                tokens.pop()
            # Up to three words before the unit, not counting filler such as "business" or "or
            # so": "twenty or so business days" is one span, "2 items within 30 days" is not.
            span, content = [tokens.pop()] if tokens and tokens[-1][1] == unit.start() else [], 0
            while tokens and content < 3:
                token = tokens.pop()
                span.append(token)
                if _MARKUP.sub("", token[2]).strip(".,;:!?") not in _FILLER:
                    content += 1
            for lo, _hi, word in span:
                core = _MARKUP.sub("", word)
                if not core or not numberish(core):
                    continue
                alnum = [k for k, char in enumerate(word) if char.isalnum()]
                if not alnum:
                    continue
                begin, finish = lo + alnum[0], lo + alnum[-1] + 1
                if any(lo_ <= begin and finish <= hi_ for lo_, hi_ in consumed):
                    continue  # read whole by the parser
                first = begin if first is None else min(first, begin)
            if first is None:
                continue
            start, end = view.raw_span(first, unit.end())
            if end <= self._consumed or (end >= len(self._raw) and not final):
                continue
            stems = self._policy_context(start, end, final)
            if stems is None:
                if self._undecided is None or start < self._undecided:
                    self._undecided = start
                continue
            if stems:
                return Match("false_policy", start, end)
        return None

    def _safe_end(self, final: bool) -> int:
        if final:
            return len(self._raw)
        end = len(self._raw)
        # The trailing word, and the whitespace before it, so a marker removed after a space
        # takes that space with it however the deltas fell.
        tail = self._ws_start if self._ws_start is not None else self._word_start
        if tail is not None:
            end = max(self._consumed, tail)
        window = max(self._consumed, self._back(len(self._raw), L - 1))
        view = self._view(window, len(self._raw))
        trigger = TRIGGER.search(view.text)
        if trigger:
            end = min(end, view.index[trigger.start()])
        if self._undecided is not None:
            end = min(end, self._undecided)
        return end

    def _emit(self, start: int, end: int) -> list[str | None]:
        """Raw text [start, end) as sent, in the model's own deltas."""
        if end <= start:
            return []
        self._shown += self._raw[start:end]
        pieces: list[str | None] = []
        for delta_start, delta_end in self._deltas:
            if delta_start >= end:
                break
            piece = self._raw[max(start, delta_start) : min(end, delta_end)]
            if piece and delta_end > start:
                pieces.append(piece)
        return pieces

    def _fix_marker(self, found: re.Match[str]) -> str:
        parts = [part.strip() for part in found.group(1).split(",")]
        if not all(_ID_LIKE.fullmatch(part) for part in parts):
            return found.group()  # not a citation marker
        kept = [part for part in parts if part in self.seen]
        if len(kept) == len(parts):
            return found.group()
        return f"[{', '.join(kept)}]" if kept else ""

    def _release_to(self, safe: int) -> list[str | None]:
        """Up to the last delta end at or before `safe`, or `safe` itself after a block."""
        target = self._consumed
        for _, delta_end in self._deltas:
            if delta_end > safe:
                break
            if delta_end > self._consumed:
                target = delta_end
        if safe == len(self._raw):
            target = safe
        out = self._emit(self._consumed, target)
        self._consumed = max(self._consumed, target)
        while self._deltas and self._deltas[0][1] <= self._consumed:
            self._deltas.pop(0)
        return out

    def _advance(self, final: bool) -> list[str | None]:
        out: list[str | None] = []
        while True:
            self._clean_markers(final)
            if self._dropping is not None:
                origin = max(self._dropping.end, self._drop_from)
                boundary = _BOUNDARY.search(self._raw, origin)
                if boundary is None and not final:
                    self._drop_from = max(origin, len(self._raw) - 1)
                    return out
                end = len(self._raw) if boundary is None else boundary.start()
                if boundary is not None and self._raw[boundary.start()] != "\n":
                    end = boundary.end()
                fixed = FIXED_SENTENCES[self._dropping.cls]
                if _repeats(self._shown, fixed):
                    # The same fixed sentence is already the last thing on screen, the model's own
                    # copy or a replacement: say it once (row #12, EV-090). Still counted.
                    while (
                        end < len(self._raw)
                        and self._raw[end] in " \t"
                        and self._shown[-1:].isspace()
                    ):
                        end += 1
                else:
                    sentence = self._lead + fixed
                    self._shown += sentence
                    out.append(sentence)
                self._consumed = self._plain_since = end
                self._dropping, self._lead = None, ""
                self._matched_to = self._words_to = end
            match = self._first_match(final)
            if match is None:
                out += self._release_to(self._safe_end(final))
                return out
            self.counts[match.cls] += 1
            start = self._sentence_start(match.start)
            bullet = _BULLET.match(self._raw, start)
            self._lead = bullet.group() if bullet else ""
            if start >= self._consumed:
                out += self._emit(self._consumed, start)
            else:
                # Part of the sentence was sent: take it back, and send again what passed.
                keep = len(self._shown) - (self._consumed - start)
                self._shown = self._shown[:keep]
                out.append(None)
                if self._shown:
                    out.append(self._shown)
            self._consumed = start
            self._dropping, self._drop_from = match, match.end


def _repeats(shown: str, fixed: str) -> bool:
    """Whether the text on screen already ends with `fixed`, compared in the matching view."""
    tail = shown.rstrip()[-(len(fixed) + 16) :]
    return bool(tail) and normal(tail).endswith(normal(fixed))


def check_text(
    text: str,
    sources: Sequence[Source] = (),
    seen: Iterable[str] = (),
    live: LiveValues | None = None,
) -> str:
    """The whole of `text` through the check at once: what the shopper would be left seeing."""
    check = OutputCheck(set(seen), list(sources), live=live or LiveValues())
    check.feed(text)
    check.finish()
    return check.shown


def hits(
    text: str,
    sources: Sequence[Source] = (),
    seen: Iterable[str] = (),
    live: LiveValues | None = None,
) -> dict[str, int]:
    """What the check would block in `text`, by class. For tests and evidence: never logged."""
    check = OutputCheck(set(seen), list(sources), live=live or LiveValues())
    check.feed(text)
    check.finish()
    return {cls: n for cls, n in check.counts.items() if cls != "marker"}


LIVE_EXEMPTIONS = frozenset({"live_price", "live_stock", "pounds", "bare_price", "qualified"})


def live_backed(text: str, live: LiveValues) -> int:
    """How many sentences of `text` hold a claim that passes only because a catalog tool returned
    it this turn: a money or stock match let through by `live_price` or `live_stock`. One per
    sentence, so "3 left in stock" is one claim. The evals read it so that a live price, which no
    retrieved source can back, does not count toward the citation requirement (row #12, the user's
    change 3). Never logged."""
    check = OutputCheck(set(), [], live=live)
    view = view_of(text)
    starts = [0, *(b.end() for b in _BOUNDARY.finditer(text))]
    sentences: set[int] = set()
    for pattern in PATTERNS:
        if pattern.exempt not in LIVE_EXEMPTIONS:
            continue
        for found in pattern.regex.finditer(view.text):
            start, end = view.raw_span(found.start(), found.end())
            if pattern.exempt == "pounds" and _about_weight(view.text, found.start()):
                backed = False  # a weight, not a price
            else:
                backed = check._exempt(pattern, view, found, text[start:end])
            if backed:
                sentences.add(bisect_right(starts, start) - 1)
    return len(sentences)
