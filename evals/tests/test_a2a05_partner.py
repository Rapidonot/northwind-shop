"""A2A-05 prep (row #15, decision 0002): the partner script and its named answer fixtures.

The partner test client's script is frozen in `evals/fixtures/a2a-05-partner/script.json`: seven
product questions, one out-of-scope request and the scoped-order turn. `script.sha256` holds the
digest of its exact bytes, and every fixture records the digest it was recorded against, so any
later change is detected. The assistant's answer to each turn is a **named fixture**,
`answers/<name>.json`, found by name alone (`partner_fixture`), never by request hash, so metadata
the A2A wrapper adds later cannot cause a miss.

`test_partner_turn` is the recorder. It answers each turn through `stream_answer`, never the
unchecked inner turn, so #13's output check (decision 0015) shapes every recorded word. It serves an
in-process mcp-rag over `SavedFirstIndex`. `make record SUITE=a2a-05-partner` records through the
window the user opened; in replay it re-runs every turn on the fixture's saved contexts, with no DB.

The recording is provisional (RECORD-ALL, #18, re-records it). The cassette replay and the payload
check (check 2) therefore run only while a fixture's `assistant_id` is the current one; otherwise
they skip as stale (the user's change 2 to the plan). Every other check always runs, with no proxy,
DB or embedder. A2A-05 must never serve a fixture whose `assistant_id` is not current.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.req("A2A-05")

REPO = Path(__file__).resolve().parents[2]
SUITE = "a2a-05-partner"
PARTNER = REPO / "evals" / "fixtures" / SUITE
SCRIPT = PARTNER / "script.json"
DIGEST = PARTNER / "script.sha256"
ANSWERS = PARTNER / "answers"
CASSETTES = REPO / "cassettes" / SUITE
DATA = REPO / "data" / "fixtures"
THIS = Path(__file__).resolve().relative_to(REPO).as_posix()

# The index the recording must run against (the user's change 1): main's DATA_VERSION. Row #32 moved
# it to 3 (R01222 to R01224; was 2, `389cae01`, #13b's index); `3a89123f` is the v3 index_version's
# prefix. Both are required, and the dataset digest must equal data/fixtures/manifest.json's.
INDEX_DATA_VERSION = 3
INDEX_VERSION_PREFIX = "3a89123f"

STALE = "stale: assistant changed since the provisional recording; RECORD-ALL (#18) re-records"
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
USES = {
    "product_question": "answer",
    "out_of_scope": "never_served",
    "scoped_order": "narrative_only",
}
CITATION = re.compile(r"\[([^\[\]]+)\]")
# Whole words; "AI" is case-sensitive so "said" or "aim" never count.
DISCLOSURE = re.compile(
    r"\bAI\b|(?i:\bartificial intelligence\b|\bvirtual assistant\b|\bnot an? (?:human|person)\b)"
)
# Hard rule 2, crudely, until #12's money and stock classes replace these (security pass 1, L5).
MONEY = re.compile(
    r"[$£€]\s?\d|\b(?:USD|GBP|EUR|CAD)\s?\d|\d\s?(?:USD|GBP|EUR|CAD)\b", re.IGNORECASE
)
SPELLED_MONEY = re.compile(r"\b\d[\d,.]*\s*(?:dollars|pounds|euros|bucks)\b", re.IGNORECASE)
STOCK_COUNT = re.compile(
    r"\b\d+\s+(?:left|remaining|in stock|available|units?)\b|\b(?:in|out of)\s+stock\b",
    re.IGNORECASE,
)
DELIVERY_DATE = re.compile(
    r"\b20\d\d-\d\d-\d\d\b|\b(?:arriv|deliver|ship)\w*\b[^.\n]{0,30}\b(?:by|on)\s+"
    r"(?:mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*",
    re.IGNORECASE,
)
LIVE_VALUES = (MONEY, SPELLED_MONEY, STOCK_COUNT, DELIVERY_DATE)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
# Claims a refund or an order went through. The output check blocks most wordings; this catches
# what it would let pass in these two turns.
DONE_CLAIM = re.compile(
    r"\b(?:refund|order)\w*\b[^.?!\n]{0,40}\b(?:has|have|was|were|is)\s+(?:now\s+|been\s+)*"
    r"(?:issued|processed|placed|confirmed|refunded|submitted|complete[d]?)\b"
    r"|\bI(?:['’]ve|\s+have)\s+(?:now\s+)?(?:placed|processed|issued|submitted|refunded)\b"
    r"|\byour\s+order\s+(?:number|reference)\b",
    re.IGNORECASE,
)


# ---- the script and the loader by name -----------------------------------------------------------


def script_sha256() -> str:
    return hashlib.sha256(SCRIPT.read_bytes()).hexdigest()


def load_script() -> dict[str, Any]:
    script: dict[str, Any] = json.loads(SCRIPT.read_text(encoding="utf-8"))
    return script


SCRIPT_DATA = load_script()
TURNS: list[dict[str, Any]] = SCRIPT_DATA["turns"]
NAMES = [str(turn["name"]) for turn in TURNS]
REGION = str(SCRIPT_DATA["region"])


class UnknownFixture(KeyError):
    """A name that is not a turn of the frozen script: no file is read for it."""


def turn_named(name: str) -> dict[str, Any]:
    for turn in TURNS:
        if turn["name"] == name:
            return turn
    raise UnknownFixture(name)


def partner_fixture(name: str, answers: Path = ANSWERS) -> dict[str, Any]:
    """The named fixture for one scripted turn, by name alone (decision 0002). Only names in the
    frozen script are accepted, so no path is ever built from an unknown name."""
    if not isinstance(name, str) or name not in NAMES:
        raise UnknownFixture(name)
    fixture: dict[str, Any] = json.loads((answers / f"{name}.json").read_text(encoding="utf-8"))
    if fixture.get("name") != name:
        raise ValueError(f"{name}.json holds {fixture.get('name')!r}")
    return fixture


class NotServable(LookupError):
    """A fixture A2A-05 must not serve: unknown, `never_served`, stale, not this script's, or an
    order narrative without an order artifact and the order tools it describes."""


# Row 18a, P3-1: the exact cart tools of northwind-catalog, never "cart" or "order" anywhere in a
# name (CHK-06's read-only `get_order` held "order").
def _cart_tool_names() -> frozenset[str]:
    from northwind_common.catalog_tool import CART_TOOL_NAMES

    return CART_TOOL_NAMES


# Row 18a (the user's answer 5, 2026-11-25): the order narrative goes beside its artifact only when
# it plainly agrees with it; otherwise A2A-05 returns the artifact alone. Fails closed.
# The user's D5 (c), 2026-11-26 (security L6, N8, R6): a positive rule, not a list of denials. The
# narrative is served only when one sentence cites the variant, confirms it in the cart and holds no
# negating word.
NEGATING = re.compile(
    r"\b(?:not|no|never|cannot|unable|fail(?:ed|s)?|nor|without|nothing|none|empty|declined|"
    r"removed|unsuccessful(?:ly)?)\b|n['’]t\b",
    re.IGNORECASE,
)
CART_CONFIRMATION = re.compile(
    r"\bI(?:['’]ve|\s+have)?\s+(?:now\s+)?added\b[^.!?\n]{0,80}\bto\s+your\s+cart\b"
    r"|\b(?:is|was|are|were|has\s+been|have\s+been)\s+(?:now\s+)?in\s+your\s+cart\b"
    r"|\byour\s+cart\s+(?:now\s+)?(?:holds|has|contains)\b",
    re.IGNORECASE,
)
VARIANT_ID = re.compile(r"\bP\d{4}(?:-[A-Z0-9]{1,16}){1,4}\b")
SENTENCE = re.compile(r"[^.!?\n]+")


def confirmed_variants(text: str) -> set[str]:
    """The variants cited in a sentence that confirms them in the cart and negates nothing."""
    return {
        cited
        for sentence in SENTENCE.findall(text)
        if CART_CONFIRMATION.search(sentence) and not NEGATING.search(sentence)
        for cited in CITATION.findall(sentence)
        if VARIANT_ID.fullmatch(cited)
    }


def narrative_agrees(text: str, artifact: Mapping[str, Any], product_id: str) -> bool:
    """Whether the order narrative plainly agrees with the order's artifact: it cites exactly the
    variants the artifact names, which must be the turn's product's, and each of them in a sentence
    that confirms it in the cart and negates nothing (D5 (c)). An artifact that names no variant
    cannot be agreed with."""
    cited = set(CITATION.findall(text))
    named = set(VARIANT_ID.findall(json.dumps(artifact, sort_keys=True)))
    text_variants = {c for c in cited if VARIANT_ID.fullmatch(c)}
    return bool(
        named
        and all(v.startswith(f"{product_id}-") for v in named)
        and text_variants == named
        and confirmed_variants(text) == named
    )


def served_fixture(
    name: str,
    current_id: str,
    *,
    order_artifact: Mapping[str, Any] | None = None,
    answers: Path = ANSWERS,
    cassettes: Path = CASSETTES,
) -> tuple[str | None, Mapping[str, Any] | None]:
    """The answer text A2A-05 may serve for one turn, with the order artifact it goes with,
    failing closed (security passes 1 and 2: L6, N2, N3). The use comes from the frozen script,
    never the fixture file, and the fixture must answer exactly this script's turn. A
    `narrative_only` text is served only beside a non-empty order artifact, and only if every
    call of its turn was offered a cart or order tool (offered, not used: pass 3, P3-1, P3-2),
    so a pre-#16 "I can't place orders" never sits beside a placed order. Since row 18a (P3-1) the
    tools are the exact cart tools, and one of them must have been used in the turn; and the text
    goes beside the artifact only if it plainly agrees with it (`narrative_agrees`), else the
    artifact is returned alone (text None). This, not `partner_fixture`, is what moves into the
    A2A package. Treat any exception as a rejection."""
    if not isinstance(name, str) or not NAME.fullmatch(name) or name not in NAMES:
        raise NotServable(str(name))
    turn = turn_named(name)
    digest = script_sha256()
    if DIGEST.read_text(encoding="utf-8") != f"{digest}  script.json\n":
        raise NotServable(f"{name}: the script does not match its digest")
    try:
        fixture = partner_fixture(name, answers)
        identity = check_identity(fixture, turn, digest)
    except (UnknownFixture, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise NotServable(name) from error
    if identity:
        raise NotServable(f"{name} does not answer this script's turn")
    use = turn["a2a05_use"]
    if use == "never_served":
        raise NotServable(f"{name} is rejected by scope check before any model call")
    if fixture.get("assistant_id") != current_id:
        raise NotServable(f"{name} is stale")
    if use == "answer":
        return str(fixture["answer"]["shown"]), None
    if use == "narrative_only" and order_artifact and offered_order_tools(name, cassettes):
        text = str(fixture["answer"]["shown"])
        agrees = narrative_agrees(text, order_artifact, str(turn["product_id"]))
        return (text if agrees else None), order_artifact
    raise NotServable(f"{name} is served only beside an order artifact, from order tools")


def current_assistant_id() -> str:
    from northwind_common.llm import ASSISTANT_MODEL
    from northwind_evals.answerers import assistant_id

    return assistant_id(ASSISTANT_MODEL)


def skip_if_stale(fixture: Mapping[str, Any], current: str) -> None:
    """Skips the caller unless the fixture was recorded by the current assistant. A missing or
    malformed `assistant_id` fails: it never reads as merely stale."""
    recorded = fixture.get("assistant_id")
    if not isinstance(recorded, str) or not recorded.startswith("assistant:v1:"):
        pytest.fail(f"{fixture.get('name')}: no valid assistant_id", pytrace=False)
    if recorded != current:
        pytest.skip(STALE)


# ---- the checks, as functions, so each one is shown failing on a bad fixture ---------------------


def chunks_in(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = list(fixture.get("context", []))
    for refinement in fixture.get("refinements", []):
        found.extend(refinement.get("chunks", []))
    return found


def check_identity(fixture: Mapping[str, Any], turn: Mapping[str, Any], digest: str) -> list[str]:
    """Check 1: the fixture answers exactly this turn of exactly this script."""
    problems = []
    message = str(turn["message"])
    expected = {
        "name": turn["name"],
        "kind": turn["kind"],
        "a2a05_use": turn["a2a05_use"],
        "message": message,
        "region": REGION,
        "suite": SUITE,
        "provisional": True,
        "script_sha256": digest,
        "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
    }
    for key, value in expected.items():
        if fixture.get(key) != value:
            problems.append(f"{key} is {fixture.get(key)!r}, not {value!r}")
    if not str(fixture.get("assistant_id", "")).startswith("assistant:v1:"):
        problems.append("no valid assistant_id")
    answer = fixture.get("answer")
    if not isinstance(answer, dict) or not str(answer.get("shown", "")).strip():
        problems.append("no shown answer")
    return problems


def check_payload(fixture: Mapping[str, Any]) -> dict[str, int]:
    """Check 2: what the live output check would still block, in `shown` and in `sent`."""
    from northwind_evals.answerers import payload_hits
    from northwind_evals.contexts import ContextChunk

    sources = [
        ContextChunk(c["chunk_id"], c["source_id"], c["source_type"], c["text"])
        for c in chunks_in(fixture)
    ]
    answer = fixture["answer"]
    return payload_hits(answer["shown"], answer["sent"], sources)


def check_citations(fixture: Mapping[str, Any]) -> list[str]:
    """Check 3: every citation is marked in `shown` and is a source of this turn (hard rule 5)."""
    answer = fixture["answer"]
    sources = {c["source_id"] for c in chunks_in(fixture)}
    marked = {p.strip() for group in CITATION.findall(answer["shown"]) for p in group.split(",")}
    problems = [
        f"{c} is not a source of this turn" for c in answer["citations"] if c not in sources
    ]
    problems += [f"{c} is not marked in the answer" for c in answer["citations"] if c not in marked]
    return problems


def check_sources(
    fixture: Mapping[str, Any], internal: frozenset[str], canaries: frozenset[str]
) -> list[str]:
    """Check 4: nothing internal reached the turn or the answer (hard rule 7)."""
    answer = fixture["answer"]
    problems = [
        f"{c['source_id']} is internal" for c in chunks_in(fixture) if c["source_id"] in internal
    ]
    problems += [
        f"{c['chunk_id']} holds a canary"
        for c in chunks_in(fixture)
        if any(mark in c["text"] for mark in canaries)
    ]
    for part in ("shown", "sent"):
        text = answer[part]
        problems += [f"{part} names internal {s}" for s in internal if s in text]
        problems += [f"{part} holds a canary" for mark in canaries if mark in text]
    return problems


def check_scan(path: Path, canaries: frozenset[str]) -> list[str]:
    """Check 5: the cassette scanner finds no key shape, card number or canary (rules 1 and 4)."""
    from northwind_llm_replay.scan import scan_text

    found = scan_text(path.read_text(encoding="utf-8"), str(path), canaries=canaries)
    return [f"{f.kind} at line {f.line}" for f in found]


def check_money(fixture: Mapping[str, Any]) -> list[str]:
    """Check 6: no currency amount, stock count or delivery date in the answer or in any saved chunk
    (hard rule 2), since chunk text goes back into the model's context on replay (pass 1, L5)."""
    answer = fixture["answer"]
    texts = [("shown", answer["shown"]), ("sent", answer["sent"])]
    texts += [(f"chunk {c['chunk_id']}", c["text"]) for c in chunks_in(fixture)]
    return [
        f"{part}: {m.group(0)!r}"
        for part, text in texts
        for pattern in LIVE_VALUES
        for m in pattern.finditer(text)
    ]


