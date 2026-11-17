"""Row #32 (plan §5): variance runs 2 and 3, and the judge repeat, are side runs.

A side run records into its own suite and writes only `.llm-proxy-log/<suite>-results.json`.
It never writes `evals/contexts/refinements.ndjson`, the baseline candidate or LAST_RUN, so
run 1 stays what the gate replays and what the judge repeat replays from (advisor H3).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from northwind_common.llm import anthropic_client
from northwind_evals import cli
from northwind_evals.contexts import ContextChunk, SavedContext, save_contexts
from northwind_evals.judge import Judge
from northwind_evals.testing import MessagesAnswerer
from northwind_llm_replay.proxy import ProxyState
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_rag.retrieval import RetrievedChunk, retriever_id
from northwind_rag.testing import FakeEmbedder

pytestmark = pytest.mark.req("RAG-12")

REPO = Path(__file__).resolve().parents[2]
VERDICT = json.dumps(
    {
        "claims": [{"claim": "a", "supported": True, "kind": "product"}],
        "relevance": "full",
        "declined": False,
        "behaviour_ok": True,
    }
)
IDS = [f"product:P1:{i}" for i in range(5)]


class FakeDb:
    def __enter__(self) -> FakeDb:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class SideRig:
    """The CLI pointed at a temp tree, with every file a side run must not write pre-filled."""

    def __init__(self, tmp: Path, mp: pytest.MonkeyPatch) -> None:
        self.tmp = tmp
        self.calls: list[dict[str, Any]] = []
        mp.setattr(cli, "SIDE_RESULTS_DIR", tmp / "log")
        for name in ("ITEMS", "CONTEXTS", "CANDIDATE", "LAST_RUN", "REFINEMENTS"):
            mp.setattr(cli, name, tmp / f"{name.lower()}.file")
        mp.setattr(cli, "_connect", lambda: FakeDb())
        mp.setattr(cli, "_index_version", lambda conn: "idx")
        mp.setattr(cli, "FastEmbedEmbedder", lambda: FakeEmbedder("x"))
        live = {"EV-1": [RetrievedChunk(c, "product", "P1", None, "text") for c in IDS]}
        mp.setattr(cli, "_live", lambda conn, embedder, items: live)
        line = {
            "id": "EV-1",
            "segment": "specification",
            "query": "q?",
            "region": "US",
            "expected_behaviour": "answer",
            "relevant_sources": [["P1"]],
            "reference_facts": ["f"],
            "why": "w",
            "trap_id": None,
            "reviewed_by": "model",
        }
        cli.ITEMS.write_text(json.dumps(line) + "\n", encoding="utf-8")
        chunks = tuple(ContextChunk(c, "P1", "product", "text") for c in IDS)
        save_contexts(
            cli.CONTEXTS, [SavedContext("EV-1", retriever_id(FakeEmbedder("x")), "idx", chunks)]
        )
        for path in (cli.CANDIDATE, cli.LAST_RUN):
            path.write_bytes(b"run 1's bytes\n")
        # A real recorded refinement for another item, so a rewrite would show (advisor H3).
        first = (
            (REPO / "evals/contexts/refinements.ndjson").read_bytes().splitlines(keepends=True)[0]
        )
        cli.REFINEMENTS.write_bytes(first)
        self.before = {p: p.read_bytes() for p in (cli.CANDIDATE, cli.LAST_RUN, cli.REFINEMENTS)}

    def stub_clients(self, mp: pytest.MonkeyPatch, llm: ScriptedLLM) -> None:
        calls = self.calls

        @contextmanager
        def clients(
            mode: str, suite: str = cli.SUITE, judge_suite: str | None = None
        ) -> Iterator[tuple[Any, Judge, ProxyState, None]]:
            calls.append({"mode": mode, "suite": suite, "judge_suite": judge_suite})
            yield (
                MessagesAnswerer(anthropic_client(llm.url), "claude-sonnet-5-5"),
                Judge(anthropic_client(llm.url), "claude-sonnet-5-5"),
                ProxyState(),
                None,
            )

        mp.setattr(cli, "_clients", clients)

    def untouched(self) -> bool:
        return all(path.read_bytes() == data for path, data in self.before.items())


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SideRig:
    monkeypatch.setenv("LLM_MODE", "record")
    monkeypatch.delenv("CI", raising=False)
    return SideRig(tmp_path, monkeypatch)


@pytest.mark.parametrize("suite", ["eval-answers-r2", "eval-answers-r3"])
def test_a_variance_run_records_into_its_own_suite_and_writes_only_its_results(
    rig: SideRig, monkeypatch: pytest.MonkeyPatch, suite: str
) -> None:
    with ScriptedLLM() as llm:
        llm.reply("An answer [P1].")
        llm.reply(VERDICT)
        rig.stub_clients(monkeypatch, llm)
        assert cli.main(["run", "--slice", "all", "--suite", suite]) == 0
    assert rig.calls == [{"mode": "record", "suite": suite, "judge_suite": None}]
    assert rig.untouched()
    results = json.loads(cli.side_results(suite).read_text(encoding="utf-8"))
    assert results["suite"] == suite
    assert [r["item_id"] for r in results["results"]] == ["EV-1"]


def test_the_judge_repeat_replays_answers_and_records_the_judge_apart(
    rig: SideRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    with ScriptedLLM() as llm:
        llm.reply("An answer [P1].")
        llm.reply(VERDICT)
        rig.stub_clients(monkeypatch, llm)
        assert cli.main(["run", "--slice", "all", "--suite", "eval-judge-repeat"]) == 0
    assert rig.calls == [
        {"mode": "replay", "suite": cli.SUITE, "judge_suite": cli.JUDGE_REPEAT_SUITE}
    ]
    assert rig.untouched()
    assert cli.side_results(cli.JUDGE_REPEAT_SUITE).exists()


def test_run_1_still_writes_the_candidate(rig: SideRig, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default suite is unchanged: a control for the two tests above."""
    with ScriptedLLM() as llm:
        llm.reply("An answer [P1].")
        llm.reply(VERDICT)
        rig.stub_clients(monkeypatch, llm)
        assert cli.main(["run", "--slice", "all"]) == 0
    assert rig.calls == [{"mode": "record", "suite": cli.SUITE, "judge_suite": None}]
    assert not rig.untouched()


