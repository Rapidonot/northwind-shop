"""The eval CLI end to end, with no database, no embedder and no API (RAG-12).

Retrieval is faked at the `_live` seam. Answers come from `scripted_llm`, or from the real replay
proxy over an empty cassette directory (a cassette miss). Nothing here can spend: `run` is only
called without LLM_MODE=record, and every client is either scripted or replay-only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from northwind_common.llm import anthropic_client
from northwind_evals import cli
from northwind_evals.answerers import Answerer, SingleShotAnswerer
from northwind_evals.contexts import ContextChunk, SavedContext, save_contexts
from northwind_evals.gate import Baseline, save_baseline
from northwind_evals.items import MIN_HUMAN_REVIEWED, EvalItem
from northwind_evals.judge import Judge
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("RAG-12")

VERDICT = json.dumps(
    {
        "claims": [{"claim": "a", "supported": True}],
        "relevance": "full",
        "declined": False,
        "behaviour_ok": True,
    }
)


def item(item_id: str = "EV-1", **changes: Any) -> EvalItem:
    base = EvalItem(
        item_id, "specification", "q?", "US", "answer", (("P1",),), ("f",), "why", None, "model"
    )
    return replace(base, **changes)


def chunk_ids(n: int = 5) -> list[str]:
    return [f"product:P1:{i}" for i in range(n)]


def context_for(item_id: str, ids: list[str]) -> SavedContext:
    chunks = tuple(ContextChunk(c, "P1", "product", "text") for c in ids)
    return SavedContext(item_id, "dense:v1:x", "idx", chunks)


def live_for(ids: list[str], source: str = "P1") -> list[RetrievedChunk]:
    return [RetrievedChunk(c, "product", source, None, "text") for c in ids]


class FakeDb:
    def __enter__(self) -> FakeDb:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class Rig:
    """Points the CLI at a temp tree and fakes live retrieval."""

    def __init__(self, tmp: Path, mp: pytest.MonkeyPatch) -> None:
        self.tmp = tmp
        self.live: dict[str, list[RetrievedChunk]] = {}
        mp.setattr(cli, "ITEMS", tmp / "items.jsonl")
        mp.setattr(cli, "CONTEXTS", tmp / "contexts.jsonl")
        mp.setattr(cli, "BASELINE", tmp / "baseline.json")
        mp.setattr(cli, "REVIEW_LIST", tmp / "docs" / "review.md")
        mp.setattr(cli, "_connect", lambda: FakeDb())
        mp.setattr(cli, "_index_version", lambda conn: "idx")
        mp.setattr(cli, "FastEmbedEmbedder", lambda: object())
        mp.setattr(cli, "_live", lambda conn, embedder, items: self.live)

    def write_items(self, items: list[EvalItem]) -> None:
        lines = [
            {
                "id": i.id,
                "segment": i.segment,
                "query": i.query,
                "region": i.region,
                "expected_behaviour": i.expected_behaviour,
                "relevant_sources": [list(g) for g in i.relevant_sources],
                "reference_facts": list(i.reference_facts),
                "why": i.why,
                "trap_id": i.trap_id,
                "reviewed_by": i.reviewed_by,
            }
            for i in items
        ]
        cli.ITEMS.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")

    def write_baseline(self, retrieval: dict[str, Any], answers: dict[str, Any]) -> None:
        save_baseline(
            cli.BASELINE,
            Baseline("dense-only", "r", "idx", "a", "j", retrieval, answers, summary={}),
        )

    def setup_one_item(self, *, live_ids: list[str] | None = None) -> None:
        ids = chunk_ids()
        self.write_items([item()])
        save_contexts(cli.CONTEXTS, [context_for("EV-1", ids)])
        self.live = {"EV-1": live_for(live_ids if live_ids is not None else ids)}
        self.write_baseline(
            {"EV-1": {"recall@5": 1.0, "mrr": 1.0}},
            {"EV-1": {"groundedness": 1.0, "relevance": 1.0, "behaviour_ok": 1.0}},
        )


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    monkeypatch.delenv(cli.ALLOW_STALE_ENV, raising=False)
    return Rig(tmp_path, monkeypatch)


def scripted_clients(monkeypatch: pytest.MonkeyPatch, llm: ScriptedLLM) -> None:
    @contextmanager
    def clients(mode: str) -> Iterator[tuple[Answerer, Judge]]:
        assert mode == "replay"
        yield (
            SingleShotAnswerer(anthropic_client(llm.url), "claude-sonnet-5-5"),
            Judge(anthropic_client(llm.url), "claude-sonnet-5-5"),
        )

    monkeypatch.setattr(cli, "_clients", clients)


# --- gate exit codes -------------------------------------------------------------------------


def test_gate_passes_when_nothing_moved(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    with ScriptedLLM() as llm:
        llm.reply("Answer [P1].").reply(VERDICT)
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 0
        assert len(llm.requests) == 2  # one answer, one verdict
    assert "eval-gate passed" in capsys.readouterr().out


def test_gate_fails_when_a_retrieval_metric_drops_beyond_tolerance(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Live retrieval keeps the saved chunks but the wanted source falls out of the top 5."""
    rig.setup_one_item()
    # Same top-5 chunk IDs in the same order, so the context is not stale; the source IDs moved.
    rig.live["EV-1"] = [
        RetrievedChunk(c, "product", "OTHER" if n < 5 else "P1", None, "t")
        for n, c in enumerate(chunk_ids(10))
    ]
    with ScriptedLLM() as llm:
        llm.reply("Answer [P1].").reply(VERDICT)
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "eval-gate FAILED" in out