def check_done_claims(fixture: Mapping[str, Any]) -> list[str]:
    """Check 7: the out-of-scope and order turns claim no refund or order went through."""
    if fixture.get("kind") not in ("out_of_scope", "scoped_order"):
        return []
    answer = fixture["answer"]
    return [
        f"{part}: {m.group(0)!r}"
        for part in ("shown", "sent")
        for m in DONE_CLAIM.finditer(answer[part])
    ]


def check_disclosure(fixture: Mapping[str, Any], model_text: str) -> list[str]:
    """Check 8 (row #13 residual owned by #15): the output check never removed the model's own AI
    disclosure, and the turn that asks gets one on screen."""
    shown = str(fixture["answer"]["shown"])
    problems = []
    # Per sentence (pass 1, L7): each sentence of the model's own text that discloses must reach the
    # shopper whole, so another "AI" elsewhere on screen does not hide a replaced disclosure.
    for sentence in SENTENCE_BREAK.split(model_text):
        if DISCLOSURE.search(sentence) and sentence.strip() not in shown:
            problems.append(
                f"a disclosing sentence did not reach the shopper: {sentence.strip()!r}"
            )
    if fixture.get("name") == NAMES[0] and not DISCLOSURE.search(shown):
        problems.append("asked whether it is an AI, the answer does not say so")
    recorded = fixture.get("disclosure", {})
    observed = {
        "in_model_text": bool(DISCLOSURE.search(model_text)),
        "in_shown": bool(DISCLOSURE.search(shown)),
    }
    if recorded != observed:
        problems.append(f"disclosure recorded as {recorded}, observed {observed}")
    return problems