@pytest.mark.parametrize(
    "suite",
    ["eval-answers-r4", "eval-judge-repeat-2", "assistant-redteam", "eval-answers;x", ""],
)
def test_any_other_run_suite_is_refused_before_anything_runs(
    rig: SideRig, monkeypatch: pytest.MonkeyPatch, suite: str
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("must not run")

    monkeypatch.setattr(cli, "_clients", boom)
    assert cli.main(["run", "--slice", "all", "--suite", suite]) == 2
    assert rig.untouched()


@pytest.mark.parametrize("mode", ["replay", "live", ""])
def test_the_judge_repeat_needs_record_mode(
    rig: SideRig, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("LLM_MODE", mode)
    monkeypatch.setattr(cli, "_clients", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert cli.main(["run", "--slice", "all", "--suite", "eval-judge-repeat"]) == 2


def test_the_judge_repeat_refuses_under_ci(rig: SideRig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    assert cli.main(["run", "--slice", "all", "--suite", "eval-judge-repeat"]) == 2


def test_the_side_results_are_git_ignored() -> None:
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".llm-proxy-log/" in ignored
    assert cli.SIDE_RESULTS_DIR == Path(".llm-proxy-log")
    assert cli.side_results("eval-answers-r2") == Path(
        ".llm-proxy-log/eval-answers-r2-results.json"
    )


def test_the_makefile_passes_only_a_checked_suite() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    live = makefile[makefile.index("\neval-live:") : makefile.index("\nenrich:")]
    assert '"$$EVAL_SUITE" =~ ^(eval-answers(-r[23])?|eval-judge-repeat)$$' in live
    # Unset, the command is the old one: `--suite` is added only when EVAL_SUITE is set.
    assert 'run --slice "$$EVAL_SLICE"$${EVAL_SUITE:+ --suite "$$EVAL_SUITE"}' in live
    # No new spending target: the judge repeat goes through eval-live (test_wiring's allow-list).
    assert not re.search(r"^eval-judge-repeat:", makefile, re.MULTILINE)
