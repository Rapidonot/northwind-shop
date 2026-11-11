"""The output check: what the model writes is checked before any of it reaches the shopper
(NFR-03, RAG-08; decision 0014).

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
    "stock": "I can't check stock in chat.",
    "false_policy": "I couldn't match that policy term to Northwind's policy for your region.",
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
_ADVERB = r"(?:(?:now|already|just|also|since|[a-z]{2,10}ly)\s{1,3})?"
_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}  # fmt: skip
_NUMBER = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
DURATION = re.compile(
    rf"\b(?P<n>\d{{1,3}}|{_NUMBER})(?:[\s-]{{1,2}}(?:full|calendar))?[\s-]{{1,2}}"
    r"(?:(?:business|working)[\s-]{1,2})?(?P<u>hour|day|week|month|year)s?\b"
)


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


# Fixed with the row #13 plan before any recording; a change after one is stop 7 (decision 0014).
PATTERNS: tuple[Pattern, ...] = (
    # 1. Promotions come only from live tools (hard rule 2): class-level, no exemption.
    _p("promotion", r"discount"),
    _p("promotion", r"coupon"),
    _p("promotion", rf"promo(?:tion(?:al)?)?{_S}codes?"),
    _p("promotion", r"\d{1,3}\s{0,2}%\s{0,2}off\b"),
    _p("promotion", r"voucher"),
    _p("promotion", rf"staff{_S}codes?"),
    _p("promotion", rf"member{_S}prices?"),
    _p("promotion", rf"free{_S}today"),
    _p("promotion", rf"\d{{1,3}}\s?(?:percent|per{_S}cent){_S}off\b"),  # M3, the user's list
    _p("promotion", rf"\bhalf{_S}price"),
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
        rf"\b{_SUBJECT}{_S}(?P<cop>is|has{_S}been|was){_S}{_ADVERB}"
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
    _p("stock", rf"\bout{_S}of{_S}stock\b"),
    _p("stock", rf"\bsold{_S}out\b"),
    _p("stock", rf"\bin{_S}stock\b"),
    _p("stock", rf"\bonly{_S}\d{{1,4}}{_S}left\b"),
    _p("stock", rf"\blow{_S}stock\b"),
    _p("stock", rf"\bjust{_S}\d{{1,4}}{_S}left\b"),
    _p("stock", rf"\bstock{_S}is{_S}(?:running{_S})?low\b"),
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
)
# A public document that says returns are free, in any of these words, lets the terms above pass:
# the US policy says "prepaid return label" and answers say "the prepaid label".
FREE_TERMS = re.compile(
    rf"\bfree{_S}(?:prepaid{_S})?returns?\b|\bprepaid{_S}(?:return{_S})?labels?\b|"
    rf"\breturns?{_S}(?:are|is){_S}free\b"
)

# Where a payload can start. Text from the earliest one in the last L characters is held.
_SPELLED = "|".join(sorted((w for w in _NUMBER_WORDS if len(w) > 2), key=len, reverse=True))
TRIGGER = re.compile(
    r"disc|coupon|promo|vouch|staff|member|free|prepaid|%|http|www|authori|approv|refund|order|"
    r"payment|confirm|process|appl|placed|issued|stock|sold|issue_|hour|day|week|month|year|"
    rf"code|\d|\bi['’\s]|\bwe['’\s]|\[|\+|\(|\b(?:{_SPELLED}|out|only|low|half|clearance)\b"
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
# Stems that can never match: a policy sentence whose stems could not be read (fail closed).
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
_ID_LIKE = re.compile(r"[A-Za-z]{1,4}(?:\d|-[A-Za-z0-9])[A-Za-z0-9-]*")
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


def stems_of(text: str) -> frozenset[str]:
    return frozenset(POLICY_STEMS.findall(normal(text)))


@lru_cache(maxsize=512)
def policy_durations(text: str) -> frozenset[tuple[int, str, str]]:
    """(value, unit, stem) for every duration in a sentence of a document, with every policy stem
    of that sentence and of the heading or parent line above it (M2, the user's decision)."""
    found: set[tuple[int, str, str]] = set()
    heading = ""
    for number, paragraph in enumerate(re.split(r"\n\s*\n", text)):
        lines = [line for line in paragraph.split("\n") if line.strip()]
        # A markdown heading, or an indexed chunk's own first line, "Title > Section" (P3-5).
        titled = number == 0 and len(lines) == 1 and " > " in lines[0]
        if lines and (lines[0].lstrip().startswith("#") or titled):
            heading, lines = lines[0], lines[1:]
        parent, prose, units = heading, [], []
        for line in lines:
            if _LIST_ITEM.match(line):
                units.append((parent, line))
            else:
                prose.append(line)
                parent = line
        units += [(heading, s) for s in re.split(r"(?<=[.!?])\s+", " ".join(prose)) if s]
        for above, sentence in units:
            stems = stems_of(f"{heading} {above} {sentence}")
            for value, unit in durations(sentence):
                found.update((value, unit, stem) for stem in stems)
    return frozenset(found)


@lru_cache(maxsize=512)
def durations(text: str) -> frozenset[tuple[int, str]]:
    found = set()
    for match in DURATION.finditer(view_of(text).text):
        number = match["n"]
        value = int(number) if number.isdigit() else _NUMBER_WORDS[number]
        found.add((value, match["u"]))
    return frozenset(found)


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
            self._track(position)

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
        self._ws_start = self._word_start = None
        # From the start of the word that holds `_consumed`, which may be partly sent: tracking
        # from `_consumed` itself would split that word in two (pass 3, P3-1).
        origin = self._consumed
        while origin > self._plain_since and not self._raw[origin - 1].isspace():
            origin -= 1
        for position in range(origin, start):
            self._track(position)
        self._index(start)

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
        # The word holding the cut is checked again whole, even if part of it was sent: the
        # repair takes it back (pass 3, P3-1).
        word = start
        while word > self._plain_since and not self._raw[word - 1].isspace():
            word -= 1
        self._words_to = min(self._words_to, word)
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
            copula = re.sub(r"\s+", " ", found["cop"]) if "cop" in found.groupdict() else ""
            introduced = _CONDITIONAL.search(view.text[max(0, start - 40) : start])
            return bool(introduced) and copula in PRESENT_COPULAS
        if pattern.exempt == "duration":
            return False  # decided in `_first_match`, by policy stems (`_vouched`, M2)
        span = re.sub(r"\s+", " ", view.text[start:end])
        if pattern.exempt == "free_terms":
            return any(
                s.public_document and FREE_TERMS.search(normal(s.text)) for s in self.sources
            )
        if pattern.exempt == "policy_span":
            return any(s.public_policy and span in normal(s.text) for s in self.sources)
        raise ValueError(f"unknown exemption: {pattern.exempt}")  # fail closed on a new kind

    def _vouched(self, text: str, stems: frozenset[str]) -> bool:
        """A policy duration passes only if a sentence of a public document returned this turn
        states the same duration with one of the same policy stems (M2): FAQ-012's repair
        warranty of two years does not vouch for a returns window of two years."""
        wanted = durations(text)
        have: set[tuple[int, str, str]] = set()
        for source in self.sources:
            if source.public_document:
                have |= policy_durations(source.text)
        return bool(wanted) and all(
            any((value, unit, stem) in have for stem in stems) for value, unit in wanted
        )

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

    def _context_stems(self, sentence: int) -> frozenset[str]:
        """The policy stems of the line that heads the sentence's line (M2): for a list item its
        parent, otherwise the line before it if that is a heading. Measured in view characters
        (pass 3, P3-3): a list item whose parent lies more than 16L back counts as policy with
        unreadable stems (fail closed)."""
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
        if complete and complete[-1][0] and _HEADING.fullmatch(complete[-1][1]):
            return stems_of(complete[-1][1])
        return frozenset()

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
                if pattern.exempt == "duration":
                    stems = self._policy_context(start, end, final)
                    if stems is None:  # hold it until its sentence shows whether it is policy
                        if self._undecided is None or start < self._undecided:
                            self._undecided = start
                        continue
                    if not stems or self._vouched(view.text[found.start() : found.end()], stems):
                        continue
                if self._exempt(pattern, view, found, self._raw[start:end]):
                    continue
                if best is None or start < best.start:
                    best = Match(pattern.cls, start, end)
                break
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
                sentence = self._lead + FIXED_SENTENCES[self._dropping.cls]
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


def check_text(text: str, sources: Sequence[Source] = (), seen: Iterable[str] = ()) -> str:
    """The whole of `text` through the check at once: what the shopper would be left seeing."""
    check = OutputCheck(set(seen), list(sources))
    check.feed(text)
    check.finish()
    return check.shown


def hits(text: str, sources: Sequence[Source] = (), seen: Iterable[str] = ()) -> dict[str, int]:
    """What the check would block in `text`, by class. For tests and evidence: never logged."""
    check = OutputCheck(set(seen), list(sources))
    check.feed(text)
    check.finish()
    return {cls: n for cls, n in check.counts.items() if cls != "marker"}