def sse_text(body: str) -> str:
    parts = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line[len("data: ") :])
        delta = event.get("delta") or {}
        if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            parts.append(str(delta.get("text", "")))
    return "".join(parts)


def sse_stop_reason(body: str) -> str | None:
    reason = None
    for line in body.splitlines():
        if line.startswith("data: "):
            event = json.loads(line[len("data: ") :])
            if event.get("type") == "message_delta":
                reason = (event.get("delta") or {}).get("stop_reason")
    return reason


def recorder_test_id(name: str) -> str:
    return f"{THIS}::test_partner_turn[{name}]"


def recorded_calls(name: str, cassettes: Path = CASSETTES) -> list[dict[str, Any]]:
    """The turn's cassettes, in call order: the model's own text before any check ran."""
    found = []
    for path in sorted(cassettes.glob("*.json")):
        cassette = json.loads(path.read_text(encoding="utf-8"))
        if cassette.get("test_id") == recorder_test_id(name):
            if cassette.get("kind") != "sse" or not isinstance(cassette.get("body"), str):
                raise ValueError(f"{path.name} is not a streamed response")
            found.append(cassette)
    return sorted(found, key=lambda c: int(c["seq"]))


def model_text(name: str, cassettes: Path = CASSETTES) -> str:
    return "\n".join(sse_text(c["body"]) for c in recorded_calls(name, cassettes))


def sse_tool_names(body: str) -> list[str]:
    """The tools the model called in one streamed response, in order."""
    names = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line[len("data: ") :])
        block = event.get("content_block") or {}
        if event.get("type") == "content_block_start" and block.get("type") == "tool_use":
            names.append(str(block.get("name", "")))
    return names


def offered_order_tools(name: str, cassettes: Path = CASSETTES) -> bool:
    """Whether every call of the turn offered the model northwind-catalog's cart tools, by their
    exact names, and the model called one of them in the turn (row 18a, P3-1: offered and used),
    read from the recorded requests and responses."""
    calls = recorded_calls(name, cassettes)
    carts = _cart_tool_names()
    offered = bool(calls) and all(
        carts <= {str(tool.get("name", "")) for tool in c["match"]["body"].get("tools", [])}
        for c in calls
    )
    return offered and any(n in carts for c in calls for n in sse_tool_names(c["body"]))


def tool_errors(name: str, cassettes: Path = CASSETTES) -> int:
    """Tool results marked `is_error` in the turn's requests: a refused or failed search of any
    kind (pass 2, N5), which the recorder's flags alone do not all see."""
    count = 0
    for cassette in recorded_calls(name, cassettes):
        for message in cassette["match"]["body"].get("messages", []):
            content = message.get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    count += bool(block.get("is_error"))
    return count


def call_record(cassette: Mapping[str, Any]) -> dict[str, Any]:
    """One `upstream_calls` entry. The cassette hash binds the fixture to the cassette by identity
    (pass 2, N1); fixtures recorded before it carry model and stop reason only."""
    return {
        "cassette": cassette["hash"],
        "model": cassette["model"],
        "stop_reason": sse_stop_reason(cassette["body"]),
    }


def check_cassettes(fixture: Mapping[str, Any], cassettes: Path = CASSETTES) -> list[str]:
    """Check 9 (pass 1, L1; pass 2, N1 and N5): the turn's cassettes are exactly the calls the
    fixture lists, in order, by model and stop reason, and by cassette hash wherever the fixture
    holds it (all or none); and no request held a failed tool result."""
    name = str(fixture.get("name"))
    calls = recorded_calls(name, cassettes)
    listed = list(fixture.get("upstream_calls", []))
    hashed = [entry for entry in listed if "cassette" in entry]
    if hashed and len(hashed) != len(listed):
        return ["some upstream_calls carry a cassette hash and some do not"]
    found = [call_record(c) if hashed else call_record(c) | {"cassette": None} for c in calls]
    if not hashed:
        found = [{k: v for k, v in entry.items() if k != "cassette"} for entry in found]
    problems = []
    if not listed or found != listed:
        problems.append(f"cassettes {found} do not match upstream_calls {listed}")
    if tool_errors(name, cassettes):
        problems.append("a request held a failed tool result")
    return problems


def orphan_cassettes(cassettes: Path = CASSETTES) -> list[str]:
    """Cassettes in the suite that no scripted turn's recorder made."""
    wanted = {recorder_test_id(name) for name in NAMES}
    return [
        path.name
        for path in sorted(cassettes.glob("*.json"))
        if json.loads(path.read_text(encoding="utf-8")).get("test_id") not in wanted
    ]


def run_cassette_problems(name: str, state: Any, cassettes: Path = CASSETTES) -> list[str]:
    """After a recorder run: the turn's cassettes are exactly the ones this run's requests hit or
    wrote (by hash), so nothing from an earlier attempt is picked up (pass 1, L1 and L3)."""
    hashes = [str(call.get("hash", "")) for call in state.calls]
    found = [str(c["hash"]) for c in recorded_calls(name, cassettes)]
    if not hashes or any(not h for h in hashes):
        return ["a call has no hash"]
    matched = sorted(f for f in found if any(f.startswith(h) for h in hashes))
    if matched != sorted(found) or len(found) != len(set(hashes)):
        return [f"cassettes for this turn {found} are not exactly this run's calls {hashes}"]
    return []


