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


def _spans(tokens: list[str], seqs: tuple[tuple[str, ...], ...]) -> dict[int, int]:
    """start -> end of the longest match at each position, in one pass (security review M2)."""
    found: dict[int, int] = {}
    for i in range(len(tokens)):
        best = max((len(s) for s in seqs if _at(tokens, i, s)), default=0)
        if best:
            found[i] = i + best
    return found


class _Clause:
    """One clause, read once: every lookup below is then constant time, so a 2,000-character
    message costs linear time on the gateway's event loop, never quadratic (security review M2)."""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        n = len(tokens)
        self.strong = _spans(tokens, _STRONG)
        self.person = _spans(tokens, _STRONG + _WEAK)
        self.talk = _spans(tokens, _TALK_VERBS)
        self.want = _spans(tokens, _WANT_VERBS)
        self.bare = _spans(tokens, _BARE_HANDOVER)
        # The clause is negated, hypothetical, a how-to question or reported speech before
        # position `upto` exactly when `upto >= self.blocked_from`.
        ends = [i + 1 for i, t in enumerate(tokens) if t in _NEGATIONS]
        ends += [i + len(b) for i in range(n) for b in _BLOCKERS if _at(tokens, i, b)]
        self.blocked_from = min(ends, default=n + 1)
        # Everything from position j on is a terse filler.
        self.fillers_after = [True] * (n + 1)
        for j in range(n - 1, -1, -1):
            self.fillers_after[j] = self.fillers_after[j + 1] and tokens[j] in _TERSE_FILLERS

    def blocked(self, upto: int) -> bool:
        return upto >= self.blocked_from

    def product_use(self, start: int, end: int) -> bool:
        tokens = self.tokens
        before = tokens[start - 1] if start > 0 else ""
        after = tokens[end] if end < len(tokens) else ""
        return before in _BEFORE_NOUN or after in _AFTER_NOUN

    def noun_after(self, start: int, nouns: dict[int, int]) -> bool:
        """A person word at `start`, or after at most `MAX_FILLERS` filler words, not a product
        use."""
        i = start
        while i < len(self.tokens) and i - start <= MAX_FILLERS:
            if i in nouns:
                return not self.product_use(i, nouns[i])
            if self.tokens[i] not in _FILLERS:
                return False
            i += 1
        return False

    def terse(self) -> bool:
        if not self.strong or any(self.product_use(s, e) for s, e in self.strong.items()):
            return False
        covered = {i for s, e in self.strong.items() for i in range(s, e)}
        return all(i in covered or t in _TERSE_FILLERS for i, t in enumerate(self.tokens))

    def requests(self) -> bool:
        tokens = self.tokens
        for start, end in self.talk.items():
            noun_use = start > 0 and tokens[start - 1] in _NOUN_DETERMINERS
            if not noun_use and not self.blocked(start) and self.noun_after(end, self.person):
                return True
        for start, end in self.want.items():
            if not self.blocked(start) and self.noun_after(end, self.strong):
                return True
        for start, end in self.person.items():
            takes_over = (
                _at(tokens, end, ("take", "over"))
                or _at(tokens, end, ("join", "this", "chat"))
                or _at(tokens, end, ("join", "the", "chat"))
            )
            if takes_over and not self.blocked(start) and not self.product_use(start, end):
                return True
        return False

    def bare_handover(self) -> bool:
        """ "connect me" (or "transfer me", "put me through") with nothing after it but fillers."""
        return any(
            not self.blocked(start) and self.fillers_after[end] for start, end in self.bare.items()
        )

    def names_person(self) -> bool:
        return any(not self.product_use(s, e) for s, e in self.person.items())


def asks_for_person(message: str) -> bool:
    """True when the message explicitly asks for a person now (see the module docstring)."""
    clauses = [_Clause(tokens) for tokens in _clauses(message)]
    if any(c.terse() or c.requests() for c in clauses):
        return True
    return any(c.bare_handover() for c in clauses) and any(c.names_person() for c in clauses)


__all__ = ["asks_for_person"]
