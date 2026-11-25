"""SA-04 and SA-03 through the real assistant, recorded (`assistant-intents`, rows #30 and #22).

Each message is one turn through `stream_answer` on the production surface (mcp-rag, mcp-catalog,
query understanding), and each message stands alone (SA-05 owns memory). The labels and rules were
frozen first (`test_sa_labels_frozen.py`); the scorers are `northwind_evals.intents`, no judge.

Every message has a frozen vector before anything is spent (decision 0012). The part texts Haiku
rewrites are frozen after W1, from its cassettes, so only a replay insists on them.
`make record SUITE=assistant-intents` runs these; `make e2e` and CI's `e2e` job replay them.

SA-04's criterion is a pass mark; SA-03's is a reported rate per intent, so its test asserts that
every conversation was answered and scored, and writes the rates to the report.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import psycopg
import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.turn import Citations, Reset, TextDelta, TurnError, stream_answer
from northwind_assistant.understand import Understander
from northwind_common.llm import agent_runtime
from northwind_evals.answerers import CatalogLog, eval_session, live_of, serve_catalog
from northwind_evals.cli import _chunk_guard
from northwind_evals.contexts import ContextChunk, internal_sources
from northwind_evals.intents import sa03_problems, sa04_problems
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_rag.testing import KEY, CallerIndex, serve_retriever
from northwind_rag.cli import DEFAULT_DATABASE_URL
from northwind_rag.embedding import CachedEmbedder, FastEmbedEmbedder
from northwind_rag.query import rules_plan
from northwind_rag.retrieval import RetrievedChunk

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "evals/fixtures"
REPORT = REPO / ".llm-proxy-log"
PASS_MARK = 18  # SA-04: at least 90% of 20


@dataclass(frozen=True)
class Turn:
    """One message's answer: the text left on screen, the IDs cited, what `check_stock` returned."""

    shown: str
    cited: tuple[str, ...]
    checked: Mapping[str, int]
    error: str | None = None


