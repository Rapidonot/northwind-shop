"""What produces an answer for the judge to score (RAG-12; row #13b).

`Answerer` is the seam: the harness only needs `id` and `answer()`. `AssistantAnswerer` is the
Shopper Assistant itself: `stream_answer`, never `run_turn`, so the assistant's own prompt and the
output check (decision 0015) are what gets scored. Its first search returns the item's saved
context (decision 0007); the model's later `retrieve_context` calls are refinement searches,
recorded in `evals/contexts/refinements.ndjson` and served from there in replay (option B).

The judge renders sources with `render_sources` here. Nothing in this module may be imported by
`gate`, `items` or `metrics`: promotion loads those on the runner, where the assistant is not
installed (a test holds that).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import importlib.util
import json
import secrets
import shutil
import tempfile
import threading
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from northwind_evals.contexts import ContextChunk
from northwind_evals.items import EvalItem

REFINEMENT_MISS = "refinement miss"
REPLAY_MISS = "replay miss"


class AnswerError(RuntimeError):
    """The answerer gave no usable answer. `code` names why; the item fails, never passes."""

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class StaleAnswer(RuntimeError):
    """The recording or a recorded refinement no longer matches: a reported state, not a pass."""


@dataclass(frozen=True)
class Answer:
    """`shown` is what the shopper is left seeing (scored); `sent` every token streamed, text
    before a reset included (the every-token rule); `sources` every chunk the turn returned."""

    shown: str
    sent: str = ""
    sources: tuple[ContextChunk, ...] = ()
    payload_hits: Mapping[str, int] = field(default_factory=dict)
    # Row #12 (RAG-09): every catalog tool result the turn returned, as `type="catalog"` blocks for
    # the judge only (the user's D6), and how many sentences of `shown` hold a claim backed only by
    # a live price or stock value, which no retrieved source can be cited for (change 3).
    live: tuple[ContextChunk, ...] = ()
    live_claims: int = 0

    @property
    def text(self) -> str:
        return self.shown


class Answerer(Protocol):
    @property
    def id(self) -> str:
        """Names the answerer, its prompt version and its model."""

    def answer(self, item: EvalItem, context: Sequence[ContextChunk]) -> Answer: ...


def escaped(text: str) -> str:
    """Untrusted text cannot open or close a prompt tag (hard rule 6)."""
    return html.escape(text, quote=False)


def as_data(text: str) -> str:
    """NFKC first, then escaping, as mcp-rag renders (RAG-11; row #13): fullwidth lookalikes
    such as `＜/source＞` fold to ASCII and are escaped too."""
    return escaped(unicodedata.normalize("NFKC", text))


def render_sources(context: Sequence[ContextChunk]) -> str:
    """Used by the judge. Saved contexts hold no compatibility forms, so this is byte-identical to
    the rendering their recordings were made with (a test holds that)."""
    return "\n".join(
        f'<source id="{html.escape(chunk.source_id)}" type="{html.escape(chunk.source_type)}">\n'
        f"{as_data(chunk.text)}\n</source>"
        for chunk in context
    )


def response_text(response: Any) -> str:
    """The text of a finished response. Raises rather than scoring a partial or refused one."""
    if response.stop_reason != "end_turn":
        raise AnswerError(f"stop_reason {response.stop_reason}")
    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise AnswerError("empty response")
    return str(text)


# --- refinements -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Refinement:
    """The model's `n`th follow-up search on one item (n from 1), and what it returned."""

    item_id: str
    n: int
    query: str
    chunks: tuple[ContextChunk, ...]


def load_refinements(path: Path) -> dict[tuple[str, int], Refinement]:
    if not path.exists():
        return {}
    found: dict[tuple[str, int], Refinement] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        refinement = Refinement(
            item_id=record["item_id"],
            n=record["n"],
            query=record["query"],
            chunks=tuple(ContextChunk(**chunk) for chunk in record["chunks"]),
        )
        key = (refinement.item_id, refinement.n)
        if key in found:
            raise ValueError(f"duplicate refinement {key} in {path}")
        found[key] = refinement
    return found


def save_refinements(path: Path, refinements: Iterator[Refinement] | Sequence[Refinement]) -> None:
    ordered = sorted(refinements, key=lambda r: (r.item_id, r.n))
    lines = [json.dumps(asdict(r), ensure_ascii=False, sort_keys=True) for r in ordered]
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


class LiveSearch(Protocol):
    def search(self, query: str, caller: Any) -> Sequence[Any]: ...


class SavedFirstIndex:
    """What mcp-rag searches during one item's turn: the saved context first, then refinements.

    The first search must be the item's own query, from a shopper in the item's region; anything
    else is refused and recorded as a mismatch, so a wrong first search can never be scored.
    Later searches come from the live index in record mode (and are kept), or from the recorded
    refinements in replay, where a miss is flagged and the item reported stale."""

    def __init__(
        self,
        recorded: Mapping[tuple[str, int], Refinement],
        live: LiveSearch | None = None,
        leaks: Callable[[Sequence[ContextChunk]], bool] | None = None,
    ) -> None:
        self._recorded = dict(recorded)
        self._live = live
        # Checked before a refinement reaches the model, a cassette or a file (hard rule 7): a
        # leaking search is refused, so mcp-rag returns its fixed failure and nothing else.
        self._leaks = leaks
        self.leak = False
        self._lock = threading.Lock()
        self._item: EvalItem | None = None
        self._saved: tuple[ContextChunk, ...] = ()
        self._searches = 0
        self.mismatch = False
        self.miss = False
        self.served: list[ContextChunk] = []
        self.new: list[Refinement] = []

    def begin(self, item: EvalItem, saved: Sequence[ContextChunk]) -> None:
        with self._lock:
            self._item = item
            self._saved = tuple(saved)
            self._searches = 0
            self.mismatch = False
            self.miss = False
            self.leak = False
            self.served = []
            self.new = []

    def search(self, query: str, caller: Any) -> Sequence[Any]:
        from northwind_rag.retrieval import RetrievedChunk

        with self._lock:
            item = self._item
            if item is None:
                self.mismatch = True
                raise RuntimeError("no item is being answered")
            self._searches += 1
            n = self._searches - 1
            if caller.audience != "shopper" or caller.region != item.region:
                self.mismatch = True
                raise RuntimeError("search from an unexpected caller")
            if n == 0:
                if query != item.query:
                    self.mismatch = True
                    raise RuntimeError("the first search is not the item's query")
                chunks = self._saved
            else:
                chunks = self._refine(item, n, query, caller)
                if self._leaks is not None and self._leaking(chunks):
                    raise RuntimeError("a refinement held internal content")
            self.served.extend(chunks)
        return [
            RetrievedChunk(c.chunk_id, c.source_type, c.source_id, None, c.text) for c in chunks
        ]

    def _refine(self, item: EvalItem, n: int, query: str, caller: Any) -> tuple[ContextChunk, ...]:
        if self._live is None:
            recorded = self._recorded.get((item.id, n))
            if recorded is None or recorded.query != query:
                self.miss = True
                raise RuntimeError("no recorded refinement for this search")
            return recorded.chunks
        found = tuple(
            ContextChunk(c.chunk_id, c.source_id, c.source_type, c.text)
            for c in self._live.search(query, caller)
        )
        if self._leaks is None or not self._leaking(found):
            self.new.append(Refinement(item.id, n, query, found))
        return found

    def _leaking(self, chunks: Sequence[ContextChunk]) -> bool:
        """The guard, failing closed: a guard that cannot run counts as a leak (re-review N3)."""
        if self._leaks is None:
            return False
        try:
            leaking = self._leaks(chunks)
        except Exception:
            self.leak = True
            raise
        self.leak = self.leak or leaking
        return leaking

    def sources(self) -> tuple[ContextChunk, ...]:
        """Every chunk served this turn, first seen first."""
        first: dict[str, ContextChunk] = {}
        for chunk in self.served:
            first.setdefault(chunk.chunk_id, chunk)
        return tuple(first.values())


# --- the assistant ---------------------------------------------------------------------------


def _module_sha8(name: str) -> str:
    spec = importlib.util.find_spec(name)
    if spec is None or spec.origin is None:
        raise RuntimeError(f"cannot find {name}")
    return hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest()[:8]


def assistant_id(model: str) -> str:
    """`assistant:v1:<model>:prompt-<sha8>:check-<sha8>`, hashed from the source of the prompt and
    the output check, so neither can change unnoticed (F8). The gate fails on a changed model or
    prompt part and only reports a changed check part: score drift already guards that."""
    prompt = _module_sha8("northwind_assistant.prompt")
    check = _module_sha8("northwind_assistant.output_check")
    return f"assistant:v1:{model}:prompt-{prompt}:check-{check}"


def payload_hits(
    shown: str, sent: str, sources: Sequence[ContextChunk], live: Any = None
) -> dict[str, int]:
    """What the check would still block in text that already passed it, on the text left on
    screen and on every token sent (`shown:<class>`, `sent:<class>`): anything is a bypass. The same
    engine as the live check, so not independent of it: the planted-claim regexes are (L6). `live`
    is the turn's `LiveValues` (row #12), so a price the check let through is not a bypass."""
    from northwind_assistant.output_check import Source, hits

    checked = [
        Source(c.source_id, c.source_type, unicodedata.normalize("NFKC", c.text)) for c in sources
    ]
    seen = {c.source_id for c in sources}
    found = {f"shown:{cls}": n for cls, n in hits(shown, checked, seen, live).items() if n}
    found |= {f"sent:{cls}": n for cls, n in hits(sent, checked, seen, live).items() if n}
    return found


LIVE_SOURCE_TYPE = "catalog"


class CatalogLog:
    """What the in-process mcp-catalog returned during one item's turn (row #12): the tool name,
    the error flag and the text, in order. The judge sees these results, as the model did (D6)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.results: list[tuple[str, bool, str]] = []

    def begin(self) -> None:
        with self._lock:
            self.results = []

    def add(self, tool: str, is_error: bool, text: str) -> None:
        with self._lock:
            self.results.append((tool, is_error, text))

    def taken(self) -> list[tuple[str, bool, str]]:
        with self._lock:
            return list(self.results)


def live_of(
    results: Sequence[tuple[str, bool, str]], region: str
) -> tuple[tuple[ContextChunk, ...], Any]:
    """The turn's catalog results as judge blocks, and its `LiveValues` for the check's numbers.

    A block is one successful, labelled result: id `live:<tool>:<n>`, type `catalog`, the JSON it
    held as text (the judge renders it through `as_data`, like every source). Live values are read
    with the assistant's own `record_live`, so the evals and the check agree on what counts."""
    from northwind_assistant.tools import ToolState, record_live
    from northwind_common import catalog_tool

    state = ToolState(region=region)
    blocks: list[ContextChunk] = []
    for n, (tool, is_error, text) in enumerate(results):
        labelled = None if is_error else catalog_tool.unlabel(text)
        if labelled is None or labelled[0] != tool:  # the label must name the tool called
            continue
        name, value = labelled
        body = json.dumps(value, sort_keys=True, ensure_ascii=False)
        source_id = f"live:{name}:{n}"
        blocks.append(ContextChunk(source_id, source_id, LIVE_SOURCE_TYPE, body))
        record_live(state, f"mcp__{catalog_tool.SERVER_NAME}__{name}", is_error=False, text=text)
    return tuple(blocks), state.live


class AssistantAnswerer:
    """One `stream_answer` turn per item, through an in-process mcp-rag (`serve_retriever`) and,
    from row #12, an in-process mcp-catalog over the verified fixtures."""

    def __init__(
        self,
        proxy_url: str,
        index: SavedFirstIndex,
        rag: Any,
        model: str,
        test_id: Callable[[str], str],
        catalog: Any = None,
        catalog_log: CatalogLog | None = None,
    ) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._index = index
        self._rag = rag
        self._model = model
        self._test_id = test_id
        self._id = assistant_id(model)
        self._catalog = catalog
        self._catalog_log = catalog_log or CatalogLog()

    @property
    def id(self) -> str:
        return self._id

    def answer(self, item: EvalItem, context: Sequence[ContextChunk]) -> Answer:
        from northwind_assistant.caller import Caller
        from northwind_assistant.turn import Reset, TextDelta, TurnError, stream_answer
        from northwind_common.llm import agent_runtime

        self._index.begin(item, context)
        self._catalog_log.begin()
        root = Path(tempfile.mkdtemp(prefix="nw-eval-"))
        try:
            base_url = f"{self._proxy_url}/_t/{self._test_id('eval-answerer:' + item.id)}"
            runtime = agent_runtime(base_url=base_url, root=root)

            async def collect() -> list[Any]:
                caller = Caller(region=item.region)  # type: ignore[arg-type]
                return [
                    e
                    async for e in stream_answer(
                        item.query, caller, self._rag, runtime, self._catalog
                    )
                ]

            events = asyncio.run(collect())
        finally:
            shutil.rmtree(root, ignore_errors=True)
        if self._index.leak:
            raise AnswerError("a refinement held internal content", "leak")
        if self._index.mismatch:
            raise AnswerError("the first search did not match the item", "mismatch")
        if self._index.miss:
            raise StaleAnswer(REFINEMENT_MISS)
        sent: list[str] = []
        shown: list[str] = []
        for event in events:
            if isinstance(event, TextDelta):
                sent.append(event.text)
                shown.append(event.text)
            elif isinstance(event, Reset):
                shown = []
            elif isinstance(event, TurnError):
                if event.code == "replay_miss":
                    raise StaleAnswer(REPLAY_MISS)
                raise AnswerError(f"turn error {event.code}", str(event.code))
        text = "".join(shown)
        if not text.strip():
            raise AnswerError("empty answer", "empty")
        sources = self._index.sources()
        streamed = "".join(sent)
        blocks, live = live_of(self._catalog_log.taken(), item.region)
        # The catalog's visibility filter keeps internal records out of tool results; this is the
        # same leak guard refinements get, before a block reaches the judge or a file (hard rule 7).
        if blocks and self._index._leaking(blocks):
            raise AnswerError("a catalog result held internal content", "leak")
        from northwind_assistant.output_check import live_backed

        return Answer(
            text,
            streamed,
            sources,
            payload_hits(text, streamed, sources, live),
            blocks,
            live_backed(text, live),
        )


@contextlib.contextmanager
def assistant_answerer(
    proxy_url: str,
    recorded: Mapping[tuple[str, int], Refinement],
    model: str,
    test_id: Callable[[str], str],
    live: LiveSearch | None = None,
    leaks: Callable[[Sequence[ContextChunk]], bool] | None = None,
) -> Iterator[tuple[AssistantAnswerer, SavedFirstIndex]]:
    """A real mcp-rag over `SavedFirstIndex` and a real mcp-catalog over the verified fixtures
    (row #12), both signed with one fresh key per run (never the fixed test key), and the answerer
    that talks to them."""
    from northwind_assistant.caller import Caller
    from northwind_assistant.catalog_client import CatalogEndpoint
    from northwind_assistant.rag_client import RagEndpoint
    from northwind_mcp_rag.testing import serve_retriever

    key = secrets.token_bytes(40)
    index = SavedFirstIndex(recorded, live, leaks)
    log = CatalogLog()
    with (
        serve_retriever(index, Caller, key=key) as (url, _server),
        serve_catalog(key, log) as catalog_url,
    ):
        answerer = AssistantAnswerer(
            proxy_url,
            index,
            RagEndpoint(url, key),
            model,
            test_id,
            CatalogEndpoint(catalog_url, key),
            log,
        )
        yield answerer, index


@contextlib.contextmanager
def serve_catalog(key: bytes, log: CatalogLog) -> Iterator[str]:
    """mcp-catalog in-process over `FixtureCatalog` (verified against the manifest before it
    seeds), recording every result into `log`. Fixtures do not change during a run, so a replayed
    turn gets the tool results its recording got."""
    from northwind_mcp_catalog import testing as catalog_testing
    from northwind_mcp_catalog.live import FixtureCatalog
    from northwind_mcp_catalog.server import CatalogServer
    from northwind_mcp_catalog.tools import TOOLS

    class Recording(CatalogServer):
        async def call_tool(self, ctx: Any, params: Any) -> Any:
            result = await super().call_tool(ctx, params)
            text = "".join(getattr(block, "text", "") for block in result.content)
            log.add(str(params.name), bool(result.is_error), text)
            return result

    server = Recording(FixtureCatalog(), key=key, tools=TOOLS)
    with catalog_testing.running(server) as url:
        yield url
