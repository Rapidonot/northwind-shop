"""The eval CLI end to end, with no database, no embedder and no API (RAG-12).

Retrieval is faked at the `_live` seam. Answers come from `scripted_llm`, or from the real replay
proxy over an empty cassette directory (a cassette miss). Nothing here can spend: `run` is only
called without LLM_MODE=record, and every client is either scripted or replay-only.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from northwind_common.llm import anthropic_client
from northwind_evals import cli
from northwind_evals.answerers import Answerer
from northwind_evals.contexts import ContextChunk, SavedContext, load_contexts, save_contexts
from northwind_evals.gate import Baseline, compare, load_baseline, save_baseline, summary_of
from northwind_evals.items import MIN_HUMAN_REVIEWED, EvalItem, load_items
from northwind_evals.judge import JUDGE_VERSION, Judge
from northwind_evals.testing import MessagesAnswerer
from northwind_llm_replay.proxy import ProxyState
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_rag.retrieval import RetrievedChunk, retriever_id
from northwind_rag.testing import FakeEmbedder

_spec = importlib.util.spec_from_file_location(
    "promote_rig", Path(__file__).with_name("promote_rig.py")
)
assert _spec is not None and _spec.loader is not None
promote_rig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(promote_rig)

pytestmark = pytest.mark.req("RAG-12")

GOOD = {
    "groundedness": 1.0,
    "relevance": 1.0,
    "behaviour_ok": 1.0,
    "false_refusal": 0.0,
    "citation_ok": 1.0,
    "fabricated_ok": 1.0,  # row #13b: every item now reports it
}
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
    return SavedContext(item_id, retriever_id(FakeEmbedder("x")), "idx", chunks)


def live_for(ids: list[str], source: str = "P1") -> list[RetrievedChunk]:
    return [RetrievedChunk(c, "product", source, None, "text") for c in ids]


MESSAGES_ID = "messages-test-double:claude-sonnet-5-5"
JUDGE_ID = f"judge:v{JUDGE_VERSION}:claude-sonnet-5-5"


class FakeDb:
    def __enter__(self) -> FakeDb:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def complete_baseline(retrieval: dict[str, Any], answers: dict[str, Any]) -> Baseline:
    """A baseline as `promote` writes it: segments recorded, summary matching its scores."""
    known = {i.id: i.segment for i in load_items(cli.ITEMS)} if cli.ITEMS.exists() else {}
    segments = {i: known.get(i, "specification") for i in {*retrieval, *answers}}
    summary = summary_of(retrieval, answers, segments)
    # The IDs a scripted run reports, so F8 (row #13b) compares like with like.
    ids = (retriever_id(FakeEmbedder("x")), "idx", MESSAGES_ID, JUDGE_ID)
    return Baseline("dense-only", *ids, retrieval, answers, summary, segments)


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
        mp.setattr(cli, "FastEmbedEmbedder", lambda: FakeEmbedder("x"))
        mp.setattr(cli, "CANDIDATE", tmp / "candidate.json")
        mp.setattr(cli, "KNOWN_FAILURES", tmp / "known_failures.json")
        cli.KNOWN_FAILURES.write_text("[]", encoding="utf-8")
        mp.setattr(cli, "_live", lambda conn, embedder, items: self.live)

    def promote(self, *args: str) -> int:
        return int(promote_rig.run(self.tmp, *args))

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
        save_baseline(cli.BASELINE, complete_baseline(retrieval, answers))

    def setup_one_item(self, *, live_ids: list[str] | None = None) -> None:
        ids = chunk_ids()
        self.write_items([item()])
        save_contexts(cli.CONTEXTS, [context_for("EV-1", ids)])
        self.live = {"EV-1": live_for(live_ids if live_ids is not None else ids)}
        self.write_baseline({"EV-1": {"recall@5": 1.0, "mrr": 1.0}}, {"EV-1": dict(GOOD)})

    def setup_one_fresh_one_stale(self) -> None:
        """EV-1 replays and EV-2's context has moved, so the gate has answers to compare."""
        ids = chunk_ids()
        self.write_items([item(), item("EV-2")])
        save_contexts(cli.CONTEXTS, [context_for("EV-1", ids), context_for("EV-2", ids)])
        self.live = {"EV-1": live_for(ids), "EV-2": live_for([*chunk_ids(4), "product:P9:0"])}
        perfect = {"recall@5": 1.0, "mrr": 1.0}
        self.write_baseline(
            {"EV-1": perfect, "EV-2": perfect}, {"EV-1": dict(GOOD), "EV-2": dict(GOOD)}
        )


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    monkeypatch.delenv(cli.ALLOW_STALE_ENV, raising=False)
    return Rig(tmp_path, monkeypatch)


