"""RAG-02 switch (row #8c): more E1 tests: the command's exit code and output, (b) and (c) edges.

Complements test_enrichment_e1.py. No database, model or network: the command's collaborators are
monkeypatched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

import psycopg
import pytest

from northwind_evals import cli
from northwind_evals.enrichment_report import e1_verdict, review_share
from northwind_evals.items import EvalItem
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("RAG-02")

INJ = {"review:RI1:0": "TRAP-INJ-001", "review:RI2:0": "TRAP-INJ-002"}
CON = {"review:RC1:0": "TRAP-CON-001", "review:RC2:0": "TRAP-CON-002"}
X, X2 = "review:RI1:0", "review:RI2:0"
Y, Y2 = "review:RC1:0", "review:RC2:0"

Live = Mapping[str, Sequence[RetrievedChunk]]


def chunk(chunk_id: str) -> RetrievedChunk:
    kind, source, _ = chunk_id.split(":")
    return RetrievedChunk(chunk_id, kind, source, None, f"text of {source}")


def top(*ids: str) -> list[RetrievedChunk]:
    filler = [f"product:F{i}:0" for i in range(1, 7)]
    rest = [f for f in filler if f not in ids]
    return [chunk(i) for i in (list(ids) + rest)[:6]]


def verdict(plain: Live, enriched: Live, segments: Mapping[str, str]) -> list[str]:
    return e1_verdict(plain, enriched, segments, INJ, CON)


# --- (b) and (a) edges ----------------------------------------------------------------------------


def test_b_fails_when_a_trap_leaves_one_item_and_enters_another_at_equal_counts() -> None:
    plain = {"A": top(X), "B": top()}
    enriched = {"A": top(), "B": top(X)}
    segs = {"A": "factual", "B": "factual"}
    failures = verdict(plain, enriched, segs)
    assert [f[:3] for f in failures] == ["(b)"]
    assert "TRAP-INJ-001" in failures[0] and " B" in failures[0]


def test_a_passes_but_b_fails_for_the_swap_so_only_b_is_reported() -> None:
    plain = {"A": top(X), "B": top()}
    enriched = {"A": top(), "B": top(X)}
    assert not any(f.startswith("(a)") for f in verdict(plain, enriched, {}))


def test_a_trap_leaving_everywhere_passes() -> None:
    plain = {"A": top(X), "B": top(X2)}
    enriched = {"A": top(), "B": top()}
    assert verdict(plain, enriched, {"A": "adversarial", "B": "unanswerable"}) == []


def test_b_names_each_new_entry_separately() -> None:
    plain = {"A": top(), "B": top()}
    enriched = {"A": top(X, X2), "B": top(X)}
    b = [f for f in verdict(plain, enriched, {}) if f.startswith("(b)")]
    assert len(b) == 3


# --- (c) edges ------------------------------------------------------------------------------------


def test_c_ignores_non_trap_chunks_entering() -> None:
    plain = {"A": top()}
    ids = ["review:NEW:0", "product:ZZ:0", "policy:P:0", "guide:G:0", "product:F1:0"]
    enriched = {"A": [chunk(i) for i in ids]}
    assert verdict(plain, enriched, {"A": "adversarial"}) == []


def test_c_does_not_see_a_trap_beyond_the_top_five() -> None:
    plain = {"A": top()}
    enriched = {"A": [chunk(f"product:F{i}:0") for i in range(1, 6)] + [chunk(Y)]}
    assert verdict(plain, enriched, {"A": "unanswerable"}) == []


def test_c_fails_per_chunk_when_two_traps_enter_one_item() -> None:
    failures = verdict({"A": top()}, {"A": top(X, Y)}, {"A": "adversarial"})
    c = [f for f in failures if f.startswith("(c)")]
    assert len(c) == 2
    assert any(Y in f for f in c) and any(X in f for f in c)


def test_c_refuses_an_item_missing_from_the_segment_map() -> None:
    """Tightened after the security review (low 2): an item (c) cannot check fails (c)."""
    failures = [f for f in verdict({"A": top()}, {"A": top(Y)}, {}) if f.startswith("(c)")]
    assert failures == ["(c) A has no segment, so (c) cannot be checked on it"]


# --- ordering and determinism ---------------------------------------------------------------------


def test_failure_strings_start_with_a_clause_and_are_deterministic() -> None:
    plain = {"A": top(), "B": top(), "C": top()}
    enriched = {"A": top(X, Y), "B": top(X2), "C": top(Y2)}
    segs = {"A": "adversarial", "B": "factual", "C": "unanswerable"}
    first = verdict(plain, enriched, segs)
    second = verdict(
        dict(reversed(list(plain.items()))), dict(reversed(list(enriched.items()))), segs
    )
    assert first == second
    assert first
    assert all(f[:3] in {"(a)", "(b)", "(c)"} for f in first)
    assert [f[:3] for f in first] == sorted(f[:3] for f in first)


# --- review_share on short lists ------------------------------------------------------------------


def test_review_share_of_nothing_is_zero() -> None:
    assert review_share({}) == 0.0
    assert review_share({"A": []}) == 0.0


def test_a_short_list_contributes_its_own_length() -> None:
    live = {"A": [chunk("review:R1:0"), chunk("product:P:0")]}
    assert review_share(live) == pytest.approx(0.5)


def test_short_and_full_lists_are_pooled_by_slot() -> None:
    live = {
        "A": [chunk("review:R1:0")],  # 1 slot, 1 review
        "B": [chunk(f"product:P{i}:0") for i in range(1, 6)],  # 5 slots, 0 review
    }
    assert review_share(live) == pytest.approx(1 / 6)


def test_review_share_ignores_slots_beyond_five() -> None:
    live = {"A": [chunk(f"product:P{i}:0") for i in range(1, 6)] + [chunk("review:R1:0")] * 3}
    assert review_share(live) == 0.0


# --- the command ----------------------------------------------------------------------------------


class FakeConn:
    def __enter__(self) -> FakeConn:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class FakeCache:
    def reason_counts(self) -> dict[str, int]:
        return {}


class FakeEmbedder:
    id = "fake-embedder"


def _item(item_id: str, segment: str) -> EvalItem:
    abstain = segment in ("adversarial", "unanswerable")
    return EvalItem(
        id=item_id,
        segment=segment,
        query=f"query {item_id}",
        region="US",
        expected_behaviour="abstain" if abstain else "answer",
        relevant_sources=() if abstain else (("S1",),),
        reference_facts=(),
        why="synthetic",
        trap_id=None,
        reviewed_by="model",
    )


@pytest.fixture
def run_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    out = tmp_path / "report.md"

    def run(
        plain: Live,
        enriched: Live,
        *,
        problems: Sequence[Sequence[str]] = ((), ()),
        enriched_reachable: bool = True,
        same_index: Sequence[tuple[str, str]] | None = None,
    ) -> int:
        items = [_item("A", "adversarial"), _item("B", "factual")]
        lives = iter([plain, enriched])
        problem_lists = iter([list(p) for p in problems])
        monkeypatch.setattr(cli, "ENRICHMENT_REPORT", out)
        monkeypatch.setattr(cli, "load_items", lambda _p: items)
        monkeypatch.setattr(cli, "FastEmbedEmbedder", FakeEmbedder)
        monkeypatch.setattr(cli, "_connect", lambda: FakeConn())

        def fake_connect(_url: str) -> FakeConn:
            if not enriched_reachable:
                raise psycopg.OperationalError("down")
            return FakeConn()

        monkeypatch.setattr(psycopg, "connect", fake_connect)
        monkeypatch.setattr(cli, "enriched_database_url", lambda: "postgresql://x/enriched")
        monkeypatch.setattr(cli, "require_enriched_database", lambda _c: None)

        def index_problems(_c: object, _e: str, side: str | None = None) -> list[str]:
            sides.append(side)
            return next(problem_lists, [])

        monkeypatch.setattr(cli, "_index_problems", index_problems)
        identities = iter(same_index or [("northwind", "p1"), ("northwind_enriched", "e1")])
        monkeypatch.setattr(cli, "_index_identity", lambda _c: next(identities))
        monkeypatch.setattr(cli, "_live", lambda _c, _e, _i: next(lives))
        monkeypatch.setattr(cli, "load_contexts", lambda _p: {})
        monkeypatch.setattr(cli, "load_baseline", lambda _p: type("B", (), {"retrieval": {}})())
        monkeypatch.setattr(cli, "retriever_id", lambda _e: "fake")
        monkeypatch.setattr(cli, "EnrichmentCache", FakeCache)
        monkeypatch.setattr(cli, "_trap_chunks", lambda: dict(INJ))
        monkeypatch.setattr(cli, "_contradiction_chunks", lambda: dict(CON))
        return int(cli.cmd_enrichment_report(None))  # type: ignore[arg-type]

    sides: list[str | None] = []
    run.out = out  # type: ignore[attr-defined]
    run.sides = sides  # type: ignore[attr-defined]
    return run


def test_the_command_exits_0_when_e1_passes(
    run_command: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    live = {"A": top(), "B": top()}
    assert run_command(live, live) == 0
    assert "E1: passed" in capsys.readouterr().out
    assert "E1: passed" in run_command.out.read_text(encoding="utf-8")


def test_the_command_exits_1_when_a_clause_fails_and_reports_it(
    run_command: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_command({"A": top(), "B": top()}, {"A": top(Y), "B": top()}) == 1
    printed = capsys.readouterr().out
    written = run_command.out.read_text(encoding="utf-8")
    for text in (printed, written):
        assert "E1: FAILED" in text
        assert Y in text


def test_the_command_exits_1_on_a_b_only_failure(run_command: Any) -> None:
    assert run_command({"A": top(X), "B": top()}, {"A": top(), "B": top(X)}) == 1


def test_the_command_exits_1_on_a_plain_index_problem_and_writes_no_report(
    run_command: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    live = {"A": top(), "B": top()}
    assert run_command(live, live, problems=(["chunk_count: stale"], [])) == 1
    assert "chunk_count: stale" in capsys.readouterr().err
    assert not run_command.out.exists()


def test_the_command_exits_1_on_an_enriched_index_problem(
    run_command: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    live = {"A": top(), "B": top()}
    assert run_command(live, live, problems=([], ["data_version: stale"])) == 1
    assert "data_version: stale" in capsys.readouterr().err
    assert not run_command.out.exists()


def test_the_command_exits_1_when_there_is_no_enriched_index(run_command: Any) -> None:
    live = {"A": top(), "B": top()}
    assert run_command(live, live, enriched_reachable=False) == 1
    assert not run_command.out.exists()


def test_the_command_exits_1_when_both_sides_are_the_same_index(
    run_command: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Security review (medium 1): an index compared with itself must not pass E1."""
    live = {"A": top(), "B": top()}
    same_db = [("northwind_enriched", "e1"), ("northwind_enriched", "e1")]
    assert run_command(live, live, same_index=same_db) == 1
    assert "both sides read the database 'northwind_enriched'" in capsys.readouterr().err
    assert not run_command.out.exists()


def test_the_command_checks_each_side_as_its_own_kind(run_command: Any) -> None:
    """Security review (medium 1): the plain side is checked as plain, the enriched as enriched."""
    live = {"A": top(), "B": top()}
    assert run_command(live, live) == 0
    assert run_command.sides == ["plain", "enriched"]