def labelled(name: str) -> list[dict[str, Any]]:
    text = (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


@lru_cache(maxsize=1)
def _internal_terms() -> re.Pattern[str]:
    """Internal products' names (brand and model, as `names_catalog_product` keys a public one) and
    every internal source ID, which that detector leaves out on purpose (hard rule 7)."""
    terms = []
    for line in (REPO / "data/fixtures/products.jsonl").read_text("utf-8").splitlines():
        product = json.loads(line)
        if product["visibility"] == "internal":
            brand, name = product["brand"], product["name"]
            terms.append(" ".join([*brand.split(), name[len(brand) :].split()[0]]))
    terms += sorted(internal_sources(REPO / "data/fixtures"))
    words = "|".join(r"\s+".join(map(re.escape, term.split())) for term in terms)
    return re.compile(rf"\b(?:{words})\b", re.IGNORECASE)


def internal_content(text: str) -> list[str]:
    return _internal_terms().findall(re.sub(r"[*_`]", "", text))


class Guarded:
    """The index, refusing a search that returned internal content before the model or a cassette
    sees it: the guard `eval-answers` refinements get (security review M-1)."""

    def __init__(self, index: Any) -> None:
        self._index = index
        self._leaks = _chunk_guard()

    def search(self, query: str, caller: Any) -> list[RetrievedChunk]:
        found: list[RetrievedChunk] = self._index.search(query, caller)
        chunks = [ContextChunk(c.chunk_id, c.source_id, c.source_type, c.text) for c in found]
        if self._leaks(chunks):
            raise RuntimeError("a search returned internal content")
        return found


@contextmanager
def surface(index: Any) -> Iterator[tuple[RagEndpoint, CatalogEndpoint, CatalogLog]]:
    log = CatalogLog()
    guard = _chunk_guard()
    with (
        serve_retriever(Guarded(index), Caller) as (url, _),
        serve_catalog(KEY, log, guard) as catalog_url,
    ):
        yield RagEndpoint(url, KEY), CatalogEndpoint(catalog_url, KEY), log


def ask(
    message: str,
    region: str,
    session: str,
    runtime: Any,
    rag: RagEndpoint,
    catalog: CatalogEndpoint,
    log: CatalogLog,
    understand: Understander | None,
) -> Turn:
    log.begin(session)

    async def collect() -> list[Any]:
        caller = Caller(region=region, session=session)  # type: ignore[arg-type]
        turn = stream_answer(message, caller, rag, runtime, catalog, understand)
        return [event async for event in turn]

    shown: list[str] = []
    cited: tuple[str, ...] = ()
    error = None
    for event in asyncio.run(collect()):
        if isinstance(event, TextDelta):
            shown.append(event.text)
        elif isinstance(event, Reset):
            shown.clear()
        elif isinstance(event, Citations):
            cited = event.source_ids
        elif isinstance(event, TurnError):
            error = str(event.code)
    _, live = live_of(log.taken(), region)
    return Turn("".join(shown), cited, dict(live.stock), error)


def preflight(embedder: Any, connect: Callable[[], Any]) -> None:
    """Before either test's first model call: every message of both files has a frozen vector, and
    the database answers (security review L-7: nothing is spent on a run that cannot finish)."""
    for name, key in (("sa04_prompts", "prompt"), ("sa03_conversations", "query")):
        embedder.embed([item[key] for item in labelled(name)])
    assert embedder.misses == 0, "a message has no frozen vector: run make embed-cache before W1"
    connect()


def run_turns(
    items: Sequence[Mapping[str, Any]],
    key: str,
    runtime_of: Callable[[Mapping[str, Any]], Any],
    rag: Any,
    catalog: Any,
    log: Any,
    after: Callable[[Mapping[str, Any]], None],
) -> dict[str, Turn]:
    """One turn per item, stopping at the first that ends in an error or holds internal content."""
    turns: dict[str, Turn] = {}
    for item in items:
        turn = ask(
            item[key],
            item["region"],
            eval_session(item["id"]),
            runtime_of(item),
            rag,
            catalog,
            log,
            Understander(),
        )
        assert turn.error is None, f"{item['id']}: {turn.error}"
        assert not log.leak, f"{item['id']}: a catalog result held internal content"
        assert internal_content(turn.shown) == [], f"{item['id']}: internal content on screen"
        after(item)
        turns[item["id"]] = turn
    return turns


def run_recorded(
    items: Sequence[Mapping[str, Any]], key: str, llm_proxy: Any, root: Path
) -> dict[str, Turn]:
    def connect() -> psycopg.Connection:
        url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
        return psycopg.connect(url, autocommit=True)

    embedder = CachedEmbedder(FastEmbedEmbedder())
    preflight(embedder, lambda: connect().close())
    hits = embedder.hits

    def frozen(item: Mapping[str, Any]) -> None:
        nonlocal hits
        routed = rules_plan(item[key], item["region"]).needs_model
        if llm_proxy.mode == "replay" or not routed:
            assert embedder.hits > hits, f"{item['id']}: the first search is not frozen"
        hits = embedder.hits

    def runtime_of(item: Mapping[str, Any]) -> Any:
        return agent_runtime(base_url=llm_proxy.base_url, root=root / item["id"])

    with surface(CallerIndex(connect, embedder)) as (rag, catalog, log):
        return run_turns(items, key, runtime_of, rag, catalog, log, frozen)


def write_report(name: str, turns: Mapping[str, Turn], problems: Mapping[str, list[str]]) -> None:
    REPORT.mkdir(exist_ok=True)
    rows = {i: {**asdict(turn), "problems": problems[i]} for i, turn in turns.items()}
    (REPORT / f"{name}.json").write_text(json.dumps(rows, indent=1, sort_keys=True), "utf-8")


@pytest.mark.e2e
@pytest.mark.req("SA-04")
@pytest.mark.llm_suite("assistant-intents")
def test_the_assistant_asks_first_on_at_least_18_of_20(llm_proxy: Any, tmp_path: Path) -> None:
    items = labelled("sa04_prompts")
    turns = run_recorded(items, "prompt", llm_proxy, tmp_path)
    problems = {i["id"]: sa04_problems(i, turns[i["id"]].shown) for i in items}
    write_report("sa04", turns, problems)
    # The user's Q2: an empty answer is a stop, whatever the pass mark says.
    assert [i for i, t in turns.items() if not t.shown.strip()] == []
    failed = {i: p for i, p in problems.items() if p}
    assert len(items) == 20
    assert len(items) - len(failed) >= PASS_MARK, failed


@pytest.mark.e2e
@pytest.mark.req("SA-03")
@pytest.mark.llm_suite("assistant-intents")
def test_every_intent_has_ten_scored_conversations(llm_proxy: Any, tmp_path: Path) -> None:
    items = labelled("sa03_conversations")
    turns = run_recorded(items, "query", llm_proxy, tmp_path)
    store = FixtureCatalog()
    problems: dict[str, list[str]] = {}
    for item in items:
        turn = turns[item["id"]]
        units = store.stock(item["variants"][0], item["region"]) if item["variants"] else None
        problems[item["id"]] = sa03_problems(item, turn.shown, turn.cited, turn.checked, units)
    write_report("sa03", turns, problems)
    assert [i for i, t in turns.items() if not t.shown.strip()] == []
    asked = Counter(i["intent"] for i in items)
    passed = Counter(i["intent"] for i in items if not problems[i["id"]])
    print("SA-03 pass rate:", {intent: f"{passed[intent]}/{n}" for intent, n in asked.items()})
    assert all(n >= 10 for n in asked.values()) and len(asked) == 5


# ---- the wiring, on a scripted model: no database, no spend --------------------------------------


class One:
    def search(self, query: str, caller: Caller) -> list[RetrievedChunk]:
        return [RetrievedChunk("c1", "product", "P0001", "product", "A rain jacket.")]


@pytest.mark.req("SA-04")
def test_a_scripted_clarifying_question_is_scored_as_asked(tmp_path: Path) -> None:
    item = labelled("sa04_prompts")[0]
    with ScriptedLLM() as llm, surface(One()) as (rag, catalog, log):
        llm.reply_stream("What size do you ", "need, and what will you use it for?")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path / "rt")
        turn = ask(item["prompt"], item["region"], "0" * 32, runtime, rag, catalog, log, None)
        assert len(llm.requests) == 1  # no tool call, so first token is the first call's
    assert turn.error is None and turn.cited == ()
    assert turn.shown == "What size do you need, and what will you use it for?"
    assert sa04_problems(item, turn.shown) == []