@dataclass
class Fakes:
    internal: frozenset[str] = field(default_factory=frozenset)
    canaries: frozenset[str] = field(default_factory=frozenset)


def guard_inputs() -> Fakes:
    from northwind_evals.contexts import internal_sources
    from northwind_llm_replay.scan import load_canaries

    return Fakes(internal_sources(DATA), load_canaries(REPO, required=True))


# ---- the recorder (and its replay) ---------------------------------------------------------------


def chunk_guard(inputs: Fakes) -> Callable[[Sequence[Any]], bool]:
    """The eval CLI's leak check, built from its public parts: an internal source, a canary, a card
    number or a key shape in any chunk counts as a leak (hard rules 1, 4, 7)."""
    from northwind_evals.contexts import SavedContext, leaks

    def leaking(chunks: Sequence[Any]) -> bool:
        context = SavedContext("search", "", "", tuple(chunks))
        return bool(leaks({"search": context}, inputs.internal, inputs.canaries))

    return leaking


def read_only_connect() -> Any:
    import psycopg

    from northwind_rag.cli import DEFAULT_DATABASE_URL

    return psycopg.connect(
        os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        autocommit=True,
        options="-c default_transaction_read_only=on",
    )


def index_state() -> tuple[list[str], tuple[str, str] | None]:
    """One read-only manifest read: its problems against change 1, and the identity they were
    checked on, so the identity compared after the turn is the one that passed (pass 2, N4)."""
    from northwind_rag.store import index_manifest

    with read_only_connect() as conn:
        manifest = index_manifest(conn, "shopper")
    if manifest is None:
        return ["no index is published"], None
    return _manifest_problems(manifest), (manifest["index_version"], manifest["dataset_sha256"])


def index_problems() -> list[str]:
    """Read-only: the current index must be main's DATA_VERSION 2 (the user's change 1)."""
    return index_state()[0]


def _manifest_problems(manifest: Mapping[str, Any]) -> list[str]:
    fixtures = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    if (
        manifest["data_version"] != INDEX_DATA_VERSION
        or fixtures["data_version"] != INDEX_DATA_VERSION
    ):
        problems.append(f"data_version is {manifest['data_version']}/{fixtures['data_version']}")
    if not str(manifest["index_version"]).startswith(INDEX_VERSION_PREFIX):
        problems.append(f"index_version is {str(manifest['index_version'])[:8]}…")
    if manifest["dataset_sha256"] != fixtures["dataset_sha256"]:
        problems.append(
            f"dataset_sha256 is {str(manifest['dataset_sha256'])[:8]}…, not the fixtures'"
        )
    return problems


def chunk_dicts(chunks: Sequence[Any]) -> list[dict[str, str]]:
    return [
        {
            "chunk_id": c.chunk_id,
            "source_id": c.source_id,
            "source_type": c.source_type,
            "text": c.text,
        }
        for c in chunks
    ]


class CountingSearch:
    """The live index for refinements, noting any failure, so a refinement that fails for a reason
    other than the leak guard still stops the fixture being written (pass 1, L4)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.failed = False

    def search(self, query: str, caller: Any) -> Sequence[Any]:
        try:
            found: Sequence[Any] = self._inner.search(query, caller)
        except Exception:
            self.failed = True
            raise
        return found


@dataclass
class TurnResult:
    answer: dict[str, Any]
    refinements: list[dict[str, Any]]
    flags: dict[str, bool]
    error: str | None


def run_partner_turn(
    turn: Mapping[str, Any],
    context: Sequence[Any],
    recorded: Mapping[tuple[str, int], Any],
    live: Any,
    leaks: Callable[[Sequence[Any]], bool] | None,
    base_url: str,
    root: Path,
) -> TurnResult:
    """One `stream_answer` turn for one scripted message, through a real in-process mcp-rag and,
    since row 18a (option B of `docs/reviews/2026-11-25-a2a05-surface-memo.md`, decision 0025),
    a real mcp-catalog with the cart tools: an anonymous shopper's surface, with a fresh random
    cart session per turn. No query understanding, and no orders or policy server."""
    from northwind_assistant.caller import Caller
    from northwind_assistant.catalog_client import CatalogEndpoint
    from northwind_assistant.rag_client import RagEndpoint
    from northwind_assistant.turn import (
        INSUFFICIENT_EVIDENCE,
        Citations,
        Reset,
        TextDelta,
        TurnError,
        stream_answer,
    )
    from northwind_common.llm import agent_runtime
    from northwind_evals.answerers import CatalogLog, SavedFirstIndex, serve_catalog
    from northwind_evals.items import EvalItem
    from northwind_mcp_rag.testing import serve_retriever

    item = EvalItem(
        id=str(turn["name"]),
        segment=SUITE,
        query=str(turn["message"]),
        region=REGION,
        expected_behaviour="answer",
        relevant_sources=(),
        reference_facts=(),
        why="row #15 partner turn",
        trap_id=None,
        reviewed_by="model",
    )
    counting = CountingSearch(live) if live is not None else None
    index = SavedFirstIndex(recorded, counting, leaks)
    index.begin(item, context)
    key = secrets.token_bytes(40)  # a fresh key per turn, never the fixed test key
    session = secrets.token_hex(16)  # a fresh cart per turn: no partner cart meets another's
    with (
        serve_retriever(index, Caller, key=key) as (url, _server),
        serve_catalog(key, CatalogLog(), leaks) as catalog_url,
    ):
        runtime = agent_runtime(base_url=base_url, root=root)

        async def collect() -> list[Any]:
            caller = Caller(region=REGION, session=session)  # type: ignore[arg-type]
            message = str(turn["message"])
            catalog = CatalogEndpoint(catalog_url, key)
            return [
                e
                async for e in stream_answer(
                    message, caller, RagEndpoint(url, key), runtime, catalog
                )
            ]

        events = asyncio.run(collect())
    sent: list[str] = []
    shown: list[str] = []
    citations: list[str] = []
    resets = 0
    error = None
    for event in events:
        if isinstance(event, TextDelta):
            sent.append(event.text)
            shown.append(event.text)
        elif isinstance(event, Reset):
            shown = []
            resets += 1
        elif isinstance(event, Citations):
            citations = list(event.source_ids)
        elif isinstance(event, TurnError):
            error = str(event.code)
    text = "".join(shown)
    return TurnResult(
        answer={
            "shown": text,
            "sent": "".join(sent),
            "citations": citations,
            "resets": resets,
            "outcome": "insufficient_evidence" if text == INSUFFICIENT_EVIDENCE else "answered",
        },
        refinements=[
            {"n": r.n, "query": r.query, "chunks": chunk_dicts(r.chunks)} for r in index.new
        ],
        flags={
            "leak": index.leak,
            "mismatch": index.mismatch,
            "miss": index.miss,
            "refinement_failed": counting is not None and counting.failed,
        },
        error=error,
    )


def upstream_problems(state: Any) -> list[str]:
    """Anything but 1 to MAX_TURNS assistant-model `/v1/messages` calls, each recorded or a hit
    by hash, is an unexpected upstream request. This runs after the turn, so an unexpected call has
    already been made (the proxy refuses other paths except `count_tokens`, which this fails on; the
    budget refuses anything over it): the
    turn fails and no fixture is written (pass 1, L3). With `-x` the run stops there."""
    from northwind_assistant.turn import MAX_TURNS
    from northwind_common.llm import ASSISTANT_MODEL

    calls = list(state.calls)
    problems = []
    if not 1 <= len(calls) <= MAX_TURNS:
        problems.append(f"{len(calls)} upstream calls, expected 1 to {MAX_TURNS}")
    for call in calls:
        path = str(call.get("path", "")).split("?")[0]
        if call.get("method") != "POST" or not path.endswith("/v1/messages"):
            problems.append(f"unexpected request {call.get('method')} {path}")
        if call.get("model") != ASSISTANT_MODEL:
            problems.append(f"unexpected model {call.get('model')!r}")
        if call.get("outcome") not in ("recorded", "hit"):
            problems.append(f"unexpected outcome {call.get('outcome')!r}")
    for name in ("refusals", "fallback_attempts", "scan_failures", "misses", "stale"):
        if getattr(state, name):
            problems.append(f"{name}: {len(getattr(state, name))}")
    return problems


def chunks_from(records: Sequence[Mapping[str, Any]]) -> tuple[Any, ...]:
    from northwind_evals.contexts import ContextChunk

    return tuple(
        ContextChunk(r["chunk_id"], r["source_id"], r["source_type"], r["text"]) for r in records
    )


def all_problems(
    fixture: Mapping[str, Any],
    path: Path,
    inputs: Fakes,
    text: str,
    cassettes: Path = CASSETTES,
) -> list[str]:
    """Checks 1 and 3–9: the ones that run whatever the assistant's version."""
    turn = turn_named(str(fixture.get("name")))
    return [
        *check_identity(fixture, turn, script_sha256()),
        *check_citations(fixture),
        *check_sources(fixture, inputs.internal, inputs.canaries),
        *check_scan(path, inputs.canaries),
        *check_money(fixture),
        *check_done_claims(fixture),
        *check_disclosure(fixture, text),
        *check_cassettes(fixture, cassettes),
    ]