def test_gate_fails_when_an_answer_metric_drops_beyond_tolerance(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    bad = json.loads(VERDICT) | {"claims": [{"claim": "a", "supported": False}]}
    with ScriptedLLM() as llm:
        llm.reply("Answer [P1].").reply(json.dumps(bad))
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1
    assert "eval-gate FAILED" in capsys.readouterr().out


def test_gate_fails_on_a_false_refusal_of_an_answerable_item(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.setup_one_item()
    declined = json.loads(VERDICT) | {"declined": True}
    with ScriptedLLM() as llm:
        llm.reply("Sorry, not available.").reply(json.dumps(declined))
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1


def test_a_judge_error_fails_the_gate_even_when_stale_is_allowed(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    monkeypatch.setenv(cli.ALLOW_STALE_ENV, "1")
    with ScriptedLLM() as llm:
        llm.reply("Answer [P1].").reply("this is not json")
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1
    assert "EV-1" in capsys.readouterr().out


# --- stale versus cassette miss --------------------------------------------------------------


def test_a_stale_context_fails_the_gate_without_calling_the_model(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item(live_ids=[*chunk_ids(4), "product:P9:0"])
    with ScriptedLLM() as llm:
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1
        assert llm.requests == []  # stale is decided before any call, never a miss
    out = capsys.readouterr().out
    assert "STALE EV-1" in out
    assert "eval-gate FAILED" in out


def test_eval_allow_stale_one_lets_a_stale_item_pass(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item(live_ids=[*chunk_ids(4), "product:P9:0"])
    monkeypatch.setenv(cli.ALLOW_STALE_ENV, "1")
    with ScriptedLLM() as llm:
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 0
    out = capsys.readouterr().out
    assert "STALE EV-1" in out  # still reported
    assert "eval-gate passed" in out


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_only_the_exact_value_one_allows_stale(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    rig.setup_one_item(live_ids=[*chunk_ids(4), "product:P9:0"])
    monkeypatch.setenv(cli.ALLOW_STALE_ENV, value)
    with ScriptedLLM() as llm:
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1


def test_a_cassette_miss_is_reported_as_stale_not_as_an_error(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Live retrieval matches, but no recording exists for the request: the real replay proxy
    answers with a miss, which the gate must show as STALE (and fail), never pass."""
    rig.setup_one_item()
    monkeypatch.chdir(rig.tmp)  # `cassettes/` is relative: an empty tree, so every call misses
    monkeypatch.setenv("LLM_MODE", "replay")
    assert cli.main(["gate"]) == 1
    out = capsys.readouterr().out
    assert "STALE EV-1" in out
    assert "eval-gate FAILED" in out
    assert "EV-1:" not in out.replace("STALE EV-1", "")  # not also listed as an error


def test_a_cassette_miss_passes_only_with_allow_stale(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.setup_one_item()
    monkeypatch.chdir(rig.tmp)
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setenv(cli.ALLOW_STALE_ENV, "1")
    assert cli.main(["gate"]) == 0


def test_the_gate_compares_only_items_in_the_baseline(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 20-item baseline over a 2-item set: the unrecorded item is not replayed or stale."""
    rig.setup_one_item()
    rig.write_items([item(), item("EV-2")])
    save_contexts(cli.CONTEXTS, [context_for("EV-1", chunk_ids()), context_for("EV-2", ["x"])])
    rig.live["EV-2"] = live_for(["different"], source="P2")
    rig.write_baseline(
        {"EV-1": {"recall@5": 1.0, "mrr": 1.0}, "EV-2": {"recall@5": 0.0, "mrr": 0.0}},
        {"EV-1": {"groundedness": 1.0, "relevance": 1.0, "behaviour_ok": 1.0}},
    )
    with ScriptedLLM() as llm:
        llm.reply("Answer [P1].").reply(VERDICT)
        scripted_clients(monkeypatch, llm)
        code = cli.main(["gate"])
        assert len(llm.requests) == 2
    out = capsys.readouterr().out
    assert "EV-2" not in out.replace("answers replayed", "")
    assert code == 0


# --- run refuses to spend ---------------------------------------------------------------------


@pytest.mark.parametrize("mode", [None, "replay"])
def test_run_refuses_without_record_mode_before_touching_anything(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, mode: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise AssertionError("run must refuse before it loads, connects or builds a client")

    for name in ("load_items", "load_contexts", "_connect", "_clients"):
        monkeypatch.setattr(cli, name, boom)
    if mode is None:
        monkeypatch.delenv("LLM_MODE", raising=False)
    else:
        monkeypatch.setenv("LLM_MODE", mode)
    assert cli.main(["run", "--slice", "5"]) == 2
    assert "make eval-live" in capsys.readouterr().err


def test_run_refuses_under_ci_even_in_record_mode(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise AssertionError("run must refuse before it loads, connects or builds a client")

    for name in ("load_items", "load_contexts", "_connect", "_clients"):
        monkeypatch.setattr(cli, name, boom)
    monkeypatch.setenv("LLM_MODE", "record")
    monkeypatch.setenv("CI", "true")
    assert cli.main(["run", "--slice", "5"]) == 2
    assert "replay only" in capsys.readouterr().err


def test_run_needs_a_slice() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["run"])
    assert exit_info.value.code == 2


def test_gate_and_review_list_take_no_slice_and_unknown_commands_fail() -> None:
    with pytest.raises(SystemExit):
        cli.main(["spend-everything"])


# --- review list -----------------------------------------------------------------------------


def test_review_list_names_exactly_the_unreviewed_unanswerable_and_adversarial_items(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.write_items(
        [
            item("EV-1"),
            item("EV-2", segment="unanswerable", expected_behaviour="abstain", relevant_sources=()),
            item("EV-3", segment="adversarial", expected_behaviour="contain", relevant_sources=()),
            item(
                "EV-4",
                segment="adversarial",
                expected_behaviour="contain",
                relevant_sources=(),
                reviewed_by="human",
            ),
        ]
    )
    assert cli.main(["review-list"]) == 0
    text = cli.REVIEW_LIST.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| EV-")]
    assert [r.split(" | ")[0] for r in rows] == ["| EV-2", "| EV-3"]
    assert "2 waiting; 1 of 4 items are human-reviewed" in text
    assert f"at least {MIN_HUMAN_REVIEWED} needed" in text
    assert "2 items listed" in capsys.readouterr().out


def test_review_list_escapes_pipes_and_joins_alternatives(rig: Rig) -> None:
    rig.write_items(
        [
            item(
                "EV-2",
                segment="unanswerable",
                query="a | b?",
                expected_behaviour="abstain",
                relevant_sources=(("POL-US", "FAQ-US"), ("P1",)),
            )
        ]
    )
    cli.main(["review-list"])
    [row] = [x for x in cli.REVIEW_LIST.read_text().splitlines() if x.startswith("| EV-2")]
    assert "a \\| b?" in row
    assert "POL-US or FAQ-US, P1" in row


def test_review_list_with_everything_reviewed_lists_nobody(rig: Rig) -> None:
    rig.write_items([item("EV-1"), item("EV-2", reviewed_by="human")])
    assert cli.main(["review-list"]) == 0
    text = cli.REVIEW_LIST.read_text(encoding="utf-8")
    assert "0 waiting" in text
    assert not [x for x in text.splitlines() if x.startswith("| EV-")]


def test_review_list_never_changes_reviewed_by(rig: Rig) -> None:
    rig.write_items([item("EV-2", segment="unanswerable", expected_behaviour="abstain")])
    before = cli.ITEMS.read_bytes()
    cli.main(["review-list"])
    assert cli.ITEMS.read_bytes() == before


def test_the_committed_review_list_matches_the_committed_items() -> None:
    """`make eval-review-list` output must not drift from the items the user reviews."""
    from northwind_evals.items import awaiting_review, load_items

    repo = Path(__file__).resolve().parents[2]
    waiting = awaiting_review(load_items(repo / "evals/fixtures/starter_set.jsonl"))
    listed = (repo / "docs/reviews/RAG-12-eval-review.md").read_text(encoding="utf-8")
    rows = {
        x.split(" | ")[0].removeprefix("| ") for x in listed.splitlines() if x.startswith("| EV-")
    }
    assert rows == {i.id for i in waiting}
