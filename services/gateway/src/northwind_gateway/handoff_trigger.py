"""Does a chat message ask for a person? (row #23, SA-06: "an explicit request for a person is
honored immediately").

Pure code, before any search or model call: a match hands the chat off at once, so no model ever
decides it, and nothing a model or a retrieved text says can trigger it. It is tuned for precision:
a message it misses goes to the assistant as normal, while a false match hands off a shopper who
did not ask. The labelled set `services/gateway/tests/handoff_phrases.jsonl` was written before this
matcher and pinned by its sha256 (the user's change 2 to the plan).

A message matches when one of its clauses (split on sentence punctuation and "but"):
1. is only a request word for a person ("agent", "human please", "real person!"); or
2. has a request verb ("talk to", "speak with", "connect me", "get me", "I want", ...) followed,
   through at most four filler words, by a word for a person ("a person", "a real human",
   "someone from support", "customer service"); `someone`, `support`, `staff` and `people` count
   after a talk-to verb only ("I need support for my knees" is not a request); or
3. has a word for a person followed by "take over" or "join this chat"; or
4. is only "connect me" (or "transfer me", "put me through"), and another clause names a person
   ("Is there a human I can chat with? If so, please connect me.").
Words are read after a small spelling map ("persn", "wanna", "u"); a talk verb used as a noun
("this chat with a real human?") is not a request.
A clause does not match when, before its verb, it is negated ("don't", "no need"), hypothetical
("if", "would I have to", "do I need to"), a how-to question ("how do I") or reported speech ("my
friend said"); or when the person word is about a product ("two-person tent", "travel agent",
"human-centred").
"""

from __future__ import annotations

import re
import unicodedata

MAX_FILLERS = 4

_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "`": "'", "\u00b4": "'"})
_CLAUSE = re.compile(r"[.;!?,:()\n]+|\bbut\b|\s-\s")
_TOKEN = re.compile(r"[a-z0-9']+")
# Fix round 1 (in-sample, the user's change 2): shorthand and misspellings of the words below.
_SPELLINGS = {
    "u": "you",
    "ur": "your",
    "wanna": "want to",
    "persn": "person",
    "peson": "person",
    "preson": "person",
    "humen": "human",
    "humn": "human",
    "hooman": "human",
    "suport": "support",
    "supprt": "support",
    "conect": "connect",
    "connet": "connect",
    "agnet": "agent",
    "representive": "representative",
    "speek": "speak",
    "tlk": "talk",
}
# A talk verb used as a noun ("this chat with a real human?") is not a request.
_NOUN_DETERMINERS = frozenset({"this", "the", "our", "my", "a"})
# A verb that asks to be handed over on its own, when another clause names a person.
_BARE_HANDOVER: tuple[tuple[str, ...], ...] = (
    ("connect", "me"),
    ("transfer", "me"),
    ("put", "me", "through"),
)

# A word for a person, as token sequences. `WEAK` ones count only after a talk-to verb.
_STRONG: tuple[tuple[str, ...], ...] = (
    ("human", "being"),
    ("human",),
    ("humans",),
    ("person",),
    ("agent",),
    ("live", "agent"),
    ("representative",),
    ("rep",),
    ("operator",),
    ("customer", "service"),
    ("customer", "support"),
    ("customer", "care"),
    ("customer", "services"),
    ("support", "team"),
    ("care", "team"),
    ("staff", "member"),
    ("team", "member"),
    ("manager",),
    ("supervisor",),
)
_WEAK: tuple[tuple[str, ...], ...] = (
    ("someone",),
    ("somebody",),
    ("anyone",),
    ("anybody",),
    ("people",),
    ("support",),
    ("staff",),
    ("employee",),
    ("associate",),
)

