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
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import psycopg

from northwind_common.llm import ASSISTANT_MODEL, anthropic_client
from northwind_evals.answerers import Answerer, SingleShotAnswerer
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
from northwind_evals.enrichment_report import build_report, render
from northwind_evals.gate import (
    Baseline,
    GateResult,
    compare,
    coverage_problems,
    load_baseline,
    load_known_failures,
    override_problems,
    save_baseline,
    score_drift,
    summary_of,
    summary_problems,
)
from northwind_evals.harness import RETRIEVAL_K, ItemResult, run_answers, score_retrieval
from northwind_evals.integrity import added_known_failures, integrity_problems, merge_base
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
from northwind_rag.cli import DEFAULT_DATABASE_URL, enriched_database_url
from northwind_rag.embedding import Embedder, FastEmbedEmbedder
from northwind_rag.enrichment import EnrichmentCache
from northwind_rag.ingest import require_enriched_database
from northwind_rag.retrieval import RetrievedChunk, dense_search, retriever_id
from northwind_rag.sources import build_chunks

ITEMS = Path("evals/fixtures/starter_set.jsonl")
CONTEXTS = Path("evals/contexts/dense-only.jsonl")
BASELINE = Path("evals/baselines/dense-only.json")
CANDIDATE = Path(".llm-proxy-log/baseline-candidate.json")
FIXTURES = Path("data/fixtures")
KNOWN_FAILURES = Path("evals/fixtures/known_failures.json")
LAST_RUN = Path(".llm-proxy-log/eval-last-run.json")
REVIEW_LIST = Path("docs/reviews/RAG-12-eval-review.md")
ENRICHMENT_REPORT = Path("docs/reviews/RAG-02-enrichment-report.md")
SUITE = "eval-answers"
ALLOW_STALE_ENV = "EVAL_ALLOW_STALE"


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


def _live(
    conn: psycopg.Connection, embedder: Embedder, items: Sequence[EvalItem]
) -> dict[str, list[RetrievedChunk]]:
    return {
        item.id: dense_search(conn, embedder, item.query, audience="shopper", k=RETRIEVAL_K)
        for item in items
    }


def _retrieval_scores(
    items: Sequence[EvalItem], live: dict[str, list[RetrievedChunk]]
) -> dict[str, dict[str, float]]:
    return {
        item.id: score_retrieval(item, [chunk.source_id for chunk in live[item.id]])
        for item in items
        if item.retrieval_scored
    }


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
def _clients(mode: str) -> Iterator[tuple[Answerer, Judge, ProxyState]]:
    """The answerer and the judge, each with its own client, both through the proxy."""
    config = proxy_config(
        mode,
        SUITE,
        cassette_root=Path("cassettes"),
        log_path=None if mode == "replay" else Path(f".llm-proxy-log/{SUITE}.jsonl"),
    )
    with RunningProxy(config) as running:
        answerer_client = anthropic_client(f"{running.url}/_t/{encode_test_id('eval-answerer')}")
        judge_client = anthropic_client(f"{running.url}/_t/{encode_test_id('eval-judge')}")
        answerer = SingleShotAnswerer(answerer_client, ASSISTANT_MODEL)
        yield answerer, Judge(judge_client), running.proxy.state


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


