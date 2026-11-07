"""RAG-03: `cmd_compare_retrievers` exit codes and `_arms` region use, with faked retrievers."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from northwind_evals import cli
from northwind_evals.items import EvalItem
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("RAG-03")


def item(item_id: str, source: str, region: str = "US", **changes: Any) -> EvalItem:
    base = EvalItem(
        item_id, "specification", f"q {item_id}", region, "answer", ((source,),), ("f",), "why",
        None, "model",
    )  # fmt: skip
    return replace(base, **changes)


def chunk(source: str) -> RetrievedChunk:
    return RetrievedChunk(f"{source}#0", "product", source, None, "t")


def test_arms_run_all_three_with_each_items_region_as_a_shopper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, Any, Any]] = []

    def dense(conn: Any, emb: Any, query: str, *, audience: Any, k: int, region: Any) -> Any:
        calls.append(("dense", query, region, audience))
        return [chunk("P1")]

    def keyword(conn: Any, query: str, *, audience: Any, k: int, region: Any) -> Any:
        calls.append(("keyword", query, region, audience))
        return [chunk("P2")]

    def hybrid(conn: Any, emb: Any, query: str, *, region: Any, audience: Any, k: int) -> Any:
        calls.append(("hybrid", query, region, audience))
        return [chunk("P3")]

    monkeypatch.setattr(cli, "dense_search", dense)
    monkeypatch.setattr(cli, "keyword_search", keyword)
    monkeypatch.setattr(cli, "retrieve_context", hybrid)
    items = [item("EV-1", "P1", "UK"), item("EV-2", "P1", "EU")]
    arms = cli._arms(None, None, items)  # type: ignore[arg-type]
    assert set(arms) == {"dense", "keyword", "hybrid"}
    assert arms["dense"] == {"EV-1": ["P1"], "EV-2": ["P1"]}
    assert arms["keyword"]["EV-1"] == ["P2"]
    assert arms["hybrid"]["EV-2"] == ["P3"]
    for arm in ("dense", "keyword", "hybrid"):
        assert [c[2:] for c in calls if c[0] == arm] == [("UK", "shopper"), ("EU", "shopper")]


@pytest.fixture
def faked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Fake the DB, the embedder and the arms; return a setter for the arms' output."""

    class Conn:
        def __enter__(self) -> Conn:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    class Emb:
        id = "fake"

    items = [item("EV-1", "P1"), item("EV-2", "P2")]
    monkeypatch.setattr(cli, "load_items", lambda _path: items)
    monkeypatch.setattr(cli, "FastEmbedEmbedder", Emb)
    monkeypatch.setattr(cli, "_connect", lambda: Conn())
    monkeypatch.setattr(cli, "_index_version", lambda _c: "v1")
    monkeypatch.setattr(cli, "RETRIEVAL_REPORT", tmp_path / "report.md")

    def set_arms(arms: dict[str, dict[str, list[str]]]) -> None:
        monkeypatch.setattr(cli, "_arms", lambda *_a: arms)

    return set_arms


MISS = ["X"] * 10


def test_compare_retrievers_exits_0_when_hybrid_is_strictly_above_both(
    faked: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    faked(
        {
            "dense": {"EV-1": ["P1"], "EV-2": MISS},
            "keyword": {"EV-1": MISS, "EV-2": ["P2"]},
            "hybrid": {"EV-1": ["P1"], "EV-2": ["P2"]},
        }
    )
    assert cli.cmd_compare_retrievers(argparse.Namespace()) == 0
    assert "RAG-03" in capsys.readouterr().out


def test_compare_retrievers_exits_1_on_a_tie_with_one_arm(faked: Any) -> None:
    faked(
        {
            "dense": {"EV-1": ["P1"], "EV-2": ["P2"]},
            "keyword": {"EV-1": MISS, "EV-2": MISS},
            "hybrid": {"EV-1": ["P1"], "EV-2": ["P2"]},
        }
    )
    assert cli.cmd_compare_retrievers(argparse.Namespace()) == 1


def test_compare_retrievers_exits_1_when_hybrid_loses(faked: Any) -> None:
    faked(
        {
            "dense": {"EV-1": ["P1"], "EV-2": ["P2"]},
            "keyword": {"EV-1": ["P1"], "EV-2": ["P2"]},
            "hybrid": {"EV-1": MISS, "EV-2": MISS},
        }
    )
    assert cli.cmd_compare_retrievers(argparse.Namespace()) == 1


def test_compare_retrievers_writes_the_report_either_way(faked: Any) -> None:
    faked({a: {"EV-1": MISS, "EV-2": MISS} for a in ("dense", "keyword", "hybrid")})
    assert cli.cmd_compare_retrievers(argparse.Namespace()) == 1
    assert cli.RETRIEVAL_REPORT.read_text(encoding="utf-8").startswith("# RAG-03")