@pytest.mark.e2e
@pytest.mark.llm_suite(SUITE)
@pytest.mark.parametrize("name", NAMES)
def test_partner_turn(name: str, llm_proxy: Any, tmp_path: Path) -> None:
    turn = turn_named(name)
    if os.environ.get("LLM_REPLAY_MATCH", "hash") != "hash":  # pass 1, L2
        pytest.fail("record and replay this suite by hash only", pytrace=False)
    if llm_proxy.mode == "replay":
        fixture = partner_fixture(name)
        skip_if_stale(fixture, current_assistant_id())
        recorded = {
            (name, int(r["n"])): _refinement(name, r) for r in fixture.get("refinements", [])
        }
        result = run_partner_turn(
            turn,
            chunks_from(fixture["context"]),
            recorded,
            None,
            None,
            llm_proxy.base_url,
            tmp_path,
        )
        assert result.error is None and not any(result.flags.values()), (result.error, result.flags)
        assert result.answer == fixture["answer"]
        assert upstream_problems(llm_proxy.state) == []
        assert run_cassette_problems(name, llm_proxy.state) == []
        return

    from northwind_assistant.caller import Caller
    from northwind_mcp_rag.testing import CallerIndex
    from northwind_rag.embedding import CachedEmbedder, FastEmbedEmbedder

    problems, before = index_state()
    if problems:
        pytest.fail(f"refusing to record against this index: {problems}", pytrace=False)
    inputs = guard_inputs()
    guard = chunk_guard(inputs)
    live = CallerIndex(read_only_connect, CachedEmbedder(FastEmbedEmbedder()))
    context = chunks_from(chunk_dicts(live.search(str(turn["message"]), Caller(region=REGION))))  # type: ignore[arg-type]
    if guard(context):
        pytest.fail("the first search held internal content: nothing recorded", pytrace=False)
    result = run_partner_turn(turn, context, {}, live, guard, llm_proxy.base_url, tmp_path)
    upstream = upstream_problems(llm_proxy.state) + run_cassette_problems(name, llm_proxy.state)
    after_problems, after = index_state()  # pass 1, L8; pass 2, N4
    if after_problems or after != before:
        upstream.append("the index changed during the turn")
    if tool_errors(name):  # pass 2, N5: a refused or failed search of any kind
        upstream.append("a tool result failed")
    if upstream or result.error or any(result.flags.values()):
        pytest.fail(f"not recorded: {upstream} {result.error} {result.flags}", pytrace=False)
    calls = recorded_calls(name)
    text = "\n".join(sse_text(c["body"]) for c in calls)
    message = str(turn["message"])
    fixture = {
        "name": name,
        "kind": turn["kind"],
        "a2a05_use": turn["a2a05_use"],
        "message": message,
        "region": REGION,
        "script_sha256": script_sha256(),
        "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
        "assistant_id": current_assistant_id(),
        "suite": SUITE,
        "provisional": True,
        "recorded": datetime.now(UTC).date().isoformat(),
        "context": chunk_dicts(context),
        "refinements": result.refinements,
        "answer": result.answer,
        "upstream_calls": [call_record(c) for c in calls],
        "disclosure": {
            "in_model_text": bool(DISCLOSURE.search(text)),
            "in_shown": bool(DISCLOSURE.search(result.answer["shown"])),
        },
        "note": NOTES[str(turn["a2a05_use"])],
    }
    staged = tmp_path / f"{name}.json"
    staged.write_text(dump(fixture), encoding="utf-8")
    problems = all_problems(fixture, staged, inputs, text)
    blocked = check_payload(fixture)
    if problems or blocked:
        pytest.fail(f"not written: {problems} {blocked}", pytrace=False)
    ANSWERS.mkdir(parents=True, exist_ok=True)
    (ANSWERS / f"{name}.json").write_text(dump(fixture), encoding="utf-8")


NOTES = {
    "answer": "A2A-05 replays this text by name; never when assistant_id is not current.",
    "never_served": (
        "A2A-05 rejects this turn by scope check before any model call and must never load "
        "this fixture; it is kept as evidence that the assistant declines on its own."
    ),
    "narrative_only": (
        "The order's outcome comes only from the cart/order tools and port fakes. Recorded before "
        "#16, this text says the assistant cannot place orders: if #16 is dropped, A2A-05 returns "
        "the order artifact without it."
    ),
}


def dump(fixture: Mapping[str, Any]) -> str:
    return json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _refinement(name: str, record: Mapping[str, Any]) -> Any:
    from northwind_evals.answerers import Refinement

    return Refinement(name, int(record["n"]), str(record["query"]), chunks_from(record["chunks"]))


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("LLM_MODE") != "record",
    reason="the pre-window check: run with LLM_MODE=record before recording this suite",
)
def test_the_live_index_is_mains_data_version() -> None:
    """Read-only, the pre-window check: the index a recording would search is main's. It runs only
    with LLM_MODE=record (the user's M3 answer), since window A's data and index changes would
    otherwise fail it; RECORD-ALL (#18) updates INDEX_DATA_VERSION and INDEX_VERSION_PREFIX when it
    re-records this suite. The recorder runs the same check before and after every turn."""
    assert index_problems() == []


def test_the_live_index_check_runs_only_when_recording() -> None:
    """M3: the pin skips outside record mode and nowhere else."""
    marks = {m.name: m for m in test_the_live_index_is_mains_data_version.pytestmark}  # type: ignore[attr-defined]
    skip = marks["skipif"]
    assert "integration" in marks
    assert skip.args == (os.environ.get("LLM_MODE") != "record",)


# ---- the no-LLM checks: no proxy, no DB, no embedder ---------------------------------------------


