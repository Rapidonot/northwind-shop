"""SA-04 and SA-03 scoring (rows #30, #22): deterministic, no judge.

The rules are the frozen guide's, `docs/reviews/2026-11-25-30-labelling-guide.md`, which was pinned
before this module existed; a change of rule needs the user (plan, stop 5). Each scorer returns the
problems it found, so an empty list is a pass.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from northwind_assistant.output_check import FIXED_SENTENCES, citation_ids

_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_LIST_LINE = re.compile(r"\s*(?:[-•]|\d{1,2}[.)])\s+")
_ASKS = re.compile(r"(?:please\s+)?tell me\b|let me know\b|could you\b|can you\b", re.IGNORECASE)
_GROUP = re.compile(r"\[([^\[\]]+)\]")
_PRODUCT_ID = re.compile(r"P\d{4}(?:-.*)?")

# The guide's words, with the user's answer A and clarification 8.
SLOT_WORDS: Mapping[str, tuple[str, ...]] = {
    "size": tuple(
        "size,fit,dimension,measurement,measure,twin,full,queen,king,chest,waist,inseam,mattress,"
        "how big,how large,how tall,wide,inches,feet,how long,length,seat,guest,people,bath towel,"
        "bath sheet,hand towel".split(",")
    ),
    "budget": ("budget", "price range", "spend", "spending", "price limit"),
    "use": tuple(
        "use,using,used,activity,activities,occasion,weather,conditions,climate,season,temperature,"
        "where,what for,indoor,outdoor,hiking,travel,everyday,layering,room,warm,warmth,cold,"
        "sleep".split(",")
    ),
}
_PAY = r"how much\b[^.?!\n]*\bpay"


def _slot_pattern(slot: str) -> re.Pattern[str]:
    words = [rf"{re.escape(word)}(?:s|es)?" for word in SLOT_WORDS[slot]]
    if slot == "budget":
        words.append(_PAY)
    return re.compile(rf"\b(?:{'|'.join(words)})\b", re.IGNORECASE)


_DASHES = re.compile("[‐-―−]")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "”": '"', "“": '"', "″": '"'})


def _plain(text: str) -> str:
    """Markdown emphasis removed, quotes and dashes folded; line breaks kept."""
    return _DASHES.sub(
        "-", re.sub(r"(?m)^[ \t]*\*[ \t]+", "- ", text).translate(_QUOTES)
    ).translate(str.maketrans("", "", "*_`#"))


def fold(text: str) -> str:
    """Lower case, dashes as hyphens, markdown removed, commas inside numbers dropped, spaces
    collapsed. Both sides of every comparison go through it."""
    return " ".join(re.sub(r"(?<=\d),(?=\d{3})", "", _plain(text).lower()).split())


def has_wording(text: str, wording: str) -> bool:
    """Whether `wording` is in `text` by whole words (the guide's clarification 1)."""
    found = re.escape(fold(wording))
    before = r"(?<![a-z0-9])" if fold(wording)[:1].isalnum() else ""
    after = r"(?![a-z0-9])" if fold(wording)[-1:].isalnum() else ""
    return re.search(before + found + after, fold(text)) is not None


def _pieces(answer: str) -> list[tuple[str, bool]]:
    """Every sentence and list line, with whether it is asking text: a sentence that ends in "?" or
    begins as a request, and the list lines directly under one."""
    out: list[tuple[str, bool]] = []
    under = False  # the last sentence of the text above asks, and only list lines have come since
    for line in _plain(answer).splitlines():
        if not line.strip():
            continue
        marker = _LIST_LINE.match(line)
        if marker and under:
            out.append((line, True))
            continue
        body = line[marker.end() :] if marker else line
        for sentence in _SENTENCES.split(body.strip()):
            under = sentence.rstrip().endswith("?") or bool(_ASKS.match(sentence.strip()))
            out.append((sentence, under))
    return out


def asking_text(answer: str) -> str:
    return "\n".join(text for text, asks in _pieces(answer) if asks)


def _no_answer(answer: str) -> bool:
    rest = answer
    for fixed in FIXED_SENTENCES.values():
        rest = rest.replace(fixed, "")
    return not rest.strip()


def _names_or_cites_a_product(answer: str) -> bool:
    from northwind_rag.query import names_catalog_product  # reads the catalog on first use

    cited = [part for group in _GROUP.findall(answer) for part in citation_ids(group)]
    return names_catalog_product(_plain(answer)) or any(_PRODUCT_ID.fullmatch(c) for c in cited)


def sa04_problems(item: Mapping[str, Any], answer: str) -> list[str]:
    """SA-04: the answer asks about a slot the label accepts, and recommends nothing."""
    if _no_answer(answer):
        return ["no answer"]
    problems = []
    asked = asking_text(answer)
    if not any(_slot_pattern(slot).search(asked) for slot in item["accepts"]):
        problems.append(f"no question about {' or '.join(item['accepts'])}")
    if _names_or_cites_a_product(answer):
        problems.append("names or cites a product")
    return problems


_NEGATED = r"(?:n't|\bnot|\bno longer|\bnever)\s+(?:currently\s+|still\s+|yet\s+)?"
_OUT = re.compile(
    rf"\bout of stock\b|\bsold out\b|\bunavailable\b|{_NEGATED}(?:in stock|available)\b"
    r"|\bnone left\b|(?<![\d.,])0 left\b|\bno stock left\b"
)
# The stock wordings of IC-039's and IC-040's `absent`, read as the live answer is, so a
# hyphen hides neither (clarification 8).
_STOCK_WORDINGS = frozenset(
    (
        "out of stock",
        "sold out",
        "back in stock",
        "restock",
        "currently unavailable",
        "not in stock",
        "is in stock",
    )
)


def _stock_text(text: str) -> str:
    """Folded, with hyphens as spaces: "out-of-stock" (clarification 8)."""
    return " ".join(fold(text).replace("-", " ").split())


def _says(answer: str, wording: str) -> bool:
    if has_wording(answer, wording):
        return True
    return wording in _STOCK_WORDINGS and has_wording(_stock_text(answer), wording)


_IN = re.compile(r"\b(?:in stock|available)\b")


def _says_out(answer: str) -> bool:
    return _OUT.search(_stock_text(answer)) is not None


def _says_in(answer: str, units: int) -> bool:
    if _IN.search(re.sub(_OUT, " ", _stock_text(answer))):
        return True
    count = (
        rf"(?<![\d.,]){units}\s+(?:(?:units?|items?|pieces?|pairs?)\b|(?:left|remaining|on hand)\b)"
    )
    return re.search(count, fold(answer)) is not None


def sa03_problems(
    item: Mapping[str, Any],
    answer: str,
    cited: Sequence[str],
    checked: Mapping[str, int],
    units: int | None,
) -> list[str]:
    """SA-03. `cited` is what the turn sent as citations, `checked` the variants its `check_stock`
    results returned, and `units` what the live store holds for the labelled variant now."""
    if _no_answer(answer):
        return ["no answer"]
    problems = [
        f"cites none of {', '.join(group)}"
        for group in item["cites"]
        if not set(group) & set(cited)
    ]
    problems += [
        f"no fact: {group[0]}"
        for group in item["facts"]
        if not any(has_wording(answer, wording) for wording in group)
    ]
    problems += [f"says: {wording}" for wording in item["absent"] if _says(answer, wording)]
    if item["intent"] == "recommendation" and all(asks for _, asks in _pieces(answer)):
        problems.append("only a question")
    for variant in item["variants"]:
        if variant not in checked:
            problems.append(f"check_stock not called for {variant}")
        elif units is None:
            problems.append(f"no live stock for {variant}")
        elif units > 0 and not _says_in(answer, units):
            problems.append("does not say it is in stock")
        elif units == 0 and not _says_out(answer):
            problems.append("does not say it is out of stock")
    return problems