@pytest.mark.req("SA-03")
def test_a_scripted_stock_answer_is_scored_against_the_live_store(tmp_path: Path) -> None:
    [item] = [i for i in labelled("sa03_conversations") if i["id"] == "IC-035"]
    [variant] = item["variants"]
    units = FixtureCatalog().stock(variant, item["region"])
    assert units is not None and units > 0
    with ScriptedLLM() as llm, surface(One()) as (rag, catalog, log):
        llm.reply_tool_use("mcp__northwind-catalog__check_stock", {"variant_id": variant})
        llm.reply_stream("Yes, it's in ", "stock.")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path / "rt")
        turn = ask(item["query"], item["region"], "0" * 32, runtime, rag, catalog, log, None)
    assert turn.checked == {variant: units}
    assert turn.shown == "Yes, it's in stock."
    assert sa03_problems(item, turn.shown, turn.cited, turn.checked, units) == []


@pytest.mark.req("SA-03")
def test_a_stock_answer_with_no_check_never_reaches_the_shopper(tmp_path: Path) -> None:
    [item] = [i for i in labelled("sa03_conversations") if i["id"] == "IC-035"]
    with ScriptedLLM() as llm, surface(One()) as (rag, catalog, log):
        llm.reply_stream("Yes, it's in ", "stock.")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path / "rt")
        turn = ask(item["query"], item["region"], "0" * 32, runtime, rag, catalog, log, None)
    assert turn.checked == {}
    assert sa03_problems(item, turn.shown, turn.cited, turn.checked, 7) == ["no answer"]