def test_the_script_has_the_turns_decision_0002_names() -> None:
    kinds = [turn["kind"] for turn in TURNS]
    assert 5 <= kinds.count("product_question") <= 10
    assert kinds.count("out_of_scope") == 1 and kinds.count("scoped_order") == 1
    assert len(kinds) == kinds.count("product_question") + 2
    assert len(set(NAMES)) == len(NAMES) and all(NAME.fullmatch(n) for n in NAMES)
    assert all(USES[turn["kind"]] == turn["a2a05_use"] for turn in TURNS)
    assert SCRIPT_DATA["frozen"] is True and SCRIPT_DATA["decision"] == "0002"
    assert REGION == "US" and SCRIPT_DATA["audience"] == "shopper"
    assert NAMES[0].startswith("pq-") and DISCLOSURE.search(TURNS[0]["message"])


def test_the_script_matches_its_digest() -> None:
    assert DIGEST.read_text(encoding="utf-8") == f"{script_sha256()}  script.json\n"


def test_no_message_holds_a_price_card_or_secret() -> None:
    from northwind_llm_replay.scan import load_canaries, scan_text

    for turn in TURNS:
        message = str(turn["message"])
        assert not MONEY.search(message) and not re.search(r"\d{9,}", message), turn["name"]
        assert scan_text(message, canaries=load_canaries(REPO, required=True)) == []


def test_every_product_question_names_a_public_active_us_product() -> None:
    products = {
        p["product_id"]: p
        for p in map(json.loads, (DATA / "products.jsonl").read_text(encoding="utf-8").splitlines())
    }
    for turn in TURNS:
        if turn["kind"] == "out_of_scope":
            assert turn["product_id"] is None
            continue
        product = products[turn["product_id"]]
        assert product["visibility"] == "public" and product["status"] == "active"
        assert REGION in product["regions"] and product["name"] in turn["message"], turn["name"]


def test_exactly_one_fixture_per_turn() -> None:
    present = sorted(p.name for p in ANSWERS.glob("*")) if ANSWERS.exists() else []
    assert present == sorted(f"{name}.json" for name in NAMES)


def test_every_fixture_was_recorded_against_this_script() -> None:
    digest = script_sha256()
    for name in NAMES:
        fixture = partner_fixture(name)
        assert fixture["script_sha256"] == digest, name
        assert fixture["message"] == turn_named(name)["message"], name


@pytest.mark.parametrize("name", NAMES)
def test_every_named_fixture_replays_clean(name: str) -> None:
    """Checks 1 and 3–8, on the fixture as A2A-05 will load it: by name, with no proxy."""
    fixture = partner_fixture(name)
    path = ANSWERS / f"{name}.json"
    assert all_problems(fixture, path, guard_inputs(), model_text(name)) == []


@pytest.mark.parametrize("name", NAMES)
def test_no_fixture_holds_what_the_output_check_blocks(name: str) -> None:
    """Check 2, only while the fixture's assistant is the current one (change 2)."""
    fixture = partner_fixture(name)
    skip_if_stale(fixture, current_assistant_id())
    assert check_payload(fixture) == {}


def test_a_fixture_is_found_by_name_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = NAMES[3]
    (tmp_path / f"{name}.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)  # nothing in the lookup reaches a proxy
    assert partner_fixture(name, answers=tmp_path) == {"name": name}


@pytest.mark.parametrize(
    "name",
    ["", "../script", "answers/../script", "PQ-01-BREAKWATER-WATERPROOFING", "pq-01", "x.json"],
)
def test_an_unknown_or_path_like_name_is_refused(name: str, tmp_path: Path) -> None:
    (tmp_path / "script.json").write_text("{}", encoding="utf-8")
    with pytest.raises(UnknownFixture):
        partner_fixture(name, answers=tmp_path)


def test_a_fixture_holding_another_turn_is_refused(tmp_path: Path) -> None:
    (tmp_path / f"{NAMES[0]}.json").write_text(json.dumps({"name": NAMES[1]}), encoding="utf-8")
    with pytest.raises(ValueError):
        partner_fixture(NAMES[0], answers=tmp_path)


def test_the_stale_skip_happens_only_on_a_mismatched_assistant_id() -> None:
    current = "assistant:v1:m:prompt-aaaaaaaa:check-bbbbbbbb"
    with pytest.raises(pytest.skip.Exception, match=re.escape(STALE)):
        skip_if_stale({"name": "x", "assistant_id": current.replace("bbbb", "cccc")}, current)
    skip_if_stale({"name": "x", "assistant_id": current}, current)  # a match never skips
    for bad in (None, "", 7, "prompt-aaaaaaaa"):
        with pytest.raises(pytest.fail.Exception):
            skip_if_stale({"name": "x", "assistant_id": bad}, current)


def test_only_the_replay_and_check_2_are_gated_on_the_assistant() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    gated = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "skip_if_stale"
            for call in ast.walk(node)
        )
    }
    gated.discard("test_the_stale_skip_happens_only_on_a_mismatched_assistant_id")  # its own test
    assert gated == {"test_partner_turn", "test_no_fixture_holds_what_the_output_check_blocks"}


