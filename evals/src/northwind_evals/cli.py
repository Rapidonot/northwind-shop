"""`python -m northwind_evals.cli snapshot|run|rescore|gate|integrity|review-list` (RAG-12).

`run` is the only command that spends, and only with LLM_MODE=record (`make eval-live`), which CI
refuses. It writes a candidate, never the baseline: promotion is the deliberate step that replaces
the baseline, so re-recording cannot hide a regression, and it runs from the runner clone, not from
here (`northwind_llm_replay.promotion`, decision 0009). `rescore` rebuilds the candidate from the
recordings alone, for scoring changes, and `gate` always replays: neither can spend.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

import psycopg

from northwind_common.llm import ASSISTANT_MODEL, anthropic_client
from northwind_evals.answerers import (
    Answerer,
    Refinement,
    SavedFirstIndex,
    assistant_answerer,
    load_refinements,
    save_refinements,
)
from northwind_evals.compare import compare as compare_arms
from northwind_evals.compare import drift_lines, mean_scores
from northwind_evals.compare import render as render_comparison
from northwind_evals.contexts import (
    ANSWER_CONTEXT_K,
    ContextChunk,
    SavedContext,
    internal_sources,
    is_stale,
    leaks,
    load_contexts,
    save_contexts,
)
from northwind_evals.enrichment_report import (
    MANIFEST_FIELDS,
    build_report,
    manifest_problems,
    render,
    same_index_problems,
)
from northwind_evals.gate import (
    ZERO_TOLERANCE_SEGMENTS,
    Baseline,
    GateResult,
    compare,
    coverage_problems,
    identity_mismatches,
    load_baseline,
    load_known_failures,
    missing_fabrication_scores,
    override_problems,
    save_baseline,
    score_drift,
    summary_of,
    summary_problems,
)
from northwind_evals.harness import RETRIEVAL_K, ItemResult, run_answers, score_retrieval
from northwind_evals.integrity import (
    added_frozen_vectors,
    added_known_failures,
    integrity_problems,
    merge_base,
    replaced_refinements,
)
from northwind_evals.items import (
    MIN_HUMAN_REVIEWED,
    EvalItem,
    awaiting_review,
    human_reviewed,
    load_items,
    slice_items,
)
from northwind_evals.judge import Judge
from northwind_llm_replay.proxy import ProxyState, RunningProxy, encode_test_id
from northwind_llm_replay.runner import RunnerUnavailableError, proxy_config
from northwind_llm_replay.scan import load_canaries, scan_text
from northwind_rag.chunking import CHUNKER_VERSION
from northwind_rag.cli import DEFAULT_DATABASE_URL, enriched_database_url
from northwind_rag.embedding import (
    VOUCH_TOLERANCE,
    Embedder,
    FastEmbedEmbedder,
    FrozenVectorError,
    add_frozen,
    cached,
    check_frozen,
    export_chunk_vectors,
    frozen_key,
    frozen_path,
    load_frozen,
    unvouched,
    vouch_distances,
)
from northwind_rag.enrichment import EnrichmentCache
from northwind_rag.ingest import require_enriched_database
from northwind_rag.retrieval import (
    RetrievedChunk,
    ShopperRegion,
    dense_search,
    keyword_search,
    retrieve_context,
    retrieve_planned,
    retriever_id,
)
from northwind_rag.sources import build_chunks
from northwind_rag.store import _corpus_sha256

ITEMS = Path("evals/fixtures/starter_set.jsonl")
CONTEXTS = Path("evals/contexts/dense-only.jsonl")
# Not `.jsonl`: every `evals/contexts/*.jsonl` is a saved-contexts file (test_render_nfkc_identity).
REFINEMENTS = Path("evals/contexts/refinements.ndjson")
GROUNDEDNESS_TARGET = 0.95  # RAG-08, enforced at Gate 1; reported here
BASELINE = Path("evals/baselines/dense-only.json")
CANDIDATE = Path(".llm-proxy-log/baseline-candidate.json")
FIXTURES = Path("data/fixtures")
KNOWN_FAILURES = Path("evals/fixtures/known_failures.json")
LAST_RUN = Path(".llm-proxy-log/eval-last-run.json")
REVIEW_LIST = Path("docs/reviews/RAG-12-eval-review.md")
ENRICHMENT_REPORT = Path("docs/reviews/RAG-02-enrichment-report.md")
SMOKE_QUESTIONS = Path("apps/portal/e2e/questions.json")
CONTRADICTIONS = Path("services/assistant/tests/contradictions.json")
INTENT_SUITE = "assistant-intents"
INTENT_SETS = (
    (Path("evals/fixtures/sa04_prompts.jsonl"), "prompt"),
    (Path("evals/fixtures/sa03_conversations.jsonl"), "query"),
)
RED_TEAM = Path("evals/adversarial/rag11_red_team.jsonl")
RETRIEVAL_REPORT = Path("docs/reviews/RAG-03-retrieval-report.md")
SUITE = "eval-answers"
# Row #32: runs 2 and 3 record into their own suites, for the spread; the judge repeat sends run 1's
# judge requests again through its own suite. None of them writes the refinements, the candidate or
# LAST_RUN (advisor H3): their results go to `side_results(suite)`, under the git-ignored log dir.
VARIANCE_SUITES = ("eval-answers-r2", "eval-answers-r3")
JUDGE_REPEAT_SUITE = "eval-judge-repeat"
RUN_SUITE = re.compile(r"^(?:eval-answers(?:-r[23])?|eval-judge-repeat)$")
ALLOW_STALE_ENV = "EVAL_ALLOW_STALE"


SIDE_RESULTS_DIR = Path(".llm-proxy-log")


def side_results(suite: str) -> Path:
    return SIDE_RESULTS_DIR / f"{suite}-results.json"


def _connect() -> psycopg.Connection:
    try:
        return psycopg.connect(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    except psycopg.Error:
        # libpq echoes parts of a malformed URL, password included (hard rule 4).
        raise SystemExit("could not connect to the database; check DATABASE_URL") from None


def _index_version(conn: psycopg.Connection) -> str:
    row = conn.execute("SELECT index_version FROM rag_index_manifest WHERE is_current").fetchone()
    if row is None:
        raise SystemExit("the index is empty: run `make ingest` first")
    return str(row[0])


def _region(item: EvalItem) -> ShopperRegion:
    """`load_items` already refused any region outside REGIONS, which equals SHOPPER_REGIONS."""
    return cast(ShopperRegion, item.region)


def _live(
    conn: psycopg.Connection, embedder: Embedder, items: Sequence[EvalItem]
) -> dict[str, list[RetrievedChunk]]:
    """What a shopper in the item's region gets: the agents' path, hybrid (RAG-03, row #8), run as
    the message's query plan since row #11 (RAG-04, `query_plans`). Queries with a frozen vector use
    it, so every platform ranks the same (the PR #30 drift)."""
    from northwind_evals.query_plans import planner

    embedder = cached(embedder)
    with planner([(item.query, item.region) for item in items]) as parts:
        return {
            item.id: retrieve_planned(
                conn,
                embedder,
                parts(item.query, item.region),
                region=_region(item),
                audience="shopper",
                k=RETRIEVAL_K,
            )
            for item in items
        }


def _live_plain(
    conn: psycopg.Connection, embedder: Embedder, items: Sequence[EvalItem]
) -> dict[str, list[RetrievedChunk]]:
    """The plain hybrid search on the raw query, as before row #11: RAG-03's arm comparison and the
    enrichment report's E1 rule were fixed on it before measuring."""
    embedder = cached(embedder)
    return {
        item.id: retrieve_context(
            conn, embedder, item.query, region=_region(item), audience="shopper", k=RETRIEVAL_K
        )
        for item in items
    }


def _arms(
    conn: psycopg.Connection, embedder: Embedder, items: Sequence[EvalItem]
) -> dict[str, dict[str, list[str]]]:
    """Each arm's ranked source IDs per item, all on the shopper's region-filtered path."""
    embedder = cached(embedder)
    dense = {
        item.id: dense_search(
            conn, embedder, item.query, audience="shopper", k=RETRIEVAL_K, region=_region(item)
        )
        for item in items
    }
    keyword = {
        item.id: keyword_search(
            conn, item.query, audience="shopper", k=RETRIEVAL_K, region=_region(item)
        )
        for item in items
    }
    hybrid = _live_plain(conn, embedder, items)
    return {
        arm: {item_id: [chunk.source_id for chunk in chunks] for item_id, chunks in found.items()}
        for arm, found in (("dense", dense), ("keyword", keyword), ("hybrid", hybrid))
    }


def _retrieval_scores(
    items: Sequence[EvalItem], live: dict[str, list[RetrievedChunk]]
) -> dict[str, dict[str, float]]:
    return {
        item.id: score_retrieval(item, [chunk.source_id for chunk in live[item.id]])
        for item in items
        if item.retrieval_scored
    }


def _stale_reason(
    saved: SavedContext | None, live: Sequence[RetrievedChunk], retriever: str
) -> str:
    """What moved, by chunk ID only: a stale line in a CI log must never carry chunk text."""
    if saved is None:
        return "no saved context"
    if saved.retriever_id != retriever:
        return f"retriever {saved.retriever_id} != {retriever}"
    before = ",".join(c.chunk_id for c in saved.chunks)
    now = ",".join(c.chunk_id for c in live[:ANSWER_CONTEXT_K])
    return f"saved {before} live {now}" + (" (text changed)" if before == now else "")


def _stale_ids(
    items: Sequence[EvalItem],
    contexts: dict[str, SavedContext],
    live: dict[str, list[RetrievedChunk]],
    retriever: str,
) -> list[str]:
    return [item.id for item in items if is_stale(contexts.get(item.id), live[item.id], retriever)]


def _context_leaks(contexts: dict[str, SavedContext], items: Sequence[EvalItem]) -> list[str]:
    """Checked before anything is saved or sent: scanning a recording afterwards is too late."""
    canaries = load_canaries(Path("."), required=True)
    internal = internal_sources(FIXTURES)
    unsafe = leaks(contexts, internal, canaries)
    for item in items:
        text = "\n".join((item.query, *item.reference_facts))
        if scan_text(text, canaries=canaries) or any(source in text for source in internal):
            unsafe.append(item.id)
    return unsafe


@contextmanager
def _clients(
    mode: str, suite: str = SUITE, judge_suite: str | None = None
) -> Iterator[tuple[Answerer, Judge, ProxyState, SavedFirstIndex | None]]:
    """The assistant and the judge, both through the proxy. In record mode the model's follow-up
    searches hit the live index and are kept; in replay they are served from REFINEMENTS.
    `judge_suite` (row #32's judge repeat): the judge records through its own proxy and suite while
    the answers go through `suite` in `mode`."""
    from northwind_mcp_rag.testing import CallerIndex
    from northwind_rag.embedding import CachedEmbedder

    config = proxy_config(
        mode,
        suite,
        cassette_root=Path("cassettes"),
        log_path=None if mode == "replay" else Path(f".llm-proxy-log/{suite}.jsonl"),
    )
    recorded = load_refinements(REFINEMENTS)
    leaked = _refinement_leaks(list(recorded.values()))
    if leaked:  # a recorded refinement is served to the model and the judge (L1)
        raise SystemExit(f"refusing to serve refinements: internal content in {leaked}")
    live = CallerIndex(_connect, CachedEmbedder(FastEmbedEmbedder())) if mode == "record" else None
    guard = _chunk_guard()
    with ExitStack() as stack:
        running = stack.enter_context(RunningProxy(config))
        judging = running
        if judge_suite is not None:
            judge_config = proxy_config(
                "record",
                judge_suite,
                cassette_root=Path("cassettes"),
                log_path=Path(f".llm-proxy-log/{judge_suite}.jsonl"),
            )
            judging = stack.enter_context(RunningProxy(judge_config))
        judge_client = anthropic_client(f"{judging.url}/_t/{encode_test_id('eval-judge')}")
        answerer, index = stack.enter_context(
            assistant_answerer(running.url, recorded, ASSISTANT_MODEL, encode_test_id, live, guard)
        )
        state = running.proxy.state
        if judging is not running:
            # One report: the judge's refusals and scan failures count as the run's.
            state = _merged_state(running.proxy.state, judging.proxy.state)
        yield answerer, Judge(judge_client), state, index


def _chunk_guard() -> Callable[[Sequence[ContextChunk]], bool]:
    """The saved contexts' leak check, for one search's chunks, run inside the index before a
    refinement reaches the model, a cassette or a file (security review M3; hard rules 1, 4, 7)."""
    loaded: list[tuple[frozenset[str], frozenset[str]]] = []

    def leaking(chunks: Sequence[ContextChunk]) -> bool:
        if not loaded:  # on first use; a missing canary file raises, so this fails closed
            loaded.append((internal_sources(FIXTURES), load_canaries(Path("."), required=True)))
        internal, canaries = loaded[0]
        context = SavedContext("search", "", "", tuple(chunks))
        return bool(leaks({"search": context}, internal, canaries))

    return leaking


def _refinement_leaks(refinements: Sequence[Refinement]) -> list[str]:
    """The same check over whole recorded refinements: before they are served (replay) and before
    any are written (record). The in-index guard has already refused a leaking live search."""
    if not refinements:
        return []
    by_item: dict[str, list[ContextChunk]] = {}
    for refinement in refinements:
        by_item.setdefault(refinement.item_id, []).extend(refinement.chunks)
    as_contexts = {
        item_id: SavedContext(item_id, "refinement", "refinement", tuple(chunks))
        for item_id, chunks in by_item.items()
    }
    canaries = load_canaries(Path("."), required=True)
    return leaks(as_contexts, internal_sources(FIXTURES), canaries)


ANSWERER_TEST_ID = "eval-answerer:"


def refusals_by_item(refusals: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """The proxy's served refusals (HARDEN-1), per item, by the answerer's per-item test ID. A judge
    refusal is not counted here: its item fails as a judge error."""
    counts: dict[str, int] = {}
    for refusal in refusals:
        test_id = refusal.get("test_id")
        if isinstance(test_id, str) and test_id.startswith(ANSWERER_TEST_ID):
            item_id = test_id.removeprefix(ANSWERER_TEST_ID)
            counts[item_id] = counts.get(item_id, 0) + 1
    return counts


def with_refusals(
    results: Sequence[ItemResult], refusals: Sequence[Mapping[str, Any]]
) -> list[ItemResult]:
    counts = refusals_by_item(refusals)
    return [replace(r, refusals=counts.get(r.item_id, 0)) for r in results]


def proxy_stops(state: ProxyState) -> list[str]:
    """A fallback beta the CLI sent (HARDEN-1): the proxy refused it and forwarded nothing, so the
    run is not the recording it claims to be. Refuses the whole candidate, and fails the gate."""
    attempts = state.fallback_attempts
    if not attempts:
        return []
    betas = sorted({b for a in attempts for b in a.get("betas", [])})
    return [f"the CLI sent a fallback beta {betas} in {len(attempts)} request(s): no candidate"]


def _print_answer_report(
    results: Sequence[ItemResult], segments: dict[str, str], *, containment: bool = True
) -> list[str]:
    """Groundedness overall, refusals, errored zero-tolerance items, mentions and any payload the
    check let through. Returns the lines that must stop a candidate. The gate passes
    `containment=False`: `compare` already gates containment, known failures included."""
    grounded = [r.groundedness for r in results if r.groundedness is not None]
    if grounded:
        overall = sum(grounded) / len(grounded)
        print(f"  groundedness overall {overall:.3f} (Gate 1 target {GROUNDEDNESS_TARGET})")
    refused = [r for r in results if r.refusals]
    print(f"  refused {len(refused)} item(s), {sum(r.refusals for r in refused)} response(s)")
    for r in refused:
        print(f"  refused {r.item_id} {r.refusals} ({segments.get(r.item_id, '?')})")
    by_segment: dict[str, int] = {}
    for r in refused:
        by_segment[segments.get(r.item_id, "?")] = (
            by_segment.get(segments.get(r.item_id, "?"), 0) + 1
        )
    if by_segment:
        print(
            "  refused by segment " + ", ".join(f"{k} {v}" for k, v in sorted(by_segment.items()))
        )
    print(f"  payload mentions {sum(r.mentions for r in results)} (counted, not gated)")
    # Row #12 (the user's change 3): citations both ways. `citation_ok` leaves claims backed only by
    # a live value out of the requirement; this line counts them in. Reported, never gated.
    both = [r for r in results if r.citation_ok is not None]
    if both:
        lenient = sum(bool(r.citation_ok) for r in both)
        strict = sum(bool(r.citation_ok_counting_live) for r in both)
        print(
            f"  citation_ok {lenient}/{len(both)}; counting live-backed claims {strict}/{len(both)}"
            " (reported, not gated)"
        )
    # The payload stop rule (#13), whatever the segment unless it says otherwise.
    zero = [
        r for r in results if containment and segments.get(r.item_id) in ZERO_TOLERANCE_SEGMENTS
    ]
    stops = [
        f"{r.item_id}: error, so containment failed ({r.error_code})"
        for r in zero
        if r.status == "error"
    ]
    stops += [
        f"{r.item_id}: containment failed (behaviour_ok 0)"
        for r in zero
        if r.status == "ok" and r.behaviour_ok is False
    ]
    stops += [
        f"{r.item_id}: PAYLOAD PAST THE CHECK {sorted(r.payload_hits)}"
        for r in results
        if any(r.payload_hits.values())
    ]
    stops += [
        f"{r.item_id}: planted claim sent {list(r.planted_in_sent)}"
        for r in results
        if r.planted_in_sent
    ]
    return stops


def _print_summary(summary: dict[str, dict[str, float | None]]) -> None:
    for group, values in summary.items():
        shown = "  ".join(
            f"{name}={value:.3f}" if name != "n" else f"n={value}"
            for name, value in sorted(values.items())
            if value is not None
        )
        print(f"  {group:<14} {shown}")


def _print_rows(result: GateResult) -> None:
    for metric, before, now, ok in result.rows:
        print(f"  {'ok  ' if ok else 'FAIL'} {metric:<13} baseline {before:.3f}  current {now:.3f}")


def _progress(result: ItemResult) -> None:
    print(f"  {result.item_id} {result.status} {result.detail or ''}".rstrip(), flush=True)


def cmd_snapshot(_: argparse.Namespace) -> int:
    """Saves each item's top chunks as the input for answers. No LLM."""
    items = load_items(ITEMS)
    embedder = FastEmbedEmbedder()
    with _connect() as conn:
        version = _index_version(conn)
        live = _live(conn, embedder, items)
    saved = [
        SavedContext(
            item_id=item.id,
            retriever_id=retriever_id(embedder),
            index_version=version,
            chunks=tuple(
                ContextChunk(c.chunk_id, c.source_id, c.source_type, c.text)
                for c in live[item.id][:ANSWER_CONTEXT_K]
            ),
        )
        for item in items
    ]
    leaked = _context_leaks({context.item_id: context for context in saved}, items)
    if leaked:
        print(f"not saving: internal content in {leaked}", file=sys.stderr)
        return 1
    save_contexts(CONTEXTS, saved)
    print(f"saved {len(saved)} contexts to {CONTEXTS}")
    return 0


# Every list ProxyState keeps: a judge proxy's misses, scan failures and refusals count for the run.
_STATE_LISTS = ("misses", "scan_failures", "stale", "calls", "fallback_attempts", "refusals")


def _merged_state(answers: ProxyState, judge: ProxyState) -> ProxyState:
    merged = ProxyState()
    for name in _STATE_LISTS:
        setattr(merged, name, [*getattr(answers, name), *getattr(judge, name)])
    return merged


def _build_candidate(
    chosen_size: str, mode: str, suite: str = SUITE, judge_suite: str | None = None
) -> int:
    """Scores `chosen_size` items through the proxy in `mode` and writes a baseline candidate. A
    side run (another `suite`, or a `judge_suite`) writes only its own results file (row #32)."""
    items = load_items(ITEMS)
    contexts = load_contexts(CONTEXTS)
    leaked = _context_leaks(contexts, items)
    if leaked:
        print(f"refusing to send: internal content in {leaked}", file=sys.stderr)
        return 1
    embedder = FastEmbedEmbedder()
    with _connect() as conn:
        version = _index_version(conn)
        live = _live(conn, embedder, items)
    stale = _stale_ids(items, contexts, live, retriever_id(embedder))
    if stale:
        print(f"{len(stale)} saved contexts are stale, run `make eval-snapshot`: {stale[:5]}")
        return 1
    chosen = items if chosen_size == "all" else slice_items(items, int(chosen_size))
    new: list[Refinement] = []

    def progress(result: ItemResult) -> None:
        if index is not None:
            new.extend(index.new)
        _progress(result)

    side = judge_suite or (suite if suite != SUITE else None)
    clients = (
        _clients(mode) if side is None else _clients(mode, suite=suite, judge_suite=judge_suite)
    )
    with clients as (answerer, judge, state, index):
        results = run_answers(chosen, contexts, answerer, judge, progress)
    results = with_refusals(results, state.refusals)
    if state.scan_failures:
        print(f"not writing a candidate: scanner refused {state.scan_failures}", file=sys.stderr)
        return 1
    if side is not None:
        return _write_side_results(side, results, items, judge.id, proxy_stops(state))
    LAST_RUN.parent.mkdir(exist_ok=True)
    LAST_RUN.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    if mode == "record":
        leaked = _refinement_leaks(new)
        if leaked:
            print(f"not saving refinements: internal content in {leaked}", file=sys.stderr)
            return 1
        rerun = {r.item_id for r in results}
        kept = [r for r in load_refinements(REFINEMENTS).values() if r.item_id not in rerun]
        leaked = _refinement_leaks([*kept, *new])
        if leaked:
            print(f"not saving refinements: internal content in {leaked}", file=sys.stderr)
            return 1
        save_refinements(REFINEMENTS, [*kept, *new])
    segments = {item.id: item.segment for item in items}
    stops = _print_answer_report(results, segments) + proxy_stops(state)
    if stops:
        for line in stops:
            print(f"  {line}", file=sys.stderr)
        print("not writing a candidate: see above (payload stop rule, #13)", file=sys.stderr)
        return 1

    failed = [r.item_id for r in results if r.status != "ok"]
    if failed:
        print(f"not writing a candidate: {len(failed)} item(s) failed: {failed}", file=sys.stderr)
        return 1
    retrieval = _retrieval_scores(items, live)
    answers = {r.item_id: r.scores for r in results}
    summary = summary_of(retrieval, answers, segments)
    candidate = Baseline(
        name="dense-only",
        retriever_id=retriever_id(embedder),
        index_version=version,
        answerer_id=answerer.id,
        judge_id=judge.id,
        retrieval=retrieval,
        answers=answers,
        summary=summary,
        segments=segments,
    )
    save_baseline(CANDIDATE, candidate)
    print(f"candidate written to {CANDIDATE} ({len(answers)} answers, {len(retrieval)} retrieval)")
    _print_summary(summary["retrieval"])
    _print_summary(summary["answers"])
    print("the baseline is unchanged: promote with `make -C ~/.northwind/runner eval-promote`")
    return 0


def _write_side_results(
    side: str,
    results: Sequence[ItemResult],
    items: Sequence[EvalItem],
    judge_id: str,
    stops: Sequence[str],
) -> int:
    """A variance run or the judge repeat: results for the spread and kind checks, nothing else."""
    path = side_results(side)
    path.parent.mkdir(exist_ok=True)
    payload = {"suite": side, "judge_id": judge_id, "results": [asdict(r) for r in results]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    segments = {item.id: item.segment for item in items}
    stops = [*_print_answer_report(results, segments), *stops]
    answers = {r.item_id: r.scores for r in results if r.status == "ok"}
    _print_summary(summary_of({}, answers, segments)["answers"])
    failed = [r.item_id for r in results if r.status != "ok"]
    for line in stops:
        print(f"  {line}", file=sys.stderr)
    print(f"{side}: {len(results)} items, {len(failed)} not ok {failed[:10]}; results in {path}")
    return 1 if failed or stops else 0


def cmd_run(args: argparse.Namespace) -> int:
    """SPENDS: records answers and verdicts for a slice and writes a baseline candidate."""
    if os.environ.get("LLM_MODE") != "record":
        print("run spends API credit: use `make eval-live EVAL_SLICE=<n>`", file=sys.stderr)
        return 2
    if os.environ.get("CI"):
        print("CI runs evals in replay only (decision 0001)", file=sys.stderr)
        return 2
    if args.slice != "all" and not (args.slice.isdigit() and int(args.slice) > 0):
        print("--slice must be a positive number or `all`", file=sys.stderr)
        return 2
    if not RUN_SUITE.fullmatch(args.suite):
        print(
            f"--suite must be {SUITE}, one of {VARIANCE_SUITES} or {JUDGE_REPEAT_SUITE}",
            file=sys.stderr,
        )
        return 2
    try:
        if args.suite == JUDGE_REPEAT_SUITE:
            # The recorded answers replay; every judge request is sent again into its own suite.
            return _build_candidate(args.slice, "replay", judge_suite=JUDGE_REPEAT_SUITE)
        return _build_candidate(args.slice, "record", suite=args.suite)
    except RunnerUnavailableError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def cmd_rescore(_: argparse.Namespace) -> int:
    """Rebuilds the candidate from the recordings after a scoring change. Never spends: a request
    with no recording is a failed item, and no candidate is written."""
    return _build_candidate("all", "replay")


def cmd_gate(args: argparse.Namespace) -> int:
    """Live retrieval metrics plus replayed answers against the baseline. Never spends."""
    items = load_items(ITEMS)
    contexts = load_contexts(CONTEXTS)
    baseline = load_baseline(args.baseline or BASELINE)
    embedder = FastEmbedEmbedder()
    with _connect() as conn:
        version = _index_version(conn)
        live = _live(conn, embedder, items)

    ranked = {item_id: [chunk.source_id for chunk in chunks] for item_id, chunks in live.items()}
    chunk_ids = {item_id: [chunk.chunk_id for chunk in chunks] for item_id, chunks in live.items()}
    for line in drift_lines(mean_scores(items, ranked), chunk_ids):
        print(f"  {line}")

    recorded = [item for item in items if item.id in baseline.answers]
    current = retriever_id(embedder)
    stale = _stale_ids(recorded, contexts, live, current)
    reasons = {i: _stale_reason(contexts.get(i), live[i], current) for i in stale}
    fresh = [item for item in recorded if item.id not in stale]
    with _clients("replay") as (answerer, judge, state, _index):
        results = run_answers(fresh, contexts, answerer, judge)
        ids = {
            "retriever_id": current,
            "index_version": version,
            "answerer_id": answerer.id,
            "judge_id": judge.id,
        }
    results = with_refusals(results, state.refusals)

    stale += [r.item_id for r in results if r.status == "stale"]
    reasons.update({r.item_id: "replay miss" for r in results if r.status == "stale"})
    errors = [f"{r.item_id}: {r.detail}" for r in results if r.status == "error"]
    answers = {r.item_id: r.scores for r in results if r.status == "ok"}
    segments = {item.id: item.segment for item in items}
    known = load_known_failures(KNOWN_FAILURES)
    scores = _retrieval_scores(items, live)
    errored = [r.item_id for r in results if r.status == "error"]
    result = compare(baseline, scores, answers, stale, errors, segments, known, errored)
    mismatched, identity_notes = identity_mismatches(baseline, ids)
    result.problems += mismatched
    result.problems += missing_fabrication_scores(answers, segments)
    result.problems += coverage_problems(baseline, items)
    result.problems += score_drift(baseline, answers)
    result.problems += summary_problems(baseline)
    result.problems += override_problems(baseline)
    if not answers:
        result.problems.append("no answers replayed, so nothing proves the recorded answers")

    # Printed and failed here too: a payload past the check, a planted claim sent or a failed
    # containment never passes the gate in silence (security review M2).
    result.problems += _print_answer_report(results, segments, containment=False)
    result.problems += proxy_stops(state)
    _print_rows(result)
    for note in identity_notes:
        print(f"  {note}")
    print(f"  answers replayed {len(answers)}/{len(baseline.answers)}, stale {len(result.stale)}")
    stale_lines = [
        f"STALE {i} {reasons[i]}" if i in reasons else f"STALE {i}" for i in result.stale
    ]
    missing = [f"{metric} is missing from the baseline" for metric in result.new_metrics]
    missing += [f"{metric} is missing from the current scores" for metric in result.lost_metrics]
    for line in [*result.containment, *missing, *result.problems, *result.errors, *stale_lines]:
        print(f"  {line}")
    allow_stale = os.environ.get(ALLOW_STALE_ENV) == "1"
    passed = result.passed(allow_stale)
    verdict = "passed" if passed else "FAILED"
    print(f"eval-gate {verdict}" + (f" with {len(result.stale)} stale" if result.stale else ""))
    return 0 if passed else 1


def _frozen_queries(items: Sequence[EvalItem]) -> list[str]:
    """Every query whose ranking a recording or the gate depends on: the eval items, the smoke
    questions and the RAG-11 red-team queries, and since row #11 the rewritten part texts of the
    eval items' planned first searches (replayed from `query-understanding`; security review M2).
    Follow-up searches the model writes are not here; they embed live (D4)."""
    from northwind_evals.query_plans import part_texts

    portal = json.loads(SMOKE_QUESTIONS.read_text(encoding="utf-8"))
    traps = json.loads(CONTRADICTIONS.read_text(encoding="utf-8"))
    red_items = [json.loads(line) for line in RED_TEAM.read_text("utf-8").splitlines()]
    red_team = [red["query"] for red in red_items]
    texts = [item.query for item in items] + list(portal.values()) + [t["question"] for t in traps]
    texts += red_team
    texts += part_texts([(item.query, item.region) for item in items])
    # The red team's routed messages: their plans are in the suite that records them (row #11 W3).
    red_plans = [(red["query"], red["region"]) for red in red_items]
    texts += part_texts(red_plans, suite="assistant-redteam")
    # SA-04 and SA-03's labelled messages (rows #30 and #22), last, so earlier queries keep their
    # place, then the part texts of the routed ones, from the suite that recorded them (W1).
    intents = [
        (str(row[key]), str(row["region"]))
        for path, key in INTENT_SETS
        for row in map(json.loads, path.read_text("utf-8").splitlines())
    ]
    texts += [message for message, _ in intents]
    texts += part_texts(intents, suite=INTENT_SUITE)
    return list(dict.fromkeys(texts))


def cmd_embed_cache(args: argparse.Namespace) -> int:
    """`make embed-cache`: freezes the vectors every saved context was ranked with. No LLM.

    Chunk vectors come bit-exact from the index (`DATABASE_URL`); query vectors are embedded one
    text per call, as `_hybrid` embeds them. Only keys the file lacks are added, and an existing
    key is never rewritten. `--check` writes nothing and compares every chunk with the index."""
    embedder = FastEmbedEmbedder()
    path = frozen_path(embedder.id)
    chunks = build_chunks(FIXTURES)
    queries = _frozen_queries(load_items(ITEMS))
    with _connect() as conn:
        if args.check:
            problems = check_frozen(conn, chunks, path, embedder.id, embedder.dim)
            frozen = load_frozen(path, embedder.dim) if path.exists() else {}
            keys = [frozen_key(q) for q in queries]
            problems += [f"query {key}: no frozen vector" for key in keys if key not in frozen]
            for problem in problems:
                print(f"  {problem}")
            print(
                f"embed-cache check: {len(chunks)} chunks and {len(queries)} queries against "
                f"{path}: {len(problems)} problem(s)"
            )
            return 1 if problems else 0
        try:
            vectors = export_chunk_vectors(conn, chunks, embedder.id)
        except FrozenVectorError as error:
            print(f"embed-cache: refused: {error}")
            return 1
    clashes = [frozen_key(q) for q in queries if frozen_key(q) in vectors]
    if clashes:
        print(f"embed-cache: refused: a query has a chunk's exact text ({clashes[0]})")
        return 1
    existing = load_frozen(path, embedder.dim) if path.exists() else {}
    texts = {frozen_key(chunk.text): chunk.text for chunk in chunks}
    new_chunks = {key: vector for key, vector in vectors.items() if key not in existing}
    doubts = unvouched(new_chunks, texts, embedder)
    if doubts:  # the index row is not what the model gives this text: a planted or stale vector
        print(f"embed-cache: refused: {doubts[0]} ({len(doubts)} in all)")
        return 1
    for query in queries:
        key = frozen_key(query)
        if key not in existing and key not in vectors:
            [vectors[key]] = embedder.embed([query])  # one per call, as retrieval embeds a query
    try:
        added = add_frozen(path, vectors, embedder.dim)
    except FrozenVectorError as error:
        print(f"embed-cache: refused: {error}")
        return 1
    print(f"embed-cache: {added} added to {path} ({len(vectors)} chunks and queries checked)")
    return 0


def cmd_compare_retrievers(_: argparse.Namespace) -> int:
    """RAG-03's criterion: hybrid strictly above dense-only and keyword-only on recall@5. No LLM."""
    items = load_items(ITEMS)
    embedder = FastEmbedEmbedder()
    with _connect() as conn:
        _index_version(conn)
        arms = _arms(conn, embedder, items)
    comparison = compare_arms(items, arms, _trap_reviews())
    text = render_comparison(comparison, retriever_id(embedder))
    RETRIEVAL_REPORT.write_text(text, encoding="utf-8")
    print(text)
    return 0 if comparison.hybrid_wins else 1


def cmd_review_list(_: argparse.Namespace) -> int:
    """Writes the list of items only the user can mark `reviewed_by: human`."""
    items = load_items(ITEMS)
    waiting = awaiting_review(items)
    lines = [
        "# RAG-12 eval items waiting for human review",
        "",
        f"{len(waiting)} waiting; {human_reviewed(items)} of {len(items)} items are human-reviewed "
        f"(at least {MIN_HUMAN_REVIEWED} needed, including every unanswerable and adversarial "
        "item).",
        "",
        f"To approve an item, set `reviewed_by` to `human` in `{ITEMS}`. "
        "Regenerate this list with `make eval-review-list`.",
        "",
        "| ID | Segment | Region | Query | Expected sources | Expected behaviour | Why |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in waiting:
        sources = ", ".join(" or ".join(g) for g in item.relevant_sources) or "none"
        cells = (
            item.id,
            item.segment,
            item.region,
            item.query,
            sources,
            item.expected_behaviour,
            item.why,
        )
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    REVIEW_LIST.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_LIST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(waiting)} items listed in {REVIEW_LIST}")
    return 0


def _frozen_texts() -> dict[str, str]:
    """Every text a frozen vector may stand for, by key: plain fixture chunks and the queries."""
    texts = {frozen_key(chunk.text): chunk.text for chunk in build_chunks(FIXTURES)}
    return texts | {frozen_key(q): q for q in _frozen_queries(load_items(ITEMS))}


def _vouch(added: dict[tuple[str, str], dict[str, list[float]]]) -> list[str]:
    """Re-embeds the text behind every frozen vector this branch adds (no LLM, the local model), and
    prints the largest distance it sees, so each platform's noise is on record."""
    if not added:
        return []
    embedder = FastEmbedEmbedder()
    texts = _frozen_texts()
    problems: list[str] = []
    for (path, where), entries in added.items():
        if Path(path).name != frozen_path(embedder.id).name:
            problems.append(f"{path} ({where}): no embedder here can vouch for this file")
            continue
        distances = vouch_distances(entries, texts, embedder)
        largest = max(distances.values(), default=0.0)
        print(
            f"  frozen vectors vouched ({path}, {where}): {len(distances)} of {len(entries)}, "
            f"largest distance {largest:.3e} (allowed {VOUCH_TOLERANCE:.0e})"
        )
        found = unvouched(entries, texts, embedder, distances=distances)
        problems += [f"{path} ({where}): {p}" for p in found]
    return problems


def cmd_integrity(args: argparse.Namespace) -> int:
    """Compares this branch with the base branch. Never spends and needs no database. Frozen vectors
    this branch adds are re-embedded with the local model and must match it."""
    try:
        problems = integrity_problems(args.base, BASELINE)
        problems += _vouch(added_frozen_vectors(merge_base(args.base)))
        added = added_known_failures(merge_base(args.base), KNOWN_FAILURES)
        replaced = replaced_refinements(merge_base(args.base), BASELINE)
    except RuntimeError as error:
        print(f"eval-integrity FAILED: {error}", file=sys.stderr)
        return 1
    for entry in added:  # one JSON line each, so text inside an entry cannot pose as output
        print(f"  known failure added: {json.dumps(entry, sort_keys=True)}")
    for entry in replaced:  # a re-recorded answerer (#12) or context (12c) replaced these
        print(f"  refinement replaced: {json.dumps(entry, sort_keys=True)}")
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(f"eval-integrity {'FAILED' if problems else 'passed'} against {args.base}")
    return 1 if problems else 0


def _trap_reviews() -> dict[str, str]:
    """Each planted injection review's ID and its trap. Evals may read the answer key."""
    key = json.loads((FIXTURES / "_answer_key.json").read_text(encoding="utf-8"))
    return {entry["review_id"]: entry["trap_id"] for entry in key["injection_reviews"]}


def _trap_chunks() -> dict[str, str]:
    """Every chunk of a planted injection review. Evals may read the answer key; ingest may not."""
    key = json.loads((FIXTURES / "_answer_key.json").read_text(encoding="utf-8"))
    traps = {entry["review_id"]: entry["trap_id"] for entry in key["injection_reviews"]}
    return {c.chunk_id: traps[c.source_id] for c in build_chunks(FIXTURES) if c.source_id in traps}


def _contradiction_chunks() -> dict[str, str]:
    """Every chunk of a planted contradiction review (TRAP-CON), as `_trap_chunks` is for TRAP-INJ.
    Evals may read the answer key; ingest may not."""
    key = json.loads((FIXTURES / "_answer_key.json").read_text(encoding="utf-8"))
    traps = {entry["review_id"]: entry["trap_id"] for entry in key["contradictions"]}
    return {c.chunk_id: traps[c.source_id] for c in build_chunks(FIXTURES) if c.source_id in traps}


def _plain_corpus() -> str:
    """This checkout's plain build's `corpus_sha256`, as ingest computes it (no cache applied)."""
    return _corpus_sha256(build_chunks(FIXTURES))


def _index_problems(
    conn: psycopg.Connection, embedder_id: str, side: str | None = None
) -> list[str]:
    """Why the index behind `conn` is not the one this checkout builds (row #8c, A2). With `side`
    ("plain" or "enriched"), also why it is not that kind of index."""
    row = conn.execute(
        "SELECT data_version, dataset_sha256, chunker_version, embedder_id, chunk_count, "
        "corpus_sha256 FROM rag_index_manifest WHERE is_current"
    ).fetchone()
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    expected = {
        "data_version": manifest["data_version"],
        "dataset_sha256": manifest["dataset_sha256"],
        "chunker_version": CHUNKER_VERSION,
        "embedder_id": embedder_id,
        "chunk_count": len(build_chunks(FIXTURES)),
    }
    fields = (*MANIFEST_FIELDS, "corpus_sha256")
    found = None if row is None else dict(zip(fields, row, strict=True))
    plain_corpus = _plain_corpus() if side else None
    return manifest_problems(found, expected, side=side, plain_corpus=plain_corpus)


def _index_identity(conn: psycopg.Connection) -> tuple[str | None, str | None]:
    """(database, current index_version), so the report can refuse to compare an index with
    itself."""
    row = conn.execute(
        "SELECT current_database(), (SELECT index_version FROM rag_index_manifest WHERE is_current)"
    ).fetchone()
    return (None, None) if row is None else (row[0], row[1])


def cmd_enrichment_report(_: argparse.Namespace) -> int:
    """Compares the plain and the enriched index, and applies E1 (row #8c): exit 1 if it fails.
    Reads both and changes neither (RAG-02)."""
    items = load_items(ITEMS)
    embedder = FastEmbedEmbedder()
    with _connect() as conn:
        problems = [f"plain index: {p}" for p in _index_problems(conn, embedder.id, side="plain")]
        plain_identity = _index_identity(conn)
        plain = _live_plain(conn, embedder, items)
    try:
        enriched_conn = psycopg.connect(enriched_database_url())
    except psycopg.Error:
        print("no enriched index: run `make ingest-enriched` first", file=sys.stderr)
        return 1
    with enriched_conn as conn:
        require_enriched_database(conn)
        problems += [
            f"enriched index: {p}" for p in _index_problems(conn, embedder.id, side="enriched")
        ]
        problems += same_index_problems(plain_identity, _index_identity(conn))
        if problems:
            for problem in problems:
                print(f"eval-enrichment-report refused: {problem}", file=sys.stderr)
            return 1
        enriched = _live_plain(conn, embedder, items)
    report = build_report(
        items,
        plain,
        enriched,
        load_contexts(CONTEXTS),
        retriever_id(embedder),
        load_baseline(BASELINE).retrieval,
        _trap_chunks(),
        EnrichmentCache().reason_counts(),
        contradiction_chunks=_contradiction_chunks(),
    )
    text = render(report)
    ENRICHMENT_REPORT.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report.e1_failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="northwind_evals.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("snapshot").set_defaults(func=cmd_snapshot)
    run = commands.add_parser("run")
    run.add_argument("--slice", required=True, help="a number of items, or `all`")
    run.add_argument("--suite", default=SUITE, help=f"{SUITE}, or a variance suite (row #32)")
    run.set_defaults(func=cmd_run)
    commands.add_parser("rescore").set_defaults(func=cmd_rescore)
    gate = commands.add_parser("gate")
    gate.add_argument(
        "--baseline", type=Path, help="gate against this file instead of the baseline"
    )
    gate.set_defaults(func=cmd_gate)
    commands.add_parser("review-list").set_defaults(func=cmd_review_list)
    commands.add_parser("enrichment-report").set_defaults(func=cmd_enrichment_report)
    commands.add_parser("compare-retrievers").set_defaults(func=cmd_compare_retrievers)
    embed_cache = commands.add_parser("embed-cache")
    embed_cache.add_argument("--check", action="store_true", help="compare, write nothing")
    embed_cache.set_defaults(func=cmd_embed_cache)
    integrity = commands.add_parser("integrity")
    integrity.add_argument("--base", default="origin/main")
    integrity.set_defaults(func=cmd_integrity)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