_TALK_VERBS: tuple[tuple[str, ...], ...] = (
    ("talk", "to"),
    ("talk", "with"),
    ("speak", "to"),
    ("speak", "with"),
    ("chat", "with"),
    ("chat", "to"),
    ("connect", "me"),
    ("transfer", "me"),
    ("transfer", "to"),
    ("transferred", "to"),
    ("put", "me", "through"),
    ("put", "me", "in", "touch", "with"),
    ("hand", "me", "over"),
    ("pass", "me"),
    ("escalate",),
    ("escalate", "this"),
    ("escalate", "me"),
)
_WANT_VERBS: tuple[tuple[str, ...], ...] = (
    ("i", "want"),
    ("i", "need"),
    ("i'd", "like"),
    ("id", "like"),
    ("i", "would", "like"),
    ("get", "me"),
    ("give", "me"),
    ("bring", "me"),
    ("can", "i", "have"),
    ("can", "i", "get"),
    ("could", "i", "get"),
    ("could", "i", "have"),
    ("may", "i", "have"),
    ("let", "me", "have"),
)
_FILLERS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "with",
        "real",
        "actual",
        "proper",
        "live",
        "human",
        "some",
        "your",
        "one",
        "of",
        "member",
        "from",
        "on",
        "please",
        "pls",
        "plz",
        "now",
        "right",
        "speak",
        "talk",
        "chat",
        "someone",
        "somebody",
        "in",
        "you",
        "guys",
        "s",
        "northwind",
        "actually",
        "just",
        "really",
        "immediately",
        "asap",
        "over",
        "through",
    }
)
# Words that make a person word about a product, when they come right before or after it.
_BEFORE_NOUN = frozenset(
    {
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "single",
        "multi",
        "travel",
        "estate",
        "secret",
        "free",
        "insurance",
        "booking",
        "shipping",
    }
)
_AFTER_NOUN = frozenset(
    {
        "tent",
        "tents",
        "hammock",
        "bag",
        "bags",
        "shelter",
        "centred",
        "centered",
        "made",
        "powered",
        "sized",
        "size",
        "scale",
        "body",
        "who",
        "that",
        "kit",
        "raft",
        "kayak",
        "canoe",
        "sleeping",
    }
)
# Terse requests: a clause that is only these words plus at least one person word.
_TERSE_FILLERS = frozenset(
    {
        "a",
        "an",
        "the",
        "please",
        "pls",
        "plz",
        "now",
        "right",
        "asap",
        "real",
        "live",
        "actual",
        "human",
        "need",
        "want",
        "i",
        "hello",
        "hi",
        "hey",
        "ok",
        "okay",
        "yes",
        "just",
        "only",
        "immediately",
    }
)
_NEGATIONS = frozenset(
    {"not", "don't", "dont", "never", "no", "doesn't", "without", "won't", "wont", "nor"}
)
_BLOCKERS: tuple[tuple[str, ...], ...] = (
    ("if",),
    ("would", "i", "have"),
    ("would", "i", "need"),
    ("do", "i", "have"),
    ("do", "i", "need"),
    ("will", "i", "have"),
    ("will", "i", "need"),
    ("should", "i"),
    ("how", "do", "i"),
    ("how", "can", "i"),
    ("how", "could", "i"),
    ("how", "would", "i"),
    ("how", "to"),
    ("when", "can", "i"),
    ("where", "can", "i"),
    ("said",),
    ("says",),
    ("told", "me"),
    ("my", "friend"),
    ("recommended",),
    ("suggested",),
    ("whether",),
)


def _normal(message: str) -> str:
    text = unicodedata.normalize("NFKC", message).translate(_APOSTROPHES).casefold()
    return text.replace("-", " ")


def _tokens(part: str) -> list[str]:
    return [w for t in _TOKEN.findall(part) for w in _SPELLINGS.get(t, t).split()]


def _clauses(message: str) -> list[list[str]]:
    return [tokens for part in _CLAUSE.split(_normal(message)) if (tokens := _tokens(part))]