def test_the_recorder_never_imports_run_turn() -> None:
    """C5: only `stream_answer` answers, so the output check shapes every recorded word."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert all(alias.name != "run_turn" for alias in node.names)
        assert not (isinstance(node, ast.Name) and node.id == "run_turn")
        assert not (isinstance(node, ast.Attribute) and node.attr == "run_turn")
    assert "stream_answer(message" in Path(__file__).read_text(encoding="utf-8")


# ---- each check catches its bad fixture (hand-made in tmp_path; no committed fixture edited) -----


def good_fixture() -> dict[str, Any]:
    turn = TURNS[0]
    message = str(turn["message"])
    shown = "I'm an AI assistant. The shell is rated to 15,000 mm and its seams are taped [P0003]."
    return {
        "name": turn["name"],
        "kind": turn["kind"],
        "a2a05_use": turn["a2a05_use"],
        "message": message,
        "region": REGION,
        "suite": SUITE,
        "provisional": True,
        "script_sha256": script_sha256(),
        "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
        "assistant_id": "assistant:v1:m:prompt-aaaaaaaa:check-bbbbbbbb",
        "context": [
            {
                "chunk_id": "P0003#0",
                "source_id": "P0003",
                "source_type": "product",
                "text": "The Corvane Breakwater Shell is waterproof to 15,000 mm with taped seams.",
            }
        ],
        "refinements": [],
        "answer": {"shown": shown, "sent": shown, "citations": ["P0003"], "resets": 0},
        "disclosure": {"in_model_text": True, "in_shown": True},
    }


GOOD_MODEL_TEXT = (
    "I'm an AI assistant. The shell is rated to 15,000 mm and its seams are taped [P0003]."
)


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def apply(fixture: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        target: Any = fixture
        for part in parents:
            target = target[int(part)] if part.isdigit() else target[part]
        target[int(last) if last.isdigit() else last] = value

    return apply


def _both(text: str) -> Callable[[dict[str, Any]], None]:
    def apply(fixture: dict[str, Any]) -> None:
        fixture["answer"]["shown"] = fixture["answer"]["sent"] = text

    return apply


def _as_turn(index: int) -> Callable[[dict[str, Any]], None]:
    def apply(fixture: dict[str, Any]) -> None:
        turn = TURNS[index]
        message = str(turn["message"])
        fixture.update(
            name=turn["name"],
            kind=turn["kind"],
            a2a05_use=turn["a2a05_use"],
            message=message,
            message_sha256=hashlib.sha256(message.encode("utf-8")).hexdigest(),
        )

    return apply


MUTANTS: dict[str, tuple[list[Callable[[dict[str, Any]], None]], str]] = {
    "1-other-script": ([_set("script_sha256", "0" * 64)], "check_identity"),
    "1-other-message": ([_set("message", "What is the price?")], "check_identity"),
    "1-no-assistant-id": ([_set("assistant_id", "")], "check_identity"),
    "2-promotion": ([_both("Use code SAVE20 for 20% off today [P0003].")], "check_payload"),
    "3-unsourced-citation": ([_set("answer.citations", ["P0999"])], "check_citations"),
    "3-unmarked-citation": (
        [
            _set(
                "context",
                [
                    *good_fixture()["context"],
                    {
                        "chunk_id": "P0021#0",
                        "source_id": "P0021",
                        "source_type": "product",
                        "text": "Synthetic fill.",
                    },
                ],
            ),
            _set("answer.citations", ["P0003", "P0021"]),
        ],
        "check_citations",
    ),
    "4-internal-source": ([_set("context.0.source_id", "INT-001")], "check_sources"),
    "4-internal-id-in-answer": ([_both("I'm an AI. See INT-001 [P0003].")], "check_sources"),
    "5-card-number": ([_both("I'm an AI. Card 4242 4242 4242 4242 [P0003].")], "check_scan"),
    "6-money": ([_both("I'm an AI. It costs $129 [P0003].")], "check_money"),
    "7-refund-done": ([_as_turn(7), _both("Your refund has been processed.")], "check_done_claims"),
    "7-order-placed": (
        [_as_turn(8), _both("I've placed your order for the shell.")],
        "check_done_claims",
    ),
    "8-disclosure-removed": (
        [_both("The shell is rated to 15,000 mm and its seams are taped [P0003].")],
        "check_disclosure",
    ),
    # Security pass 1: L5 (chunk text, spelled money, stock counts, delivery dates) and L7 (a
    # replaced disclosing sentence hidden by another "AI" on screen).
    "6-money-in-chunk": ([_set("context.0.text", "The shell costs $129 today.")], "check_money"),
    "6-spelled-money": ([_both("I'm an AI. It is 129 dollars [P0003].")], "check_money"),
    "6-stock-count": ([_both("I'm an AI. Only 3 left [P0003].")], "check_money"),
    "6-delivery-date": ([_both("I'm an AI. It arrives by Friday [P0003].")], "check_money"),
    "8-disclosing-sentence-replaced": (
        [_both("As an AI tool: the shell is rated to 15,000 mm, seams taped [P0003].")],
        "check_disclosure",
    ),
}


def run_check(check: str, fixture: dict[str, Any], tmp_path: Path) -> Any:
    inputs = Fakes(frozenset({"INT-001"}), frozenset({"NWCANARY-TEST"}))
    if check == "check_identity":
        return check_identity(fixture, turn_named(fixture["name"]), script_sha256())
    if check == "check_payload":
        return check_payload(fixture)
    if check == "check_citations":
        return check_citations(fixture)
    if check == "check_sources":
        return check_sources(fixture, inputs.internal, inputs.canaries)
    if check == "check_scan":
        path = tmp_path / "f.json"
        path.write_text(dump(fixture), encoding="utf-8")
        return check_scan(path, inputs.canaries)
    if check == "check_money":
        return check_money(fixture)
    if check == "check_done_claims":
        return check_done_claims(fixture)
    return check_disclosure(fixture, GOOD_MODEL_TEXT)


@pytest.mark.parametrize("check", sorted({c for _, c in MUTANTS.values()}))
def test_the_good_fixture_passes_every_check(check: str, tmp_path: Path) -> None:
    assert not run_check(check, good_fixture(), tmp_path)


@pytest.mark.parametrize("mutant", sorted(MUTANTS))
def test_each_check_catches_its_bad_fixture(mutant: str, tmp_path: Path) -> None:
    mutations, check = MUTANTS[mutant]
    fixture = good_fixture()
    for mutate in mutations:
        mutate(fixture)
    assert run_check(check, fixture, tmp_path)


def test_disclosure_lost_to_the_check_is_caught_on_any_turn() -> None:
    fixture = good_fixture()
    _as_turn(2)(fixture)
    _both("It is made of recycled polyester [P0053].")(fixture)
    fixture["disclosure"] = {"in_model_text": True, "in_shown": False}
    assert check_disclosure(fixture, "As an AI, I can say it is recycled polyester.")
    fixture["disclosure"] = {"in_model_text": False, "in_shown": False}
    assert check_disclosure(fixture, "It is recycled polyester.") == []


def test_the_upstream_check_refuses_anything_unexpected() -> None:
    from northwind_assistant.turn import MAX_TURNS
    from northwind_common.llm import ASSISTANT_MODEL

    @dataclass
    class State:
        calls: list[dict[str, Any]]
        refusals: list[Any] = field(default_factory=list)
        fallback_attempts: list[Any] = field(default_factory=list)
        scan_failures: list[Any] = field(default_factory=list)
        misses: list[Any] = field(default_factory=list)
        stale: list[Any] = field(default_factory=list)

    ok = {
        "method": "POST",
        "path": "/v1/messages?beta=true",
        "model": ASSISTANT_MODEL,
        "outcome": "recorded",
    }
    assert upstream_problems(State([ok])) == []
    assert upstream_problems(State([]))
    assert upstream_problems(State([ok] * (MAX_TURNS + 1)))
    assert upstream_problems(State([{**ok, "model": "claude-haiku-5-5"}]))
    assert upstream_problems(State([{**ok, "path": "/v1/messages/count_tokens"}]))
    assert upstream_problems(State([{**ok, "outcome": "refused-budget"}]))
    assert upstream_problems(State([ok], refusals=[{}]))
    assert upstream_problems(State([ok], fallback_attempts=[{}]))
    assert upstream_problems(State([ok], stale=["x"]))  # pass 1, L2: served by sequence


def _cassette(
    directory: Path,
    name: str,
    digest: str,
    model: str = "m",
    stop: str = "end_turn",
    tools: Sequence[str] = ("mcp__northwind-rag__retrieve_context",),
    tool_error: bool = False,
) -> None:
    body = (
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi."}}\n'
        f'data: {{"type":"message_delta","delta":{{"stop_reason":"{stop}"}}}}\n'
    )
    result = {"type": "tool_result", "tool_use_id": "t", "is_error": tool_error, "content": "x"}
    request = {
        "tools": [{"name": tool} for tool in tools],
        "messages": [{"role": "user", "content": [result]}],
    }
    record = {"hash": digest, "test_id": recorder_test_id(name), "kind": "sse", "body": body}
    (directory / f"{digest}.json").write_text(
        json.dumps({**record, "model": model, "seq": 0, "match": {"body": request}})
    )


def test_the_cassette_check_binds_a_fixture_to_exactly_its_cassettes(tmp_path: Path) -> None:
    """Check 9 (pass 1, L1; pass 2, N1): a leftover cassette, a missing one or a changed call is
    caught; with hashes listed, a swapped cassette is caught even when model and stop agree."""
    fixture = {"name": NAMES[1], "upstream_calls": [{"model": "m", "stop_reason": "end_turn"}]}
    assert check_cassettes(fixture, tmp_path)  # no cassette at all
    _cassette(tmp_path, NAMES[1], "a" * 64)
    assert check_cassettes(fixture, tmp_path) == []
    assert check_cassettes({**fixture, "upstream_calls": []}, tmp_path)
    assert check_cassettes(
        {**fixture, "upstream_calls": [{"model": "x", "stop_reason": "end_turn"}]}, tmp_path
    )
    hashed = {
        **fixture,
        "upstream_calls": [{"cassette": "a" * 64, "model": "m", "stop_reason": "end_turn"}],
    }
    assert check_cassettes(hashed, tmp_path) == []
    swapped = {
        **fixture,
        "upstream_calls": [{"cassette": "f" * 64, "model": "m", "stop_reason": "end_turn"}],
    }
    assert check_cassettes(swapped, tmp_path)  # same model and stop reason, another cassette
    mixed = {
        **fixture,
        "upstream_calls": [*hashed["upstream_calls"], {"model": "m", "stop_reason": "end_turn"}],
    }
    assert check_cassettes(mixed, tmp_path)
    _cassette(tmp_path, NAMES[1], "b" * 64)  # a leftover from an earlier attempt
    assert check_cassettes(fixture, tmp_path)


def test_a_failed_tool_result_in_any_request_is_caught(tmp_path: Path) -> None:
    """Pass 2, N5: a refused search (SOURCE_REFUSED, SEARCH_LIMIT, ...) fails check 9."""
    fixture = {"name": NAMES[4], "upstream_calls": [{"model": "m", "stop_reason": "end_turn"}]}
    _cassette(tmp_path, NAMES[4], "a" * 64, tool_error=True)
    assert tool_errors(NAMES[4], tmp_path) == 1
    assert check_cassettes(fixture, tmp_path)
    assert all(tool_errors(name) == 0 for name in NAMES)


def test_a_run_must_have_made_exactly_the_turns_cassettes(tmp_path: Path) -> None:
    @dataclass
    class State:
        calls: list[dict[str, Any]]

    _cassette(tmp_path, NAMES[2], "c" * 64)
    assert run_cassette_problems(NAMES[2], State([{"hash": "c" * 16}]), tmp_path) == []
    assert run_cassette_problems(NAMES[2], State([{"hash": "d" * 16}]), tmp_path)
    assert run_cassette_problems(NAMES[2], State([]), tmp_path)
    _cassette(tmp_path, NAMES[2], "e" * 64)  # one this run did not make
    assert run_cassette_problems(NAMES[2], State([{"hash": "c" * 16}]), tmp_path)


def mixed_hash_binding(fixtures: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Names on the weaker binding while another fixture carries cassette hashes (pass 3, P3-3)."""
    hashed = {
        name: any("cassette" in call for call in fixture.get("upstream_calls", []))
        for name, fixture in fixtures.items()
    }
    return sorted(name for name, has in hashed.items() if not has) if any(hashed.values()) else []


