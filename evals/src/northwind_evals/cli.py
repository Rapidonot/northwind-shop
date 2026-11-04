"""`python -m northwind_evals.cli snapshot|run|gate|review-list` (RAG-12).

`run` is the only command that spends, and only with LLM_MODE=record (`make eval-live`), which CI
refuses. `gate` always replays.
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

from northwind_common import localenv
from northwind_common.llm import ASSISTANT_MODEL, JUDGE_MODEL, anthropic_client
from northwind_evals.answerers import Answerer, SingleShotAnswerer
from northwind_evals.contexts import (
    ANSWER_CONTEXT_K,
    ContextChunk,
    SavedContext,
    is_stale,
    load_contexts,
    save_contexts,
)
from northwind_evals.gate import Baseline, compare, load_baseline, save_baseline
from northwind_evals.harness import RETRIEVAL_K, ItemResult, run_answers, score_retrieval
from northwind_evals.items import (
    MIN_HUMAN_REVIEWED,
    EvalItem,
    awaiting_review,
    human_reviewed,
    load_items,
    slice_items,
)
from northwind_evals.judge import Judge
from northwind_evals.metrics import aggregate
from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.proxy import ProxyConfig, RunningProxy, encode_test_id
from northwind_rag.cli import DEFAULT_DATABASE_URL
from northwind_rag.embedding import Embedder, FastEmbedEmbedder
from northwind_rag.retrieval import RetrievedChunk, dense_search, retriever_id

ITEMS = Path("evals/fixtures/starter_set.jsonl")
CONTEXTS = Path("evals/contexts/dense-only.jsonl")
BASELINE = Path("evals/baselines/dense-only.json")
LAST_RUN = Path(".llm-proxy-log/eval-last-run.json")
REVIEW_LIST = Path("docs/reviews/RAG-12-eval-review.md")
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
) -> list[str]:
    return [
        item.id
        for item in items
        if is_stale(contexts.get(item.id), [chunk.chunk_id for chunk in live[item.id]])
    ]


@contextmanager
def _clients(mode: str) -> Iterator[tuple[Answerer, Judge]]:
    """The answerer and the judge, each with its own client, both through the proxy."""
    ledger = None
    if mode != "replay":
        ledger = Ledger(
            localenv.ledger_path(),
            budget_usd=float(os.environ.get("LLM_BUDGET_USD", "90")),
            run_budget_usd=float(os.environ.get("LLM_RUN_BUDGET_USD", "5.00")),
            run_id=os.environ.get("LLM_RUN_ID", f"evals-{os.getpid()}"),
            suite=SUITE,
        )
    config = ProxyConfig(
        mode=mode,
        suite=SUITE,
        cassette_root=Path("cassettes"),
        ledger=ledger,
        env_file=localenv.env_file(),
        log_path=None if mode == "replay" else Path(f".llm-proxy-log/{SUITE}.jsonl"),
    )
    with RunningProxy(config) as running:
        answerer_client = anthropic_client(f"{running.url}/_t/{encode_test_id('eval-answerer')}")
        judge_client = anthropic_client(f"{running.url}/_t/{encode_test_id('eval-judge')}")
        yield SingleShotAnswerer(answerer_client, ASSISTANT_MODEL), Judge(judge_client, JUDGE_MODEL)


def _print_summary(summary: dict[str, dict[str, float | None]]) -> None:
    for group, values in summary.items():
        shown = "  ".join(
            f"{name}={value:.3f}" if name != "n" else f"n={value}"
            for name, value in sorted(values.items())
            if value is not None
        )
        print(f"  {group:<14} {shown}")


def _progress(result: ItemResult) -> None:
    print(f"  {result.item_id} {result.status} {result.detail or ''}".rstrip(), flush=True)


def cmd_snapshot(_: argparse.Namespace) -> int:
    """Saves each item's top chunks as the input for answers. No LLM."""
    items = load_items(ITEMS)
    embedder = FastEmbedEmbedder()
    with _connect() as conn:
        version = _index_version(conn)
        live = _live(conn, embedder, items)
    contexts = [
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
    save_contexts(CONTEXTS, contexts)
    print(f"saved {len(contexts)} contexts to {CONTEXTS}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """SPENDS: records answers and verdicts for a slice and writes the baseline."""
    if os.environ.get("LLM_MODE") != "record":
        print("run spends API credit: use `make eval-live EVAL_SLICE=<n|all>`", file=sys.stderr)
        return 2
    if os.environ.get("CI"):
        print("CI runs evals in replay only (decision 0001)", file=sys.stderr)
        return 2
    items = load_items(ITEMS)
    contexts = load_contexts(CONTEXTS)
    embedder = FastEmbedEmbedder()
    with _connect() as conn:
        version = _index_version(conn)
        live = _live(conn, embedder, items)
    stale = _stale_ids(items, contexts, live)
    if stale:
        print(f"{len(stale)} saved contexts are stale, run `make eval-snapshot`: {stale[:5]}")
        return 1
    chosen = items if args.slice == "all" else slice_items(items, int(args.slice))
    with _clients("record") as (answerer, judge):
        results = run_answers(chosen, contexts, answerer, judge, _progress)
    LAST_RUN.parent.mkdir(exist_ok=True)
    LAST_RUN.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")

    failed = [r.item_id for r in results if r.status != "ok"]
    if failed:
        print(f"not writing a baseline: {len(failed)} item(s) failed: {failed}", file=sys.stderr)
        return 1
    segments = {item.id: item.segment for item in items}
    retrieval = _retrieval_scores(items, live)
    answers = {r.item_id: r.scores for r in results}
    summary = {"retrieval": aggregate(retrieval, segments), "answers": aggregate(answers, segments)}
    save_baseline(
        BASELINE,
        Baseline(
            name="dense-only",
            retriever_id=retriever_id(embedder),
            index_version=version,
            answerer_id=answerer.id,
            judge_id=judge.id,
            retrieval=retrieval,
            answers=answers,
            summary=summary,
        ),
    )
    print(f"baseline written to {BASELINE} ({len(answers)} answers, {len(retrieval)} retrieval)")
    _print_summary(summary["retrieval"])
    _print_summary(summary["answers"])
    return 0


def cmd_gate(_: argparse.Namespace) -> int:
    """Live retrieval metrics plus replayed answers against the baseline. Never spends."""
    items = load_items(ITEMS)
    contexts = load_contexts(CONTEXTS)
    baseline = load_baseline(BASELINE)
    with _connect() as conn:
        _index_version(conn)
        live = _live(conn, FastEmbedEmbedder(), items)

    recorded = [item for item in items if item.id in baseline.answers]
    stale = _stale_ids(recorded, contexts, live)
    fresh = [item for item in recorded if item.id not in stale]
    with _clients("replay") as (answerer, judge):
        results = run_answers(fresh, contexts, answerer, judge)

    stale += [r.item_id for r in results if r.status == "stale"]
    errors = [f"{r.item_id}: {r.detail}" for r in results if r.status == "error"]
    answers = {r.item_id: r.scores for r in results if r.status == "ok"}
    result = compare(baseline, _retrieval_scores(items, live), answers, stale, errors)

    for metric, before, now, ok in result.rows:
        print(f"  {'ok  ' if ok else 'FAIL'} {metric:<13} baseline {before:.3f}  current {now:.3f}")
    print(f"  answers replayed {len(answers)}/{len(baseline.answers)}, stale {len(result.stale)}")
    for line in [*result.problems, *result.errors, *(f"STALE {i}" for i in result.stale)]:
        print(f"  {line}")
    allow_stale = os.environ.get(ALLOW_STALE_ENV) == "1"
    passed = result.passed(allow_stale)
    print(f"eval-gate {'passed' if passed else 'FAILED'}")
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
        sources = ", ".join(item.relevant_sources) or "none"
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="northwind_evals.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("snapshot").set_defaults(func=cmd_snapshot)
    run = commands.add_parser("run")
    run.add_argument("--slice", required=True, help="a number of items, or `all`")
    run.set_defaults(func=cmd_run)
    commands.add_parser("gate").set_defaults(func=cmd_gate)
    commands.add_parser("review-list").set_defaults(func=cmd_review_list)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