def _at(tokens: list[str], i: int, seq: tuple[str, ...]) -> bool:
    return tuple(tokens[i : i + len(seq)]) == seq


def _find(tokens: list[str], seqs: tuple[tuple[str, ...], ...]) -> list[tuple[int, int]]:
    """(start, end) of every match, the longest at each position."""
    found = []
    for i in range(len(tokens)):
        best = max((len(s) for s in seqs if _at(tokens, i, s)), default=0)
        if best:
            found.append((i, i + best))
    return found


def _product_use(tokens: list[str], start: int, end: int) -> bool:
    before = tokens[start - 1] if start > 0 else ""
    after = tokens[end] if end < len(tokens) else ""
    return before in _BEFORE_NOUN or after in _AFTER_NOUN


def _blocked(tokens: list[str], upto: int) -> bool:
    """Negated, hypothetical, a how-to question or reported speech before position `upto`."""
    head = tokens[:upto]
    if any(t in _NEGATIONS for t in head) or _at(head, 0, ("no", "need")):
        return True
    return any(_at(head, i, b) for b in _BLOCKERS for i in range(len(head)))


def _terse(tokens: list[str]) -> bool:
    nouns = _find(tokens, _STRONG)
    if not nouns:
        return False
    covered = {i for start, end in nouns for i in range(start, end)}
    if any(_product_use(tokens, s, e) for s, e in nouns):
        return False
    return all(i in covered or t in _TERSE_FILLERS for i, t in enumerate(tokens))


def _noun_after(tokens: list[str], start: int, nouns: tuple[tuple[str, ...], ...]) -> bool:
    """A person word at `start`, or after at most `MAX_FILLERS` filler words, not a product use."""
    skipped = 0
    i = start
    while i < len(tokens) and skipped <= MAX_FILLERS:
        for s, e in _find(tokens[i:], nouns)[:1]:
            if s == 0:
                return not _product_use(tokens, i, i + e)
        if tokens[i] not in _FILLERS:
            return False
        skipped += 1
        i += 1
    return False


def _noun_use(tokens: list[str], start: int) -> bool:
    return start > 0 and tokens[start - 1] in _NOUN_DETERMINERS


def _bare_handover(tokens: list[str]) -> bool:
    """ "connect me" (or "transfer me", "put me through") with nothing after it but fillers."""
    for start, end in _find(tokens, _BARE_HANDOVER):
        rest = tokens[end:]
        if not _blocked(tokens, start) and all(t in _TERSE_FILLERS for t in rest):
            return True
    return False


def _names_person(tokens: list[str]) -> bool:
    return any(not _product_use(tokens, s, e) for s, e in _find(tokens, _STRONG + _WEAK))


def _requests(tokens: list[str]) -> bool:
    for start, end in _find(tokens, _TALK_VERBS):
        if _noun_use(tokens, start) or _blocked(tokens, start):
            continue
        if _noun_after(tokens, end, _STRONG + _WEAK):
            return True
    for start, end in _find(tokens, _WANT_VERBS):
        if not _blocked(tokens, start) and _noun_after(tokens, end, _STRONG):
            return True
    for start, end in _find(tokens, _STRONG + _WEAK):
        rest = tokens[end:]
        takes_over = _at(rest, 0, ("take", "over")) or _at(rest, 0, ("join", "this", "chat"))
        takes_over = takes_over or _at(rest, 0, ("join", "the", "chat"))
        if takes_over and not _blocked(tokens, start) and not _product_use(tokens, start, end):
            return True
    return False


def asks_for_person(message: str) -> bool:
    """True when the message explicitly asks for a person now (see the module docstring)."""
    clauses = _clauses(message)
    if any(_terse(tokens) or _requests(tokens) for tokens in clauses):
        return True
    return any(_bare_handover(t) for t in clauses) and any(_names_person(t) for t in clauses)


__all__ = ["asks_for_person"]