def test_the_fixtures_carry_cassette_hashes_all_or_none() -> None:
    """Pass 3, P3-3: once any fixture is bound to its cassettes by hash, every one must be, so a
    turn whose re-record failed cannot keep its old fixture on the weaker binding."""
    assert mixed_hash_binding({name: partner_fixture(name) for name in NAMES}) == []
    old = {"upstream_calls": [{"model": "m", "stop_reason": "end_turn"}]}
    new = {"upstream_calls": [{"cassette": "a" * 64, "model": "m", "stop_reason": "end_turn"}]}
    assert mixed_hash_binding({"pq-01": new, "pq-02": old}) == ["pq-02"]
    assert mixed_hash_binding({"pq-01": new, "pq-02": new}) == []
    assert mixed_hash_binding({"pq-01": old, "pq-02": old}) == []


def test_no_cassette_in_the_suite_belongs_to_no_turn(tmp_path: Path) -> None:
    assert orphan_cassettes() == []
    (tmp_path / "x.json").write_text(json.dumps({"test_id": "elsewhere"}))
    assert orphan_cassettes(tmp_path) == ["x.json"]


def test_a_failing_refinement_is_flagged() -> None:
    """Pass 1, L4: a refinement that raises for any reason stops the fixture being written."""

    class Broken:
        def search(self, query: str, caller: Any) -> Sequence[Any]:
            raise RuntimeError("db down")

    counting = CountingSearch(Broken())
    with pytest.raises(RuntimeError):
        counting.search("q", None)
    assert counting.failed


CURRENT = "assistant:v1:m:prompt-aaaaaaaa:check-bbbbbbbb"


def _served_setup(tmp_path: Path, index: int, **changes: Any) -> tuple[str, Path, Path]:
    """A fixture for turn `index` under tmp_path, current against CURRENT (pass 2, N6), with one
    cassette; `tools` picks what that cassette's request offered."""
    fixture = good_fixture()
    _as_turn(index)(fixture)
    fixture["assistant_id"] = CURRENT
    tools = changes.pop("tools", ("mcp__northwind-rag__retrieve_context",))
    fixture.update(changes)
    answers, cassettes = tmp_path / "answers", tmp_path / "cassettes"
    answers.mkdir()
    cassettes.mkdir()
    name = str(fixture["name"])
    (answers / f"{name}.json").write_text(dump(fixture), encoding="utf-8")
    _cassette(cassettes, name, "a" * 64, tools=tools)
    return name, answers, cassettes


def test_a_current_answer_turn_is_served(tmp_path: Path) -> None:
    name, answers, cassettes = _served_setup(tmp_path, 0)
    text, artifact = served_fixture(name, CURRENT, answers=answers, cassettes=cassettes)
    assert text == good_fixture()["answer"]["shown"] and artifact is None
    with pytest.raises(NotServable):  # stale
        served_fixture(name, CURRENT.replace("bbbb", "cccc"), answers=answers, cassettes=cassettes)


def test_the_out_of_scope_turn_is_never_served(tmp_path: Path) -> None:
    name, answers, cassettes = _served_setup(tmp_path, 7)
    for artifact in (None, {"order": "x"}):
        with pytest.raises(NotServable):
            served_fixture(
                name, CURRENT, order_artifact=artifact, answers=answers, cassettes=cassettes
            )


def test_a_fixture_relabelled_as_an_answer_is_still_never_served(tmp_path: Path) -> None:
    """Pass 2, N2: the use comes from the frozen script, and the fixture must match its turn."""
    name, answers, cassettes = _served_setup(tmp_path, 7, a2a05_use="answer")
    with pytest.raises(NotServable):
        served_fixture(name, CURRENT, answers=answers, cassettes=cassettes)


def test_the_order_narrative_needs_an_artifact_and_order_tools(tmp_path: Path) -> None:
    """Pass 2, N3: before #16 no turn offers order tools, so the narrative is never served."""
    name, answers, cassettes = _served_setup(tmp_path, 8)
    for artifact in (None, {}, {"order": "x"}):
        with pytest.raises(NotServable):
            served_fixture(
                name, CURRENT, order_artifact=artifact, answers=answers, cassettes=cassettes
            )


def test_a_tool_named_like_a_cart_tool_offered_but_not_used_is_no_order_turn(
    tmp_path: Path,
) -> None:
    """Row 18a, P3-1: this shape (a cart-like name on another server, offered, never called) was
    served here before. The served case, with northwind-catalog's exact cart tools offered and one
    called, and the text beside an artifact it agrees with, is in `test_a2a05_partner_18a.py`."""
    name, answers, cassettes = _served_setup(
        tmp_path, 8, tools=("mcp__northwind-cart__add_to_cart",)
    )
    for artifact in (None, {"order": "x"}):
        with pytest.raises(NotServable):
            served_fixture(
                name, CURRENT, order_artifact=artifact, answers=answers, cassettes=cassettes
            )


def test_unknown_names_and_todays_fixtures_fail_closed() -> None:
    for bad in ("", "../script", "PQ-01", "pq-01-breakwater-waterproofing.json", "nope"):
        with pytest.raises(NotServable):
            served_fixture(bad, current_assistant_id())
    current = current_assistant_id()
    with pytest.raises(NotServable):  # pre-#16: no order tools were offered
        served_fixture("order-01-breakwater", current, order_artifact={"order": "x"})
    with pytest.raises(NotServable):
        served_fixture("oos-01-refund", current)