def _build_candidate(chosen_size: str, mode: str) -> int:
    """Scores `chosen_size` items through the proxy in `mode` and writes a baseline candidate."""
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
    with _clients(mode) as (answerer, judge, state):
        results = run_answers(chosen, contexts, answerer, judge, _progress)
    if state.scan_failures:
        print(f"not writing a candidate: scanner refused {state.scan_failures}", file=sys.stderr)
        return 1
    LAST_RUN.parent.mkdir(exist_ok=True)
    LAST_RUN.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")

    failed = [r.item_id for r in results if r.status != "ok"]
    if failed:
        print(f"not writing a candidate: {len(failed)} item(s) failed: {failed}", file=sys.stderr)
        return 1
    segments = {item.id: item.segment for item in items}
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
    try:
        return _build_candidate(args.slice, "record")
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
        _index_version(conn)
        live = _live(conn, embedder, items)

    recorded = [item for item in items if item.id in baseline.answers]
    stale = _stale_ids(recorded, contexts, live, retriever_id(embedder))
    fresh = [item for item in recorded if item.id not in stale]
    with _clients("replay") as (answerer, judge, _state):
        results = run_answers(fresh, contexts, answerer, judge)

    stale += [r.item_id for r in results if r.status == "stale"]
    errors = [f"{r.item_id}: {r.detail}" for r in results if r.status == "error"]
    answers = {r.item_id: r.scores for r in results if r.status == "ok"}
    segments = {item.id: item.segment for item in items}
    known = load_known_failures(KNOWN_FAILURES)
    scores = _retrieval_scores(items, live)
    result = compare(baseline, scores, answers, stale, errors, segments, known)
    result.problems += coverage_problems(baseline, items)
    result.problems += score_drift(baseline, answers)
    result.problems += summary_problems(baseline)
    result.problems += override_problems(baseline)
    if not answers:
        result.problems.append("no answers replayed, so nothing proves the recorded answers")

    _print_rows(result)
    print(f"  answers replayed {len(answers)}/{len(baseline.answers)}, stale {len(result.stale)}")
    stale_lines = [f"STALE {i}" for i in result.stale]
    missing = [f"{metric} is missing from the baseline" for metric in result.new_metrics]
    missing += [f"{metric} is missing from the current scores" for metric in result.lost_metrics]
    for line in [*result.containment, *missing, *result.problems, *result.errors, *stale_lines]:
        print(f"  {line}")
    allow_stale = os.environ.get(ALLOW_STALE_ENV) == "1"
    passed = result.passed(allow_stale)
    verdict = "passed" if passed else "FAILED"
    print(f"eval-gate {verdict}" + (f" with {len(result.stale)} stale" if result.stale else ""))
    return 0 if passed else 1


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


def cmd_integrity(args: argparse.Namespace) -> int:
    """Compares this branch with the base branch. Never spends and needs no database."""
    try:
        problems = integrity_problems(args.base, BASELINE)
        added = added_known_failures(merge_base(args.base), KNOWN_FAILURES)
    except RuntimeError as error:
        print(f"eval-integrity FAILED: {error}", file=sys.stderr)
        return 1
    for entry in added:  # one JSON line each, so text inside an entry cannot pose as output
        print(f"  known failure added: {json.dumps(entry, sort_keys=True)}")
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(f"eval-integrity {'FAILED' if problems else 'passed'} against {args.base}")
    return 1 if problems else 0


def _trap_chunks() -> dict[str, str]:
    """Every chunk of a planted injection review. Evals may read the answer key; ingest may not."""
    key = json.loads((FIXTURES / "_answer_key.json").read_text(encoding="utf-8"))
    traps = {entry["review_id"]: entry["trap_id"] for entry in key["injection_reviews"]}
    return {c.chunk_id: traps[c.source_id] for c in build_chunks(FIXTURES) if c.source_id in traps}


def cmd_enrichment_report(_: argparse.Namespace) -> int:
    """Compares the plain and the enriched index. Reads both and changes neither (RAG-02)."""
    items = load_items(ITEMS)
    embedder = FastEmbedEmbedder()
    with _connect() as conn:
        plain = _live(conn, embedder, items)
    try:
        enriched_conn = psycopg.connect(enriched_database_url())
    except psycopg.Error:
        print("no enriched index: run `make ingest-enriched` first", file=sys.stderr)
        return 1
    with enriched_conn as conn:
        require_enriched_database(conn)
        enriched = _live(conn, embedder, items)
    report = build_report(
        items,
        plain,
        enriched,
        load_contexts(CONTEXTS),
        retriever_id(embedder),
        load_baseline(BASELINE).retrieval,
        _trap_chunks(),
        EnrichmentCache().reason_counts(),
    )
    text = render(report)
    ENRICHMENT_REPORT.write_text(text, encoding="utf-8")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="northwind_evals.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("snapshot").set_defaults(func=cmd_snapshot)
    run = commands.add_parser("run")
    run.add_argument("--slice", required=True, help="a number of items, or `all`")
    run.set_defaults(func=cmd_run)
    commands.add_parser("rescore").set_defaults(func=cmd_rescore)
    gate = commands.add_parser("gate")
    gate.add_argument(
        "--baseline", type=Path, help="gate against this file instead of the baseline"
    )
    gate.set_defaults(func=cmd_gate)
    commands.add_parser("review-list").set_defaults(func=cmd_review_list)
    commands.add_parser("enrichment-report").set_defaults(func=cmd_enrichment_report)
    integrity = commands.add_parser("integrity")
    integrity.add_argument("--base", default="origin/main")
    integrity.set_defaults(func=cmd_integrity)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