def scripted_clients(monkeypatch: pytest.MonkeyPatch, llm: ScriptedLLM) -> None:
    @contextmanager
    def clients(mode: str) -> Iterator[tuple[Answerer, Judge, ProxyState, None]]:
        assert mode == "replay"
        yield (
            MessagesAnswerer(anthropic_client(llm.url), "claude-sonnet-5-5"),
            Judge(anthropic_client(llm.url), "claude-sonnet-5-5"),
            ProxyState(),
            None,
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
    """EV-1 replays, so an error on EV-2 is the only thing left that can fail the gate."""
    rig.setup_one_fresh_one_stale()
    rig.live["EV-2"] = rig.live["EV-1"]
    monkeypatch.setenv(cli.ALLOW_STALE_ENV, "1")
    with ScriptedLLM() as llm:
        llm.reply("Answer [P1].").reply(VERDICT).reply("Answer [P1].").reply("this is not json")
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1
    out = capsys.readouterr().out
    assert "  EV-2: " in out
    assert "STALE" not in out  # a stale verdict is what the flag would have waived


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
    rig.setup_one_fresh_one_stale()
    monkeypatch.setenv(cli.ALLOW_STALE_ENV, "1")
    with ScriptedLLM() as llm:
        llm.reply("Answer [P1].").reply(VERDICT)
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 0
    out = capsys.readouterr().out
    assert "STALE EV-2" in out  # still reported
    assert "eval-gate passed" in out


@pytest.mark.parametrize(("value", "code"), [("0", 1), ("true", 1), ("yes", 1), ("", 1), ("1", 0)])
def test_only_the_exact_value_one_allows_stale(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, value: str, code: int
) -> None:
    """EV-1 replays, so only the flag decides: `1` passing shows the others fail because of it."""
    rig.setup_one_fresh_one_stale()
    monkeypatch.setenv(cli.ALLOW_STALE_ENV, value)
    with ScriptedLLM() as llm:
        llm.reply("Answer [P1].").reply(VERDICT)
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == code


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
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """EV-1 replays and EV-2 misses. Its context is unchanged, so only the proxy marks it stale."""
    rig.setup_one_fresh_one_stale()
    rig.live["EV-2"] = rig.live["EV-1"]
    miss = {"type": "error", "error": {"type": "invalid_request_error", "message": "cassette miss"}}
    for allowed, code in ((None, 1), ("1", 0)):
        if allowed:
            monkeypatch.setenv(cli.ALLOW_STALE_ENV, allowed)
        with ScriptedLLM() as llm:
            llm.reply("Answer [P1].").reply(VERDICT).reply_raw(400, json.dumps(miss))
            scripted_clients(monkeypatch, llm)
            assert cli.main(["gate"]) == code
        assert "STALE EV-2" in capsys.readouterr().out


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
        {"EV-1": dict(GOOD)},
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


# --- the baseline only changes through promote (security review, finding 1) ---------------------


def write_candidate(answers: dict[str, Any], retrieval: dict[str, Any] | None = None) -> None:
    scores = retrieval or {"EV-1": {"recall@5": 1.0, "mrr": 1.0}}
    save_baseline(cli.CANDIDATE, complete_baseline(scores, answers))


def test_promote_replaces_the_baseline_and_leaves_the_candidate(rig: Rig) -> None:
    rig.setup_one_item()
    write_candidate({"EV-1": {**GOOD, "relevance": 0.99}})
    assert rig.promote() == 0
    assert load_baseline(cli.BASELINE).answers["EV-1"]["relevance"] == 0.99
    assert cli.CANDIDATE.exists()  # row 5b: promote writes only the baseline


def test_promote_refuses_a_regression_and_keeps_the_baseline(rig: Rig) -> None:
    rig.setup_one_item()
    write_candidate({"EV-1": {**GOOD, "relevance": 0.0}})
    assert rig.promote() == 1
    assert load_baseline(cli.BASELINE).answers["EV-1"]["relevance"] == 1.0


def test_an_accepted_regression_needs_a_reason_and_is_recorded(rig: Rig) -> None:
    rig.setup_one_item()
    write_candidate({"EV-1": {**GOOD, "relevance": 0.0}})
    assert rig.promote("--accept-regression", "  ", "--approved-by", "the user") == 1
    assert rig.promote("--accept-regression", "agreed in review", "--approved-by", "the user") == 0
    baseline = load_baseline(cli.BASELINE)
    assert baseline.answers["EV-1"]["relevance"] == 0.0
    [override] = baseline.overrides
    assert override["reason"] == "agreed in review"
    assert override["accepted"] == ["relevance regressed"]


def test_overrides_survive_later_promotions(rig: Rig) -> None:
    rig.setup_one_item()
    write_candidate({"EV-1": {**GOOD, "relevance": 0.0}})
    assert rig.promote("--accept-regression", "first", "--approved-by", "the user") == 0
    write_candidate({"EV-1": {**GOOD, "relevance": 0.0}})
    assert rig.promote() == 0
    assert [o["reason"] for o in load_baseline(cli.BASELINE).overrides] == ["first"]


def test_a_containment_failure_cannot_be_promoted_even_with_a_reason(rig: Rig) -> None:
    rig.setup_one_item()
    rig.write_items([item(segment="adversarial", expected_behaviour="contain")])
    write_candidate({"EV-1": {**GOOD, "behaviour_ok": 0.0}})
    assert rig.promote("--accept-regression", "please", "--approved-by", "the user") == 1
    assert load_baseline(cli.BASELINE).answers["EV-1"]["behaviour_ok"] == 1.0


def test_promote_check_changes_nothing(rig: Rig) -> None:
    rig.setup_one_item()
    write_candidate({"EV-1": {**GOOD, "relevance": 0.99}})
    assert rig.promote("--check") == 0
    assert load_baseline(cli.BASELINE).answers["EV-1"]["relevance"] == 1.0
    assert cli.CANDIDATE.exists()


def test_rescore_never_builds_a_spending_client(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.setup_one_item()
    modes = []
    monkeypatch.setattr(cli, "_context_leaks", lambda contexts, items: [])
    monkeypatch.setattr(cli, "LAST_RUN", rig.tmp / "last.json")

    @contextmanager
    def clients(mode: str) -> Iterator[tuple[Answerer, Judge, ProxyState, None]]:
        modes.append(mode)
        with ScriptedLLM() as llm:
            llm.reply("Fine [P1].")
            llm.reply(VERDICT)
            answerer = MessagesAnswerer(anthropic_client(llm.url), "m")
            yield answerer, Judge(anthropic_client(llm.url), "m"), ProxyState(), None

    monkeypatch.setattr(cli, "_clients", clients)
    assert cli.main(["rescore"]) == 0
    assert modes == ["replay"]
    assert cli.CANDIDATE.exists()


def test_gate_can_be_pointed_at_another_baseline(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    write_candidate({})
    with ScriptedLLM() as llm:
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate", "--baseline", str(cli.CANDIDATE)]) == 1
    assert "the baseline has no answers" in capsys.readouterr().out


def test_promote_never_drops_a_gated_item_even_when_accepting_regressions(rig: Rig) -> None:
    rig.setup_one_item()
    rig.write_items([item(), item("EV-2")])
    rig.write_baseline({"EV-1": {"recall@5": 1.0, "mrr": 1.0}}, {"EV-1": GOOD, "EV-2": GOOD})
    write_candidate({"EV-1": GOOD})
    assert rig.promote("--accept-regression", "because", "--approved-by", "the user") == 1
    assert "EV-2" in load_baseline(cli.BASELINE).answers


def test_promote_needs_a_candidate(rig: Rig) -> None:
    rig.setup_one_item()
    assert rig.promote() == 1


@pytest.mark.parametrize("size", ["0", "-1", "ten", ""])
def test_run_refuses_a_slice_that_is_not_positive(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, size: str
) -> None:
    monkeypatch.setenv("LLM_MODE", "record")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(cli, "_clients", None)
    assert cli.main(["run", f"--slice={size}"]) == 2


def test_gate_fails_on_a_baseline_with_no_answers(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.setup_one_item()
    rig.write_baseline({"EV-1": {"recall@5": 1.0, "mrr": 1.0}}, {})
    with ScriptedLLM() as llm:
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1


def test_changed_chunk_text_is_stale_though_the_ids_match(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    rig.live["EV-1"][0] = RetrievedChunk(chunk_ids()[0], "product", "P1", None, "corrected text")
    with ScriptedLLM() as llm:
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1
    assert "STALE EV-1" in capsys.readouterr().out


def test_a_run_whose_cassette_the_scanner_refused_writes_no_candidate(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.setup_one_item()
    monkeypatch.setenv("LLM_MODE", "record")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(cli, "LAST_RUN", rig.tmp / "last.json")
    monkeypatch.setattr(cli, "_context_leaks", lambda contexts, items: [])

    @contextmanager
    def clients(mode: str) -> Iterator[tuple[Answerer, Judge, ProxyState, None]]:
        with ScriptedLLM() as llm:
            llm.reply("Fine [P1].")
            llm.reply(VERDICT)
            state = ProxyState(scan_failures=["cassette abc not written"])
            yield (
                MessagesAnswerer(anthropic_client(llm.url), "m"),
                Judge(anthropic_client(llm.url), "m"),
                state,
                None,
            )

    monkeypatch.setattr(cli, "_clients", clients)
    assert cli.main(["run", "--slice", "all"]) == 1
    assert not cli.CANDIDATE.exists()


@pytest.mark.parametrize(
    "unsafe",
    [
        {"query": "my card is 4242424242424242"},
        {"reference_facts": ("card 4242424242424242",)},
    ],
)
def test_run_refuses_card_data_in_an_item_before_any_client_exists(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, unsafe: dict[str, Any]
) -> None:
    rig.setup_one_item()
    rig.write_items([item(**unsafe)])
    monkeypatch.setenv("LLM_MODE", "record")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(cli, "_clients", None)
    assert cli.main(["run", "--slice", "all"]) == 1
    assert not cli.CANDIDATE.exists()


def test_run_refuses_card_data_or_an_internal_source_in_a_context(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.setup_one_item()
    monkeypatch.setenv("LLM_MODE", "record")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(cli, "_clients", None)
    chunk = ContextChunk("review:R1:0", "R1", "review", "email card 4242424242424242 now")
    save_contexts(cli.CONTEXTS, [SavedContext("EV-1", "r", "idx", (chunk,))])
    assert cli.main(["run", "--slice", "all"]) == 1
    internal = ContextChunk("variant:P0009-BLK-REG-L:0", "P0009-BLK-REG-L", "variant", "text")
    save_contexts(cli.CONTEXTS, [SavedContext("EV-1", "r", "idx", (internal,))])
    assert cli.main(["run", "--slice", "all"]) == 1


def test_the_committed_set_is_safe_to_send() -> None:
    items = load_items(cli.ITEMS)
    assert cli._context_leaks(load_contexts(cli.CONTEXTS), items) == []


def test_a_candidate_may_bring_in_a_new_metric(rig: Rig) -> None:
    rig.setup_one_item()
    old = {k: v for k, v in GOOD.items() if k != "citation_ok"}
    rig.write_baseline({"EV-1": {"recall@5": 1.0, "mrr": 1.0}}, {"EV-1": old})
    write_candidate({"EV-1": dict(GOOD)})
    assert rig.promote() == 0
    assert load_baseline(cli.BASELINE).answers["EV-1"]["citation_ok"] == 1.0
    assert load_baseline(cli.BASELINE).overrides == []


def test_a_candidate_may_not_lose_a_gated_metric(rig: Rig) -> None:
    rig.setup_one_item()
    write_candidate({"EV-1": {k: v for k, v in GOOD.items() if k != "citation_ok"}})
    assert rig.promote() == 1


# --- routes that would weaken the gate silently (decision 0003) ---------------------------------


def zero_tolerance_rig(rig: Rig) -> None:
    rig.setup_one_item()
    adversarial = item(segment="adversarial", expected_behaviour="contain")
    rig.write_items([adversarial])
    rig.write_baseline({"EV-1": {"recall@5": 1.0, "mrr": 1.0}}, {"EV-1": dict(GOOD)})


def test_relabelling_a_zero_tolerance_item_cannot_be_promoted(rig: Rig) -> None:
    zero_tolerance_rig(rig)
    rig.write_items([item(segment="specification")])
    write_candidate({"EV-1": dict(GOOD)})
    assert rig.promote("--accept-regression", "tidying labels", "--approved-by", "the user") == 1
    assert load_baseline(cli.BASELINE).segments["EV-1"] == "adversarial"


def test_deleting_a_zero_tolerance_item_cannot_be_promoted(rig: Rig) -> None:
    zero_tolerance_rig(rig)
    rig.write_items([item("EV-2")])
    write_candidate({"EV-2": dict(GOOD)}, {"EV-2": {"recall@5": 1.0, "mrr": 1.0}})
    assert rig.promote("--accept-regression", "tidying items", "--approved-by", "the user") == 1
    assert "EV-1" in load_baseline(cli.BASELINE).answers


def test_promoting_with_no_baseline_needs_a_recorded_reason(rig: Rig) -> None:
    rig.setup_one_item()
    write_candidate({"EV-1": dict(GOOD)})
    cli.BASELINE.unlink()
    assert rig.promote() == 1
    assert not cli.BASELINE.exists()
    assert rig.promote("--accept-regression", "no", "--approved-by", "the user") == 1
    assert rig.promote("--bootstrap", "  ", "--approved-by", "the user") == 1
    assert rig.promote("--bootstrap", "first baseline", "--approved-by", "the user") == 0
    assert load_baseline(cli.BASELINE).overrides[0]["reason"] == "first baseline"


def replayed_gate(monkeypatch: pytest.MonkeyPatch) -> int:
    with ScriptedLLM() as llm:
        llm.reply("Answer [P1].").reply(VERDICT)
        scripted_clients(monkeypatch, llm)
        return cli.main(["gate"])


def test_the_gate_passes_only_when_replay_equals_the_baseline(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    assert replayed_gate(monkeypatch) == 0
    rig.write_baseline(
        {"EV-1": {"recall@5": 1.0, "mrr": 1.0}}, {"EV-1": {**GOOD, "relevance": 0.99}}
    )
    assert replayed_gate(monkeypatch) == 1
    assert "replayed scores differ" in capsys.readouterr().out


def test_a_summary_that_does_not_match_its_scores_fails_the_gate(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    doctored = replace(load_baseline(cli.BASELINE), summary={"answers": {"overall": {"n": 99}}})
    save_baseline(cli.BASELINE, doctored)
    assert replayed_gate(monkeypatch) == 1
    assert "summary does not match" in capsys.readouterr().out


def test_a_baseline_without_segments_fails_the_gate(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    save_baseline(cli.BASELINE, replace(load_baseline(cli.BASELINE), segments={}))
    assert replayed_gate(monkeypatch) == 1
    assert "segment of every answer" in capsys.readouterr().out


@pytest.mark.parametrize("value", [0.5, None])
def test_a_doctored_behaviour_score_cannot_be_promoted(rig: Rig, value: float | None) -> None:
    rig.setup_one_item()
    scores: dict[str, Any] = {**GOOD, "behaviour_ok": value}
    if value is None:
        del scores["behaviour_ok"]
    write_candidate({"EV-1": scores})
    assert rig.promote("--accept-regression", "because", "--approved-by", "the user") == 1


def test_promote_recomputes_the_summary(rig: Rig) -> None:
    rig.setup_one_item()
    write_candidate({"EV-1": dict(GOOD)})
    doctored = replace(load_baseline(cli.CANDIDATE), summary={"answers": {"overall": {"n": 99}}})
    save_baseline(cli.CANDIDATE, doctored)
    assert rig.promote() == 0
    assert load_baseline(cli.BASELINE).summary["answers"]["overall"]["n"] == 1


# --- 5a: stale containment items are never waived, and a gate needs replayed answers -------------

MISS = json.dumps(
    {"type": "error", "error": {"type": "invalid_request_error", "message": "cassette miss"}}
)
SEGMENTS = {
    "adversarial": {"segment": "adversarial", "expected_behaviour": "contain"},
    "unanswerable": {"segment": "unanswerable", "expected_behaviour": "abstain"},
}


def zero_tolerance_stale_rig(rig: Rig, segment: str, route: str) -> ScriptedLLM:
    """EV-1 (ordinary) replays. EV-2 is of the given segment and goes stale by the given route.
    Returns an empty `ScriptedLLM` for the test to load."""
    rig.setup_one_fresh_one_stale()
    rig.write_items([item(), item("EV-2", relevant_sources=(), **SEGMENTS[segment])])
    # EV-2 is not retrieval-scored: a retrieval entry for it would fail the gate on its own, and the
    # test would pass with the fix deleted.
    rig.write_baseline(
        {"EV-1": {"recall@5": 1.0, "mrr": 1.0}},
        {"EV-1": dict(GOOD), "EV-2": dict(GOOD)},
    )
    if route == "miss":
        rig.live["EV-2"] = rig.live["EV-1"]  # context unchanged, so only the proxy marks it stale
    llm = ScriptedLLM()
    return llm


@pytest.mark.parametrize("human_known_failure", [False, True], ids=["listed-no", "human-approved"])
@pytest.mark.parametrize("route", ["context", "miss"])
@pytest.mark.parametrize("segment", ["adversarial", "unanswerable"])
def test_allow_stale_never_waives_a_stale_zero_tolerance_item(
    rig: Rig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    segment: str,
    route: str,
    human_known_failure: bool,
) -> None:
    llm = zero_tolerance_stale_rig(rig, segment, route)
    if human_known_failure:
        entry = {
            "item_id": "EV-2",
            "reason": "tolerated",
            "fix_item": "SA-09",
            "approved_by": "human",
            "approved_on": "2026-11-04",
        }
        cli.KNOWN_FAILURES.write_text(json.dumps([entry]), encoding="utf-8")
    monkeypatch.setenv(cli.ALLOW_STALE_ENV, "1")
    with llm:
        llm.reply("Answer [P1].").reply(VERDICT)  # EV-1 replays, so criterion 2 is not the cause
        if route == "miss":
            llm.reply_raw(400, MISS)
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1
    out = capsys.readouterr().out
    assert "EV-2: stale, so containment is unproven" in out
    assert "the retrieval items differ" not in out  # nothing else may be what failed the gate
    assert "eval-gate FAILED" in out


@pytest.mark.parametrize("segment", ["adversarial", "unanswerable"])
def test_a_stale_zero_tolerance_item_is_never_waived_by_compare(segment: str) -> None:
    """The rule on its own, with `passed(allow_stale=True)`, and with `segments` left out so only
    the baseline's record of the segment can catch it."""
    answers = {"EV-1": dict(GOOD), "EV-2": dict(GOOD)}
    retrieval = {"EV-1": {"recall@5": 1.0, "mrr": 1.0}}
    segments = {"EV-1": "specification", "EV-2": segment}
    summary = summary_of(retrieval, answers, segments)
    baseline = Baseline("b", "r", "i", "a", "j", retrieval, answers, summary, segments)
    result = compare(baseline, retrieval, {"EV-1": dict(GOOD)}, ["EV-2"], [])
    assert result.containment == ["EV-2: stale, so containment is unproven"]
    assert not result.passed(allow_stale=True)


def test_a_stale_ordinary_item_next_to_a_replayed_one_still_passes_with_the_flag(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the tests above: the flag still works for the segments it may waive."""
    rig.setup_one_fresh_one_stale()
    monkeypatch.setenv(cli.ALLOW_STALE_ENV, "1")
    assert replayed_gate(monkeypatch) == 0


def test_the_gate_fails_when_every_item_is_stale_even_with_the_flag(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item(live_ids=[*chunk_ids(4), "product:P9:0"])
    monkeypatch.setenv(cli.ALLOW_STALE_ENV, "1")
    with ScriptedLLM() as llm:
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1
    out = capsys.readouterr().out
    assert "no answers replayed" in out
    assert "eval-gate FAILED" in out


def test_the_gate_fails_when_every_call_misses_even_with_the_flag(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real replay proxy over an empty cassette tree."""
    rig.setup_one_item()
    monkeypatch.chdir(rig.tmp)
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setenv(cli.ALLOW_STALE_ENV, "1")
    assert cli.main(["gate"]) == 1
    out = capsys.readouterr().out
    assert "STALE EV-1" in out
    assert "no answers replayed" in out


# --- 5a: overrides name an approver -----------------------------------------------------------


def regressed_candidate(rig: Rig) -> None:
    rig.setup_one_item()
    write_candidate({"EV-1": {**GOOD, "relevance": 0.0}})


@pytest.mark.parametrize("flag", ["--accept-regression", "--bootstrap"])
@pytest.mark.parametrize("check", [False, True], ids=["write", "check"])
@pytest.mark.parametrize("approver", [None, "", "   "], ids=["missing", "empty", "blank"])
def test_an_override_without_an_approver_is_refused_and_writes_nothing(
    rig: Rig, flag: str, check: bool, approver: str | None
) -> None:
    regressed_candidate(rig)
    if flag == "--bootstrap":
        cli.BASELINE.unlink()
    before = cli.BASELINE.read_bytes() if cli.BASELINE.exists() else None
    argv = [flag, "agreed in review"]
    if approver is not None:
        argv += ["--approved-by", approver]
    if check:
        argv.append("--check")
    assert rig.promote(*argv) == 1
    assert cli.CANDIDATE.exists()
    after = cli.BASELINE.read_bytes() if cli.BASELINE.exists() else None
    assert after == before


@pytest.mark.parametrize("flag", ["--accept-regression", "--bootstrap"])
def test_a_promoted_override_stores_the_approver_literally(rig: Rig, flag: str) -> None:
    regressed_candidate(rig)
    if flag == "--bootstrap":
        cli.BASELINE.unlink()
    assert rig.promote(flag, "agreed", "--approved-by", "  Dana Test  ") == 0
    [override] = load_baseline(cli.BASELINE).overrides
    assert override["approved_by"] == "  Dana Test  "  # row 5b: taken literally
    assert override["reason"] == "agreed"


def with_override(override: dict[str, Any]) -> None:
    save_baseline(cli.BASELINE, replace(load_baseline(cli.BASELINE), overrides=[override]))


GOOD_OVERRIDE = {"on": "2026-11-04", "reason": "r", "accepted": ["x"], "approved_by": "Dana"}
NO_APPROVER = {"on": "2026-11-04", "reason": "r", "accepted": ["x"]}


@pytest.mark.parametrize("bad", ["missing", "", "   ", 7, None, ["Dana"]], ids=repr)
def test_the_gate_fails_on_an_override_with_no_valid_approver(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: Any
) -> None:
    rig.setup_one_item()
    override = dict(NO_APPROVER) if bad == "missing" else {**NO_APPROVER, "approved_by": bad}
    with_override(override)
    assert replayed_gate(monkeypatch) == 1
    out = capsys.readouterr().out
    assert "approved_by" in out or "approver" in out
    assert "eval-gate FAILED" in out


def test_the_gate_passes_with_an_override_that_names_its_approver(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.setup_one_item()
    with_override(dict(GOOD_OVERRIDE))
    assert replayed_gate(monkeypatch) == 0


@pytest.mark.parametrize("bad", [None, "", "   ", 7], ids=["null", "empty", "blank", "number"])
def test_promote_refuses_when_the_baseline_holds_an_unapproved_override(rig: Rig, bad: Any) -> None:
    rig.setup_one_item()
    with_override({**NO_APPROVER, "approved_by": bad})
    write_candidate({"EV-1": dict(GOOD)})
    before = cli.BASELINE.read_bytes()
    assert rig.promote() == 1
    assert cli.BASELINE.read_bytes() == before
    assert cli.CANDIDATE.exists()


def test_promote_refuses_when_the_baseline_override_has_no_approved_by_key(rig: Rig) -> None:
    rig.setup_one_item()
    with_override(dict(NO_APPROVER))
    write_candidate({"EV-1": dict(GOOD)})
    assert rig.promote() == 1


def test_promote_still_works_over_an_override_that_names_its_approver(rig: Rig) -> None:
    rig.setup_one_item()
    with_override(dict(GOOD_OVERRIDE))
    write_candidate({"EV-1": dict(GOOD)})
    assert rig.promote() == 0


@pytest.mark.req("RAG-03")
def test_gate_prints_exact_retrieval_metrics_and_the_top_k_hash(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The drift check (row #8): CI's log and the local run must show the same lines."""
    from northwind_evals.compare import fingerprint

    rig.setup_one_item()
    with ScriptedLLM() as llm:
        llm.reply("Answer [P1].").reply(VERDICT)
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 0
    out = capsys.readouterr().out
    expected = fingerprint({k: [c.chunk_id for c in v] for k, v in rig.live.items()})
    assert f"  retrieval top-k sha256 {expected}\n" in out
    assert "  retrieval recall@5 1.000000\n" in out


# ---- the stale line names what moved (RAG-03, the PR #30 drift) ------------------------------

SENTINEL = "SENTINEL-CHUNK-TEXT"  # chunk text: never printed, a stale line carries IDs only


def stale_lines(out: str) -> list[str]:
    return [line.strip() for line in out.splitlines() if line.strip().startswith("STALE ")]


@pytest.mark.req("RAG-03")
def test_a_moved_context_names_the_saved_and_the_live_ids(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    moved = [*chunk_ids(4), "product:P9:0"]
    rig.live = {"EV-1": [RetrievedChunk(c, "product", "P1", None, SENTINEL) for c in moved]}
    with ScriptedLLM() as llm:
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1
    out = capsys.readouterr().out
    assert stale_lines(out) == [f"STALE EV-1 saved {','.join(chunk_ids())} live {','.join(moved)}"]
    assert SENTINEL not in out


@pytest.mark.req("RAG-03")
def test_a_missing_context_says_so(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    save_contexts(cli.CONTEXTS, [context_for("EV-9", chunk_ids())])  # every context but EV-1's
    with ScriptedLLM() as llm:
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1
    assert stale_lines(capsys.readouterr().out) == ["STALE EV-1 no saved context"]


@pytest.mark.req("RAG-03")
def test_a_context_from_another_retriever_names_both(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    chunks = tuple(ContextChunk(c, "P1", "product", "text") for c in chunk_ids())
    save_contexts(cli.CONTEXTS, [SavedContext("EV-1", "other:retriever", "idx", chunks)])
    with ScriptedLLM() as llm:
        scripted_clients(monkeypatch, llm)
        assert cli.main(["gate"]) == 1
    current = retriever_id(FakeEmbedder("x"))
    assert stale_lines(capsys.readouterr().out) == [
        f"STALE EV-1 retriever other:retriever != {current}"
    ]


@pytest.mark.req("RAG-03")
def test_a_replay_miss_says_so(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.setup_one_item()
    monkeypatch.chdir(rig.tmp)  # an empty `cassettes/`, so the call misses
    monkeypatch.setenv("LLM_MODE", "replay")
    assert cli.main(["gate"]) == 1
    assert stale_lines(capsys.readouterr().out) == ["STALE EV-1 replay miss"]
