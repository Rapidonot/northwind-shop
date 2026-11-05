"""RAG-02: the report that compares the enriched index with the plain one, on small fixtures."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from northwind_evals.contexts import ANSWER_CONTEXT_K, ContextChunk, SavedContext
from northwind_evals.enrichment_report import Report, build_report, render
from northwind_evals.items import EvalItem
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("RAG-02")

REPO = Path(__file__).resolve().parents[2]
RETRIEVER = "dense:v1:fake"


def item(item_id: str, segment: str, source: str | None, behaviour: str = "answer") -> EvalItem:
    return EvalItem(
        id=item_id,
        segment=segment,
        query=f"query {item_id}",
        region="US",
        expected_behaviour=behaviour,
        relevant_sources=((source,),) if source else (),
        reference_facts=(),
        why="synthetic",
        trap_id=None,
        reviewed_by="model",
    )


def hit(source: str, kind: str = "product") -> RetrievedChunk:
    return RetrievedChunk(f"{kind}:{source}:0", kind, source, None, f"text of {source}")


def ranked(*sources: str) -> list[RetrievedChunk]:
    return [hit(s) for s in sources]


def saved(
    item_id: str, chunks: Sequence[RetrievedChunk], retriever: str = RETRIEVER
) -> SavedContext:
    return SavedContext(
        item_id,
        retriever,
        "v1",
        # An answer is made from the top five, so that is all a saved context holds.
        tuple(
            ContextChunk(c.chunk_id, c.source_id, c.source_type, c.text)
            for c in chunks[:ANSWER_CONTEXT_K]
        ),
    )


def baseline_for(*ids: str, value: float = 0.5) -> dict[str, dict[str, float]]:
    return {i: {"recall@5": value} for i in ids}


def make(
    items: Sequence[EvalItem],
    plain: Mapping[str, Sequence[RetrievedChunk]],
    enriched: Mapping[str, Sequence[RetrievedChunk]],
    contexts: Mapping[str, SavedContext] | None = None,
    traps: Mapping[str, str] | None = None,
    baseline: Mapping[str, Mapping[str, float]] | None = None,
) -> Report:
    return build_report(
        items,
        plain,
        enriched,
        contexts if contexts is not None else {i.id: saved(i.id, plain[i.id]) for i in items},
        RETRIEVER,
        baseline or {},
        traps or {},
    )


filler = ("F1", "F2", "F3", "F4", "F5")


def test_recall_delta_and_the_segment_table() -> None:
    items = [
        item("A", "specification", "S1"),
        item("B", "specification", "S2"),
        item("C", "policy", "S3"),
        item("D", "comparison", "S4"),
        item("U", "unanswerable", None, "abstain"),
    ]
    plain = {
        "A": ranked("S1", *filler[:4]),  # found both times
        "B": ranked(*filler),  # missed, then found
        "C": ranked(*filler),  # missed, then found
        "D": ranked("S4", *filler[:4]),  # found, then lost
        "U": ranked(*filler),
    }
    enriched = {
        "A": ranked("S1", *filler[:4]),
        "B": ranked("S2", *filler[:4]),
        "C": ranked("S3", *filler[:4]),
        "D": ranked(*filler),
        "U": ranked(*filler),
    }
    report = make(items, plain, enriched, baseline=baseline_for("A", "B", "C", "D", value=0.5))
    assert report.plain_recall == pytest.approx(0.5)  # unanswerable items are not scored
    assert report.enriched_recall == pytest.approx(0.75)
    assert report.delta == pytest.approx(0.25)
    assert report.baseline_recall == pytest.approx(0.5)
    assert report.by_segment == {
        "comparison": (1, 1.0, 0.0),
        "policy": (1, 0.0, 1.0),
        "specification": (2, 0.5, 1.0),
    }
    assert report.improved == ["B", "C"]
    assert report.worsened == ["D"]
    text = render(report)
    assert "delta +0.250" in text
    assert "| specification | 2 | 0.500 | 1.000 |" in text
    assert "| comparison | 1 | 1.000 | 0.000 |" in text
    assert "Improved: B, C" in text and "Worsened: D" in text


def test_an_enriched_index_that_changes_nothing_has_a_zero_delta() -> None:
    items = [item("A", "specification", "S1")]
    live = {"A": ranked("S1", *filler[:4])}
    report = make(items, live, live)
    assert report.delta == 0
    assert (report.improved, report.worsened) == ([], [])


def test_an_answer_goes_stale_only_when_ids_or_order_in_the_top_five_change() -> None:
    items = [item(i, "specification", "S1") for i in ("same", "order", "swap", "deep", "extra")]
    base = ranked("S1", "F1", "F2", "F3", "F4", "F5", "F6")
    plain = {i.id: base for i in items}
    enriched = {
        "same": base,
        "order": [base[1], base[0], *base[2:]],
        "swap": [*base[:4], hit("F9"), *base[5:]],
        "deep": [*base[:5], hit("F8"), hit("F7")],  # only the ranks after the fifth change
        "extra": [*base, hit("F10")],
    }
    report = make(items, plain, enriched)
    assert sorted(report.stale) == ["order", "swap"]


def test_an_item_with_no_saved_context_or_another_retriever_is_stale() -> None:
    items = [item("none", "specification", "S1"), item("other", "specification", "S1")]
    live = {i.id: ranked("S1", *filler[:4]) for i in items}
    contexts = {"other": saved("other", live["other"], retriever="dense:v9:other")}
    report = make(items, live, live, contexts=contexts)
    assert sorted(report.stale) == ["none", "other"]


def test_zero_tolerance_items_are_called_out_when_their_answers_go_stale() -> None:
    items = [
        item("spec", "specification", "S1"),
        item("adv", "adversarial", None, "contain"),
        item("una", "unanswerable", None, "abstain"),
        item("adv-ok", "adversarial", None, "contain"),
    ]
    base = ranked("S1", *filler[:4])
    plain = {i.id: base for i in items}
    swapped = [base[1], base[0], *base[2:]]
    enriched = {"spec": swapped, "adv": swapped, "una": swapped, "adv-ok": base}
    report = make(items, plain, enriched)
    assert sorted(report.stale) == ["adv", "spec", "una"]
    assert sorted(report.stale_zero_tolerance) == ["adv", "una"]
    text = render(report)
    assert "Adversarial or unanswerable: adv, una" in text


def test_the_report_names_no_zero_tolerance_item_when_none_changed() -> None:
    items = [item("adv", "adversarial", None, "contain")]
    live = {"adv": ranked(*filler)}
    text = render(make(items, live, live))
    assert "Adversarial or unanswerable: none" in text


# -- the planted chunks -------------------------------------------------------------------------

TRAP = {"review:R00002:0": "TRAP-INJ-001"}
PLANTED = hit("R00002", "review")


def trap_report(plain_items: Sequence[str], enriched_items: Sequence[str]) -> Report:
    ids = sorted({*plain_items, *enriched_items, "other"})
    items = [item(i, "adversarial", None, "contain") for i in ids]
    plain = {i: ranked(*filler) for i in ids}
    enriched = {i: ranked(*filler) for i in ids}
    for i in plain_items:
        plain[i] = [PLANTED, *ranked(*filler[:4])]
    for i in enriched_items:
        enriched[i] = [PLANTED, *ranked(*filler[:4])]
    return make(items, plain, enriched, traps=TRAP)


def cells_of(report: Report) -> list[str]:
    line = next(x for x in render(report).splitlines() if x.startswith("| TRAP-INJ-001 "))
    return [c.strip() for c in line.strip("|").split("|")]


def test_every_item_a_planted_chunk_enters_is_listed_and_rendered() -> None:
    report = trap_report(["EV-1"], ["EV-1", "EV-2", "EV-3"])
    [row] = report.traps
    assert (row.trap_id, row.chunk_id) == ("TRAP-INJ-001", "review:R00002:0")
    assert row.plain == ("EV-1",)
    assert row.enriched == ("EV-1", "EV-2", "EV-3")
    assert row.entered == ("EV-2", "EV-3")
    assert report.planted_entries == [
        ("TRAP-INJ-001", "review:R00002:0", "EV-2"),
        ("TRAP-INJ-001", "review:R00002:0", "EV-3"),
    ]
    cells = cells_of(report)
    assert cells[:2] == ["TRAP-INJ-001", "review:R00002:0"]
    assert cells[2:6] == ["EV-1", "EV-1, EV-2, EV-3", "EV-2, EV-3", "-"]


def test_a_planted_chunk_in_the_same_items_enters_nothing() -> None:
    report = trap_report(["EV-1", "EV-2"], ["EV-1", "EV-2"])
    assert report.planted_entries == []
    assert "Entered a top 5: none" in render(report)


def test_a_planted_chunk_in_fewer_items_enters_nothing_and_shows_what_it_left() -> None:
    report = trap_report(["EV-1", "EV-2", "EV-3"], ["EV-1"])
    [row] = report.traps
    assert row.left == ("EV-2", "EV-3")
    assert report.planted_entries == []
    assert cells_of(report)[5] == "EV-2, EV-3"


def test_a_planted_chunk_that_swaps_items_at_the_same_count_is_listed() -> None:
    """R00033 left EV-090 and entered EV-095: the count stayed at 2, so comparing counts missed it,
    and this test once pinned that as right."""
    report = trap_report(["EV-090", "EV-1"], ["EV-095", "EV-1"])
    assert report.planted_entries == [("TRAP-INJ-001", "review:R00002:0", "EV-095")]
    text = render(report)
    assert "Entered a top 5: 1" in text
    assert "- TRAP-INJ-001 review:R00002:0 entered EV-095" in text


def test_only_the_top_five_count_as_an_appearance() -> None:
    items = [item("A", "adversarial", None, "contain")]
    deep = [*ranked(*filler[:ANSWER_CONTEXT_K]), PLANTED]  # sixth
    shallow = [PLANTED, *ranked(*filler[:4])]
    report = make(items, {"A": deep}, {"A": shallow}, traps=TRAP)
    [row] = report.traps
    assert row.plain == ()
    assert row.enriched == ("A",)
    assert report.planted_entries == [("TRAP-INJ-001", "review:R00002:0", "A")]


def test_every_trap_gets_a_row_in_trap_order() -> None:
    traps = {
        "review:R2:0": "TRAP-INJ-002",
        "review:R1:0": "TRAP-INJ-001",
        "review:R3:0": "TRAP-INJ-001",
    }
    items = [item("A", "adversarial", None, "contain")]
    live = {"A": ranked(*filler)}
    report = make(items, live, live, traps=traps)
    assert [(r.trap_id, r.chunk_id) for r in report.traps] == [
        ("TRAP-INJ-001", "review:R1:0"),
        ("TRAP-INJ-001", "review:R3:0"),
        ("TRAP-INJ-002", "review:R2:0"),
    ]


def tree_state(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
    }


def test_building_and_rendering_a_report_writes_nothing_under_evals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = tree_state(REPO / "evals")
    assert before
    monkeypatch.chdir(tmp_path)
    report = trap_report(["EV-1"], ["EV-1", "EV-2"])
    render(report)
    assert tree_state(REPO / "evals") == before
    assert list(tmp_path.iterdir()) == []


def test_rejections_are_reported_by_reason() -> None:
    report = Report(0.5, 0.5, 0.5, rejections={"instruction": 2, "non-ascii": 1})
    text = render(report)
    assert "- instruction: 2" in text
    assert "- non-ascii: 1" in text


def test_all_three_metrics_are_reported_overall_and_per_segment() -> None:
    items = [item("A", "specification", "S1"), item("B", "policy", "S2")]
    plain = {"A": ranked("S1", *filler[:4]), "B": ranked(*filler)}
    enriched = {"A": ranked("S1", *filler[:4]), "B": ranked("S2", *filler[:4])}
    report = make(items, plain, enriched)
    assert report.metrics["recall@5"] == (0.5, 1.0)
    assert report.metrics["mrr"] == (0.5, 1.0)
    assert report.segment_metrics["policy"]["recall@10"] == (0.0, 1.0)
    text = render(report)
    assert "| Metric | Plain | Enriched | Delta |" in text
    assert "| policy | 1 | 0.000 | 1.000 | 0.000 | 1.000 | 0.000 | 1.000 |" in text


def test_the_report_states_its_own_limits() -> None:
    text = render(Report(0.5, 0.5, 0.5))
    assert "do not confirm the RAG-02 criterion" in text
    assert "no region filter" in text
    assert "TRAP-INJ reviews only" in text
