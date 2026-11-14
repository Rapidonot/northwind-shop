"""RAG-03: the retriever comparison, the drift fingerprint, and the eval path's region filter."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg
import pytest

from northwind_evals import cli
from northwind_evals.compare import (
    Comparison,
    compare,
    drift_lines,
    fingerprint,
    mean_scores,
    render,
    trap_appearances,
)
from northwind_evals.items import EvalItem, load_items
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("RAG-03")

REPO = Path(__file__).resolve().parents[2]


def item(item_id: str, source: str = "P1", **changes: Any) -> EvalItem:
    base = EvalItem(
        item_id, "specification", "q?", "US", "answer", ((source,),), ("f",), "why", None, "model"
    )
    return replace(base, **changes)


def means(recall5: float) -> dict[str, float]:
    return {"recall@5": recall5, "recall@10": 1.0, "mrr": 1.0}


# ---- the comparison ---------------------------------------------------------------------------


def test_hybrid_wins_only_when_strictly_above_both() -> None:
    assert Comparison(
        1, {"dense": means(0.5), "keyword": means(0.6), "hybrid": means(0.7)}
    ).hybrid_wins
    assert not Comparison(
        1, {"dense": means(0.5), "keyword": means(0.7), "hybrid": means(0.7)}
    ).hybrid_wins
    assert not Comparison(
        1, {"dense": means(0.8), "keyword": means(0.6), "hybrid": means(0.7)}
    ).hybrid_wins


def test_compare_scores_each_arm_on_the_same_items() -> None:
    items = [item("EV-1", "P1"), item("EV-2", "P2")]
    ranked = {
        "dense": {"EV-1": ["P1"], "EV-2": ["X"] * 10},
        "keyword": {"EV-1": ["X"] * 10, "EV-2": ["P2"]},
        "hybrid": {"EV-1": ["P1"], "EV-2": ["P2"]},
    }
    result = compare(items, ranked)
    assert result.n == 2
    assert result.means["dense"]["recall@5"] == 0.5
    assert result.means["keyword"]["recall@5"] == 0.5
    assert result.means["hybrid"]["recall@5"] == 1.0
    assert result.hybrid_wins


def test_compare_refuses_a_missing_arm() -> None:
    with pytest.raises(ValueError, match="keyword"):
        compare([item("EV-1")], {"dense": {"EV-1": []}, "hybrid": {"EV-1": []}})


def test_items_not_scored_for_retrieval_are_left_out() -> None:
    unscored = item("EV-9", expected_behaviour="refuse_unanswerable", relevant_sources=())
    scores = mean_scores([item("EV-1"), unscored], {"EV-1": ["P1"], "EV-9": []})
    assert scores["recall@5"] == 1.0


def test_trap_appearances_count_planted_reviews_in_each_top_5() -> None:
    ranked = {"EV-1": ["P1", "R9", "P2"], "EV-2": ["P1"] * 5 + ["R9"], "EV-3": ["R9", "R9", "R8"]}
    traps = {"R9": "TRAP-INJ-001", "R8": "TRAP-INJ-002"}
    assert trap_appearances(ranked, traps) == [
        ("EV-1", "TRAP-INJ-001"),
        ("EV-3", "TRAP-INJ-001"),
        ("EV-3", "TRAP-INJ-002"),
    ]


def test_the_report_shows_trap_appearances_and_the_dense_to_hybrid_change() -> None:
    items = [item("EV-1"), item("EV-2")]
    ranked = {
        "dense": {"EV-1": ["P1"], "EV-2": ["P1", "R9"]},
        "keyword": {"EV-1": ["R9", "P1"], "EV-2": ["P1"]},
        "hybrid": {"EV-1": ["P1", "R9"], "EV-2": ["P1"]},
    }
    result = compare(items, ranked, {"R9": "TRAP-INJ-001"})
    assert result.traps == {
        "dense": [("EV-2", "TRAP-INJ-001")],
        "keyword": [("EV-1", "TRAP-INJ-001")],
        "hybrid": [("EV-1", "TRAP-INJ-001")],
    }
    text = render(result, "r")
    assert "| hybrid | 1 | EV-1 TRAP-INJ-001 |" in text
    assert (
        "Dense to hybrid: +0. New in hybrid: EV-1 TRAP-INJ-001. Gone in hybrid: EV-2 TRAP-INJ-001."
        in text
    )


def test_traps_do_not_change_the_criterion() -> None:
    items = [item("EV-1")]
    ranked = {"dense": {"EV-1": ["X"]}, "keyword": {"EV-1": ["X"]}, "hybrid": {"EV-1": ["P1"]}}
    assert compare(items, ranked, {}).hybrid_wins == compare(items, ranked).hybrid_wins is True


def test_each_planted_review_is_one_chunk_so_both_trap_counts_agree() -> None:
    """`trap_appearances` counts per review, `enrichment_report` per chunk (security re-review)."""
    from collections import Counter

    from northwind_rag.sources import build_chunks

    planted = cli._trap_reviews()
    chunks = Counter(
        c.source_id for c in build_chunks(REPO / "data" / "fixtures") if c.source_id in planted
    )
    assert set(chunks) == set(planted) and set(chunks.values()) == {1}


def test_the_cli_reads_every_planted_review_from_the_answer_key() -> None:
    reviews = cli._trap_reviews()
    assert len(reviews) == 16  # TRAP-INJ-016, appended at DATA_VERSION 2 (row #13b)
    assert reviews["R00712"] == "TRAP-INJ-003" and reviews["R00970"] == "TRAP-INJ-002"
    assert reviews["R01221"] == "TRAP-INJ-016"


# ---- the drift fingerprint --------------------------------------------------------------------


def test_the_fingerprint_is_order_sensitive_and_key_order_free() -> None:
    a = {"EV-1": ["c1", "c2"], "EV-2": ["c3"]}
    assert fingerprint(a) == fingerprint({"EV-2": ["c3"], "EV-1": ["c1", "c2"]})
    assert fingerprint(a) != fingerprint({"EV-1": ["c2", "c1"], "EV-2": ["c3"]})
    assert len(fingerprint(a)) == 64


def test_drift_lines_print_six_places_and_the_hash() -> None:
    lines = drift_lines({"recall@5": 0.95442708333, "mrr": 0.5}, {"EV-1": ["c1"]})
    assert lines[:2] == ["retrieval mrr 0.500000", "retrieval recall@5 0.954427"]
    assert lines[2] == f"retrieval top-k sha256 {fingerprint({'EV-1': ['c1']})}"


# ---- the eval path ----------------------------------------------------------------------------


def test_live_retrieval_passes_each_items_region_as_a_shopper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any, Any]] = []

    def fake(
        conn: Any, embedder: Any, query: str, *, region: Any, audience: Any, k: int
    ) -> list[RetrievedChunk]:
        calls.append((query, region, audience))
        return []

    monkeypatch.setattr(cli, "retrieve_context", fake)
    items = [item("EV-1", query="a", region="UK"), item("EV-2", query="b", region="EU")]
    cli._live(None, None, items)  # type: ignore[arg-type]
    assert calls == [("a", "UK", "shopper"), ("b", "EU", "shopper")]


# ---- the criterion, on a real index -----------------------------------------------------------


@pytest.mark.integration
def test_hybrid_beats_dense_and_keyword_on_recall_at_5(
    conn_with_real_index: psycopg.Connection,
) -> None:
    """PRD RAG-03: beats dense-only and keyword-only on recall@5 in the evaluation harness."""
    from northwind_rag.embedding import FastEmbedEmbedder

    items = load_items(REPO / "evals" / "fixtures" / "starter_set.jsonl")
    result = compare(items, cli._arms(conn_with_real_index, FastEmbedEmbedder(), items))
    assert result.n == 212  # row #32: 148 new answer items
    assert result.hybrid_wins, result.means


@pytest.fixture
def conn_with_real_index() -> Any:
    """A throwaway schema with the fixtures embedded by the real model, as `make ingest` does."""
    import uuid

    from northwind_rag.cli import DEFAULT_DATABASE_URL
    from northwind_rag.embedding import CachedEmbedder, FastEmbedEmbedder
    from northwind_rag.ingest import ingest

    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"rag03_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    connection = psycopg.connect(url, autocommit=True, options=f"-csearch_path={schema},public")
    try:
        # Frozen chunk vectors, as `make ingest` writes them, so every platform ranks the same.
        ingest(connection, REPO / "data" / "fixtures", CachedEmbedder(FastEmbedEmbedder()))
        yield connection
    finally:
        connection.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_with_no_scored_items_the_gate_prints_only_the_hash_and_compare_refuses() -> None:
    unscored = item("EV-9", expected_behaviour="refuse_unanswerable", relevant_sources=())
    assert mean_scores([unscored], {"EV-9": []}) == {}
    assert drift_lines({}, {"EV-9": ["c1"]}) == [
        f"retrieval top-k sha256 {fingerprint({'EV-9': ['c1']})}"
    ]
    ranked: dict[str, dict[str, list[str]]] = {
        arm: {"EV-9": []} for arm in ("dense", "keyword", "hybrid")
    }
    with pytest.raises(ValueError, match="no retrieval-scored"):
        compare([unscored], ranked)
