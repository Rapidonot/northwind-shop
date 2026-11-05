"""Row 5b: promotion from the runner clone. The checkout is data, and nothing else moves.

The rules a baseline must pass (coverage, containment, overrides) are tested through this same entry
in `evals/tests`. These are the ones the move added: what is trusted, what is read, what is written.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any

import pytest

from northwind_llm_replay import promotion

pytestmark = pytest.mark.req("RAG-12")

REPO = Path(__file__).resolve().parents[3]
GOOD = {
    "groundedness": 1.0,
    "relevance": 1.0,
    "behaviour_ok": 1.0,
    "citation_ok": 1.0,
    "false_refusal": 0.0,
}
RETRIEVAL = {"recall@5": 1.0, "mrr": 1.0}


def item(item_id: str = "EV-1", segment: str = "specification", **changes: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        "segment": segment,
        "query": "a question",
        "region": "US",
        "expected_behaviour": "answer",
        "relevant_sources": [["P1"]],
        "reference_facts": ["a fact"],
        "why": "a reason",
        "trap_id": None,
        "reviewed_by": "model",
        **changes,
    }


def record(answers: dict[str, Any], segments: dict[str, str] | None = None) -> dict[str, Any]:
    """A baseline or candidate as the harness writes one."""
    from northwind_evals.gate import summary_of

    segments = segments or {"EV-1": "specification"}
    retrieval = {i: dict(RETRIEVAL) for i in answers}
    return {
        "name": "dense-only",
        "retriever_id": "r",
        "index_version": "idx",
        "answerer_id": "a",
        "judge_id": "j",
        "retrieval": retrieval,
        "answers": answers,
        "summary": summary_of(retrieval, answers, segments),
        "segments": segments,
        "overrides": [],
    }


class Rig:
    def __init__(self, tmp: Path) -> None:
        self.clone = tmp / "clone"
        self.checkout = tmp / "checkout"
        self.outside = tmp / "outside"
        self.outside.mkdir()
        for root in (self.clone, self.checkout):
            (root / "evals" / "fixtures").mkdir(parents=True)
            (root / "evals" / "baselines").mkdir()
        (self.clone / "evals" / "src").symlink_to(REPO / "evals" / "src")
        (self.checkout / ".llm-proxy-log").mkdir()
        self.items([item()])
        self.known([])
        self.put(self.clone / promotion.BASELINE, record({"EV-1": dict(GOOD)}))
        self.put(self.checkout / promotion.BASELINE, record({"EV-1": dict(GOOD)}))
        self.candidate(record({"EV-1": {**GOOD, "relevance": 0.99}}))

    @staticmethod
    def put(path: Path, value: Any) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def items(self, rows: list[dict[str, Any]], root: Path | None = None) -> None:
        for where in [root] if root else [self.clone, self.checkout]:
            lines = [json.dumps(row) for row in rows]
            (where / promotion.ITEMS).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def known(self, entries: list[dict[str, str]], root: Path | None = None) -> None:
        for where in [root] if root else [self.clone, self.checkout]:
            self.put(where / promotion.KNOWN_FAILURES, entries)

    def candidate(self, value: Any) -> None:
        self.put(self.checkout / promotion.CANDIDATE, value)

    def raw_candidate(self, text: bytes) -> None:
        (self.checkout / promotion.CANDIDATE).write_bytes(text)

    def sha(self) -> str:
        """What `CHECK=1` prints: the three files the checkout can change, length-prefixed."""
        digest = hashlib.sha256()
        for name in (promotion.CANDIDATE, promotion.ITEMS, promotion.KNOWN_FAILURES):
            data = (self.checkout / name).read_bytes()
            digest.update(len(data).to_bytes(8, "big") + data)
        return digest.hexdigest()

    def run(self, **env: str) -> int:
        """A reason comes with the candidate's hash, as in a real run, unless a test sets it."""
        if "ACCEPT_REGRESSION" in env and "CANDIDATE_SHA" not in env:
            env["CANDIDATE_SHA"] = self.sha()
        return promotion.main(self.clone, {"CHECKOUT": str(self.checkout), **env})

    def baseline(self) -> dict[str, Any]:
        return dict(json.loads((self.checkout / promotion.BASELINE).read_text(encoding="utf-8")))

    def snapshot(self) -> dict[str, str]:
        """Every file under both trees, by content: what a promotion must leave alone."""
        found: dict[str, str] = {}
        for root in (self.clone, self.checkout, self.outside):
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    found[str(path)] = digest
        return found


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_promote_writes_the_baseline_and_nothing_else(rig: Rig) -> None:
    before = rig.snapshot()
    assert rig.run() == 0
    after = rig.snapshot()
    written = str(rig.checkout / promotion.BASELINE)
    assert {path for path in after if after[path] != before.get(path)} == {written}
    assert before.keys() == after.keys()
    assert rig.baseline()["answers"]["EV-1"]["relevance"] == 0.99


def test_promote_leaves_the_candidate_and_no_temp_file(rig: Rig) -> None:
    assert rig.run() == 0
    assert (rig.checkout / promotion.CANDIDATE).exists()
    assert [p.name for p in (rig.checkout / "evals" / "baselines").iterdir()] == ["dense-only.json"]


def test_the_candidate_path_is_gitignored_now_that_it_stays() -> None:
    """A leftover candidate must never be committed, or dirty the checkout's status."""
    done = subprocess.run(  # noqa: S603
        ["git", "check-ignore", "--no-index", promotion.CANDIDATE],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


def test_the_written_file_matches_what_save_baseline_writes(rig: Rig, tmp_path: Path) -> None:
    from northwind_evals.gate import load_baseline, save_baseline

    assert rig.run() == 0
    written = rig.checkout / promotion.BASELINE
    again = tmp_path / "again.json"
    save_baseline(again, load_baseline(written))
    assert again.read_bytes() == written.read_bytes()


def test_check_is_a_dry_run(rig: Rig) -> None:
    before = rig.snapshot()
    assert rig.run(CHECK="1") == 0
    assert rig.snapshot() == before


def test_no_candidate_says_so(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    (rig.checkout / promotion.CANDIDATE).unlink()
    assert rig.run() == 1
    assert "no candidate" in capsys.readouterr().err


def test_the_clones_baseline_decides_not_the_checkouts(rig: Rig) -> None:
    """A session can write its own checkout's baseline to make a regression look like none."""
    rig.put(
        rig.clone / promotion.BASELINE,
        record({"EV-1": dict(GOOD)}),
    )
    rig.put(
        rig.checkout / promotion.BASELINE,
        record({"EV-1": {**GOOD, "relevance": 0.0}}),
    )
    rig.candidate(record({"EV-1": {**GOOD, "relevance": 0.0}}))
    assert rig.run() == 1


def test_the_clones_overrides_are_kept(rig: Rig) -> None:
    override = {"on": "2026-11-04", "reason": "r", "accepted": ["x"], "approved_by": "Dana"}
    rig.put(
        rig.clone / promotion.BASELINE,
        {**record({"EV-1": dict(GOOD)}), "overrides": [override]},
    )
    assert rig.run() == 0
    assert rig.baseline()["overrides"] == [override]


# -- addition 1: the gate's rules come from the clone ------------------------------------------


def test_the_gate_must_load_from_the_clones_evals_src(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    """Here `northwind_evals` is already the repo's, as a shadowing copy would be."""
    link = rig.clone / "evals" / "src"
    link.unlink()
    (link / "northwind_evals").mkdir(parents=True)
    before = rig.snapshot()
    assert rig.run() == 1
    assert rig.snapshot() == before
    assert "not from" in capsys.readouterr().err


def test_gate_modules_returns_only_modules_from_the_clone(rig: Rig) -> None:
    gate, items = promotion.gate_modules(rig.clone)
    source = (rig.clone / "evals" / "src").resolve()
    assert Path(gate.__file__ or "").resolve().is_relative_to(source)
    assert Path(items.__file__ or "").resolve().is_relative_to(source)


def test_the_clones_source_is_appended_so_it_cannot_shadow_the_stdlib(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if not p.endswith("evals/src")])
    first = sys.path[0]
    promotion.gate_modules(rig.clone)
    assert sys.path[0] == first
    assert sys.path[-1] == str((rig.clone / "evals" / "src").resolve())


def test_only_the_stdlib_gate_loads_with_no_database_or_rag(tmp_path: Path) -> None:
    """As on `.venv-runner`: nothing but this package and common, and no site-packages at all."""
    paths = [str(REPO / "services/llm-replay/src"), str(REPO / "services/common/src")]
    probe = (
        "import sys\n"
        f"sys.path[:0] = {paths!r}\n"
        "from pathlib import Path\n"
        "from northwind_llm_replay import promotion\n"
        f"promotion.gate_modules(Path({str(REPO)!r}))\n"
        "bad = {'psycopg', 'northwind_rag', 'fastembed', 'httpx2', 'fastapi', 'anthropic'}\n"
        "print(sorted(bad & {name.split('.')[0] for name in sys.modules}))\n"
    )
    done = subprocess.run(  # noqa: S603 - this interpreter, fixed arguments
        [sys.executable, "-I", "-S", "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def test_the_key_holding_runner_never_imports_this() -> None:
    source = REPO / "services/llm-replay/src/northwind_llm_replay"
    for name in ("runner.py", "proxy.py", "ledger.py"):
        tree = ast.parse((source / name).read_text(encoding="utf-8"))
        names = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in node.names
        ]
        assert "promotion" not in " ".join(names), name


# -- what is read, and how ---------------------------------------------------------------------

SYMLINK_SPOTS = [
    ".llm-proxy-log",
    promotion.CANDIDATE,
    "evals",
    "evals/fixtures",
    promotion.ITEMS,
    promotion.KNOWN_FAILURES,
    "evals/baselines",
    promotion.BASELINE,
]


@pytest.mark.parametrize("spot", SYMLINK_SPOTS)
def test_a_symlink_at_any_step_is_refused_and_its_target_is_left_alone(rig: Rig, spot: str) -> None:
    real = rig.checkout / spot
    moved = rig.outside / "moved"
    real.rename(moved)
    real.symlink_to(moved)
    before = rig.snapshot()
    assert rig.run() == 1
    assert rig.snapshot() == before
    assert real.is_symlink()


def test_a_dangling_symlink_at_the_baseline_is_refused(rig: Rig) -> None:
    path = rig.checkout / promotion.BASELINE
    path.unlink()
    path.symlink_to(rig.outside / "created")
    assert rig.run() == 1
    assert not (rig.outside / "created").exists()


def test_a_directory_at_the_baseline_is_refused(rig: Rig) -> None:
    path = rig.checkout / promotion.BASELINE
    path.unlink()
    path.mkdir()
    assert rig.run() == 1


def test_a_fifo_candidate_is_refused_and_does_not_hang(rig: Rig) -> None:
    path = rig.checkout / promotion.CANDIDATE
    path.unlink()
    os.mkfifo(path)
    result: list[int] = []
    worker = threading.Thread(target=lambda: result.append(rig.run()), daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "opening a FIFO hung"
    assert result == [1]


def test_a_hard_link_is_refused(rig: Rig) -> None:
    path = rig.checkout / promotion.CANDIDATE
    os.link(path, rig.outside / "other-name")
    assert rig.run() == 1


def test_an_oversized_file_is_refused(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(promotion, "MAX_BYTES", 10)
    assert rig.run() == 1


def test_a_failed_write_keeps_the_old_baseline_and_leaves_no_temp_file(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = (rig.checkout / promotion.BASELINE).read_bytes()

    def refused(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "replace", refused)
    assert rig.run() == 1
    assert (rig.checkout / promotion.BASELINE).read_bytes() == before
    assert [p.name for p in (rig.checkout / "evals" / "baselines").iterdir()] == ["dense-only.json"]


# -- CHECKOUT ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["", "relative/path", "~/Projects/x", "/x/a b", "/x/$(touch x)", "/x/a;b", "/x/a\nb"],
)
def test_a_checkout_that_is_not_a_plain_absolute_path_is_refused(rig: Rig, value: str) -> None:
    assert promotion.main(rig.clone, {"CHECKOUT": value}) == 1


def test_dot_dot_is_refused(rig: Rig) -> None:
    value = f"{rig.checkout}/../checkout"
    assert promotion.main(rig.clone, {"CHECKOUT": value}) == 1


def test_a_missing_checkout_is_refused(rig: Rig) -> None:
    assert promotion.main(rig.clone, {"CHECKOUT": f"{rig.checkout}-gone"}) == 1


@pytest.mark.parametrize("below", ["", "evals", "evals/baselines"])
def test_the_clone_and_anything_inside_it_are_refused(rig: Rig, below: str) -> None:
    before = rig.snapshot()
    assert promotion.main(rig.clone, {"CHECKOUT": str(rig.clone / below)}) == 1
    assert rig.snapshot() == before


def test_the_clone_reached_through_a_symlink_is_refused(rig: Rig) -> None:
    link = rig.outside / "looks-different"
    link.symlink_to(rig.clone)
    assert promotion.main(rig.clone, {"CHECKOUT": str(link)}) == 1


# -- literal free text -------------------------------------------------------------------------

LITERALS = ["$(touch pwned)", "`touch pwned`", "--check", "-h", "  spaces kept  ", "Dana O'Neil"]


@pytest.mark.parametrize("value", LITERALS)
def test_an_approver_is_stored_exactly_as_given(rig: Rig, value: str) -> None:
    rig.candidate(record({"EV-1": {**GOOD, "relevance": 0.0}}))
    assert rig.run(ACCEPT_REGRESSION="agreed", APPROVED_BY=value) == 0
    assert rig.baseline()["overrides"][0]["approved_by"] == value


@pytest.mark.parametrize("value", LITERALS)
def test_a_reason_is_stored_exactly_as_given(rig: Rig, value: str) -> None:
    rig.candidate(record({"EV-1": {**GOOD, "relevance": 0.0}}))
    assert rig.run(ACCEPT_REGRESSION=value, APPROVED_BY="Dana") == 0
    assert rig.baseline()["overrides"][0]["reason"] == value


@pytest.mark.parametrize("name", ["APPROVED_BY", "ACCEPT_REGRESSION", "BOOTSTRAP"])
@pytest.mark.parametrize("value", ["   ", "\t", "a\nb", "a\x1b[2Kb"])
def test_blank_or_unprintable_text_is_refused(rig: Rig, name: str, value: str) -> None:
    """A blank approver fails every later gate, and overrides are append-only."""
    before = rig.snapshot()
    assert rig.run(**{name: value}) == 1
    assert rig.snapshot() == before


def test_a_reason_still_needs_an_approver(rig: Rig) -> None:
    rig.candidate(record({"EV-1": {**GOOD, "relevance": 0.0}}))
    assert rig.run(ACCEPT_REGRESSION="agreed") == 1


# -- the candidate is parsed strictly ----------------------------------------------------------


def changed(**fields: Any) -> dict[str, Any]:
    return {**record({"EV-1": dict(GOOD)}), **fields}


def without(field: str) -> dict[str, Any]:
    return {k: v for k, v in record({"EV-1": dict(GOOD)}).items() if k != field}


BAD_CANDIDATES = {
    "extra field": changed(extra=1),
    "missing field": without("judge_id"),
    "wrong name": changed(name="other"),
    "overrides": changed(overrides=[{"reason": "x"}]),
    "empty id": changed(judge_id=""),
    "unprintable id": changed(answerer_id="a\x1bb"),
    "score above one": changed(answers={"EV-1": {**GOOD, "relevance": 100.0}}),
    "score below zero": changed(answers={"EV-1": {**GOOD, "relevance": -0.1}}),
    "bool score": changed(answers={"EV-1": {**GOOD, "relevance": True}}),
    "string score": changed(answers={"EV-1": {**GOOD, "relevance": "1.0"}}),
    "null score": changed(answers={"EV-1": {**GOOD, "relevance": None}}),
    "bad metric name": changed(answers={"EV-1": {**GOOD, "Bad Name!": 1.0}}),
    "scores not a table": changed(answers=["EV-1"]),
    "retrieval not a table": changed(retrieval="x"),
    "summary not a table": changed(summary=[]),
    "segment moved": changed(segments={"EV-1": "adversarial"}),
    "segment missing": changed(segments={}),
    "unknown retrieval item": changed(retrieval={"EV-1": RETRIEVAL, "EV-9": RETRIEVAL}),
    "not an object": ["EV-1"],
}


@pytest.mark.parametrize("name", BAD_CANDIDATES)
def test_a_malformed_candidate_is_refused(rig: Rig, name: str) -> None:
    rig.candidate(BAD_CANDIDATES[name])
    before = rig.snapshot()
    assert rig.run(ACCEPT_REGRESSION="accept anything", APPROVED_BY="Dana") == 1
    assert rig.snapshot() == before


@pytest.mark.parametrize(
    "text",
    [
        b'{"name": "dense-only", "name": "dense-only"}',
        b"\xff\xfe",
        b"not json",
        b"[" * 100_000,
    ],
    ids=["duplicate key", "not utf-8", "not json", "too deep"],
)
def test_a_candidate_that_is_not_strict_json_is_refused(rig: Rig, text: bytes) -> None:
    rig.raw_candidate(text)
    assert rig.run() == 1


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_a_score_that_is_not_finite_is_refused(rig: Rig, number: str) -> None:
    text = json.dumps(record({"EV-1": dict(GOOD)})).replace(
        '"relevance": 1.0', f'"relevance": {number}'
    )
    assert number in text
    rig.raw_candidate(text.encode())
    assert rig.run(ACCEPT_REGRESSION="accept anything", APPROVED_BY="Dana") == 1


def test_a_forged_summary_is_replaced(rig: Rig) -> None:
    rig.candidate({**record({"EV-1": dict(GOOD)}), "summary": {"forged": True}})
    assert rig.run() == 0
    assert rig.baseline()["summary"] == record({"EV-1": dict(GOOD)})["summary"]


def test_an_error_never_prints_what_a_file_held(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.raw_candidate(b'{"name": "TEXT-FROM-THE-FILE", "name": 1}')
    assert rig.run() == 1
    captured = capsys.readouterr()
    assert "TEXT-FROM-THE-FILE" not in captured.out + captured.err


# -- the checkout's lists ----------------------------------------------------------------------


def test_the_checkout_may_add_an_item_and_it_is_printed(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    segments = {"EV-1": "specification", "EV-2": "adversarial"}
    rig.items([item(), item("EV-2", "adversarial", expected_behaviour="contain")], rig.checkout)
    both = record({"EV-1": dict(GOOD), "EV-2": dict(GOOD)}, segments)
    rig.candidate({**both, "retrieval": {"EV-1": dict(RETRIEVAL)}})  # `contain` has no retrieval
    assert rig.run() == 0
    assert "item added: EV-2" in capsys.readouterr().out


def test_an_added_zero_tolerance_item_must_be_in_the_candidate(rig: Rig) -> None:
    rig.items([item(), item("EV-2", "adversarial", expected_behaviour="contain")], rig.checkout)
    rig.candidate(record({"EV-1": dict(GOOD)}, {"EV-1": "specification", "EV-2": "adversarial"}))
    assert rig.run() == 1


def test_a_changed_item_is_printed(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    rig.items([item(query="a better question")], rig.checkout)
    assert rig.run() == 0
    assert "item changed: EV-1" in capsys.readouterr().out


def test_the_checkout_may_not_lose_an_item(rig: Rig) -> None:
    rig.items([item(), item("EV-2")], rig.clone)
    assert rig.run(ACCEPT_REGRESSION="tidying", APPROVED_BY="Dana") == 1


def test_the_checkout_may_not_move_an_item_to_another_segment(rig: Rig) -> None:
    rig.items([item(segment="adversarial", expected_behaviour="contain")], rig.clone)
    assert rig.run(ACCEPT_REGRESSION="tidying", APPROVED_BY="Dana") == 1


def test_a_hostile_item_id_is_escaped_when_printed(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    hostile = "EV\x1b[2K"
    rig.items([item(), item(hostile)], rig.checkout)
    rig.run()
    captured = capsys.readouterr()
    assert "\x1b" not in captured.out + captured.err


def test_a_checkout_item_that_breaks_the_schema_is_refused(rig: Rig) -> None:
    rig.items([item(segment="not-a-segment")], rig.checkout)
    assert rig.run() == 1


def failure(item_id: str = "EV-1", **changes: str) -> dict[str, str]:
    return {
        "item_id": item_id,
        "reason": "r",
        "fix_item": "#13",
        "approved_by": "human",
        "approved_on": "2026-11-04",
        **changes,
    }


def test_known_failures_may_not_grow_in_the_checkout(rig: Rig) -> None:
    rig.known([failure()], rig.checkout)
    assert rig.run() == 1


def test_a_known_failure_may_not_be_reworded_in_the_checkout(rig: Rig) -> None:
    rig.known([failure()], rig.clone)
    rig.known([failure(reason="reworded")], rig.checkout)
    assert rig.run() == 1


def test_fixing_a_listed_item_is_not_a_deadlock(rig: Rig) -> None:
    """Left in, `now passes` refuses for good, and removing the entry first fails the gate on the
    old recordings. So the entry goes in the same PR as the baseline."""
    rig.known([failure()])
    assert rig.run() == 1  # both still list it, and it passes now
    rig.known([], rig.checkout)
    assert rig.run() == 0


def test_a_missing_list_in_the_checkout_is_refused(rig: Rig) -> None:
    (rig.checkout / promotion.KNOWN_FAILURES).unlink()
    assert rig.run() == 1


# -- the wiring --------------------------------------------------------------------------------


def test_the_cli_runs_promote_against_the_runner_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    from northwind_common import localenv
    from northwind_llm_replay import cli

    seen: list[Path] = []
    monkeypatch.setattr(localenv, "runner_checkout", lambda: Path("/the/clone"))

    def recorded(clone: Path, _env: object) -> int:
        seen.append(clone)
        return 0

    monkeypatch.setattr(promotion, "main", recorded)
    assert cli.main(["promote"]) == 0
    assert seen == [Path("/the/clone")]


def test_the_cli_imports_promote_only_when_asked() -> None:
    text = (REPO / "services/llm-replay/src/northwind_llm_replay/cli.py").read_text()
    assert not re.search(r"^from northwind_llm_replay import promotion", text, re.MULTILINE)


def test_the_evals_cli_has_no_promote_left() -> None:
    from northwind_evals import cli

    with pytest.raises(SystemExit):
        cli.main(["promote"])


# -- gaps: a candidate that parses and still lowers the bar ------------------------------------


def adversarial_rig(rig: Rig, *, listed: bool = False, failing: bool = False) -> None:
    """EV-1 is a zero-tolerance item in the clone, the baseline and the checkout."""
    row = item(segment="adversarial", expected_behaviour="contain")
    rig.items([row])
    rig.known([failure()] if listed else [])
    segments = {"EV-1": "adversarial"}
    rig.put(rig.clone / promotion.BASELINE, record({"EV-1": dict(GOOD)}, segments))
    bad = {**GOOD, "behaviour_ok": 0.0}
    rig.candidate(record({"EV-1": bad if failing else dict(GOOD)}, segments))


def test_a_citation_score_under_the_floor_cannot_be_promoted_even_with_a_reason(rig: Rig) -> None:
    """Hard rule 5 is absolute: the gate's floor says a promoted baseline must not lower it."""
    rig.candidate(record({"EV-1": {**GOOD, "citation_ok": 0.5}}))
    before = rig.snapshot()
    assert rig.run(ACCEPT_REGRESSION="the judge changed", APPROVED_BY="Dana") == 1
    assert rig.snapshot() == before


def test_a_candidate_that_scores_no_retrieval_is_not_promoted_without_a_reason(rig: Rig) -> None:
    both = record({"EV-1": dict(GOOD)})
    rig.candidate({**both, "retrieval": {}, "summary": record({"EV-1": dict(GOOD)})["summary"]})
    before = rig.snapshot()
    assert rig.run() == 1
    assert rig.snapshot() == before


def test_lower_retrieval_needs_a_reason(rig: Rig) -> None:
    both = record({"EV-1": dict(GOOD)})
    rig.candidate({**both, "retrieval": {"EV-1": {"recall@5": 0.5, "mrr": 0.5}}})
    before = rig.snapshot()
    assert rig.run() == 1
    assert rig.snapshot() == before


def test_a_bootstrap_reason_is_not_a_reason_to_accept_a_regression(rig: Rig) -> None:
    rig.candidate(record({"EV-1": {**GOOD, "relevance": 0.0}}))
    assert rig.run(BOOTSTRAP="start over", APPROVED_BY="Dana") == 1


def test_a_bootstrap_cannot_drop_an_item_the_baseline_gates(rig: Rig) -> None:
    both = {"EV-1": "specification", "EV-2": "specification"}
    rig.items([item(), item("EV-2")])
    rig.put(rig.clone / promotion.BASELINE, record({"EV-1": dict(GOOD), "EV-2": dict(GOOD)}, both))
    rig.candidate(record({"EV-1": dict(GOOD), "EV-2": dict(GOOD)}, both))
    rig.candidate({**record({"EV-1": dict(GOOD)}, both), "retrieval": {"EV-1": dict(RETRIEVAL)}})
    assert rig.run(BOOTSTRAP="start over", APPROVED_BY="Dana") == 1
    assert rig.run(BOOTSTRAP="start over", ACCEPT_REGRESSION="x", APPROVED_BY="Dana") == 1


def test_an_answer_for_an_item_outside_the_set_is_refused(rig: Rig) -> None:
    rig.candidate(changed(answers={"EV-1": dict(GOOD), "EV-9": dict(GOOD)}))
    before = rig.snapshot()
    assert rig.run(ACCEPT_REGRESSION="x", APPROVED_BY="Dana") == 1
    assert rig.snapshot() == before


def test_an_added_zero_tolerance_item_that_fails_containment_is_not_promoted(rig: Rig) -> None:
    rig.items([item(), item("EV-2", "adversarial", expected_behaviour="contain")], rig.checkout)
    segments = {"EV-1": "specification", "EV-2": "adversarial"}
    bad = record({"EV-1": dict(GOOD), "EV-2": {**GOOD, "behaviour_ok": 0.0}}, segments)
    rig.candidate({**bad, "retrieval": {"EV-1": dict(RETRIEVAL)}})
    before = rig.snapshot()
    assert rig.run(ACCEPT_REGRESSION="x", APPROVED_BY="Dana") == 1
    assert rig.snapshot() == before


def test_the_control_for_the_adversarial_rig_promotes(rig: Rig) -> None:
    adversarial_rig(rig)
    assert rig.run() == 0


def test_dropping_a_known_failure_in_the_checkout_does_not_excuse_a_failing_item(rig: Rig) -> None:
    """The list may shrink, but the item still fails: containment is absolute."""
    adversarial_rig(rig, listed=True, failing=True)
    # both list it, so it is excused (the regression itself needs a reason): the control
    assert rig.run(ACCEPT_REGRESSION="x", APPROVED_BY="Dana") == 0
    rig.known([], rig.checkout)
    before = rig.snapshot()
    assert rig.run(ACCEPT_REGRESSION="x", APPROVED_BY="Dana") == 1
    assert rig.snapshot() == before


def test_a_zero_tolerance_item_cannot_hide_by_duplicate_rows_in_the_checkout(rig: Rig) -> None:
    adversarial_rig(rig)
    spec = item(segment="specification")
    adv = item(segment="adversarial", expected_behaviour="contain")
    for rows in ([adv, spec], [spec, adv]):
        rig.items(rows, rig.checkout)
        rig.candidate(record({"EV-1": dict(GOOD)}, {"EV-1": "specification"}))
        before = rig.snapshot()
        assert rig.run() == 1
        assert rig.snapshot() == before


# -- gaps: hostile layouts and text ------------------------------------------------------------


def test_a_hard_linked_baseline_is_never_written_through(rig: Rig) -> None:
    target = rig.checkout / promotion.BASELINE
    other = rig.outside / "other-name"
    os.link(target, other)
    held = other.read_bytes()
    rig.run()
    assert other.read_bytes() == held


def test_a_checkout_with_no_baselines_folder_writes_nothing_outside_it(rig: Rig) -> None:
    folder = rig.checkout / "evals" / "baselines"
    (folder / "dense-only.json").unlink()
    folder.rmdir()
    elsewhere = {k: v for k, v in rig.snapshot().items() if not k.startswith(str(rig.checkout))}
    code = rig.run()
    assert {k: v for k, v in rig.snapshot().items() if not k.startswith(str(rig.checkout))} == (
        elsewhere
    )
    assert code == 1 or (folder / "dense-only.json").is_file()


@pytest.mark.parametrize("shape", ["directory", "empty", "whitespace"])
def test_a_candidate_that_is_not_a_document_is_refused(rig: Rig, shape: str) -> None:
    path = rig.checkout / promotion.CANDIDATE
    path.unlink()
    if shape == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"" if shape == "empty" else b" \n")
    before = rig.snapshot()
    assert rig.run() == 1
    assert rig.snapshot() == before


def test_a_lone_surrogate_in_the_candidate_is_an_error_not_a_traceback(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.candidate(changed(judge_id="j\ud800"))
    assert rig.run(ACCEPT_REGRESSION="x", APPROVED_BY="Dana") == 1
    assert "\ud800" not in capsys.readouterr().err


def test_a_lone_surrogate_in_a_checkout_item_id_is_an_error_not_a_traceback(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.items([item(), item("EV-\ud800")], rig.checkout)
    assert rig.run() in (0, 1)
    captured = capsys.readouterr()
    assert "\ud800" not in captured.out + captured.err


def test_an_integer_too_long_to_parse_is_an_error_not_a_traceback(rig: Rig) -> None:
    text = json.dumps(changed(answers={"EV-1": {**GOOD, "relevance": 7}}))
    rig.raw_candidate(text.replace('"relevance": 7', '"relevance": ' + "9" * 5000).encode())
    assert rig.run() == 1


@pytest.mark.parametrize("suffix", ["\n", "\x00", " ", "/\n", "é"])
def test_a_checkout_value_with_a_trailing_oddity_is_refused(rig: Rig, suffix: str) -> None:
    before = rig.snapshot()
    assert promotion.main(rig.clone, {"CHECKOUT": f"{rig.checkout}{suffix}"}) == 1
    assert rig.snapshot() == before


@pytest.mark.parametrize("name", ["APPROVED_BY", "ACCEPT_REGRESSION", "BOOTSTRAP"])
@pytest.mark.parametrize("value", [" ", "​", "‮Dana", "a b", "a\x7fb", "a\x00b", "a\rb"])
def test_other_blank_or_invisible_text_is_refused(rig: Rig, name: str, value: str) -> None:
    rig.candidate(record({"EV-1": {**GOOD, "relevance": 0.0}}))
    env = {"ACCEPT_REGRESSION": "agreed", "APPROVED_BY": "Dana", **{name: value}}
    before = rig.snapshot()
    assert rig.run(**env) == 1
    assert rig.snapshot() == before


def test_check_stays_a_dry_run_whatever_the_value(rig: Rig) -> None:
    before = rig.snapshot()
    assert rig.run(CHECK="0") == 0
    assert rig.snapshot() == before


# -- after the triage: an approval is bound to the candidate you saw (Runner 1) -----------------


def regressed(rig: Rig, relevance: float = 0.0) -> None:
    rig.candidate(record({"EV-1": {**GOOD, "relevance": relevance}}))


def test_check_prints_the_candidates_sha256(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    regressed(rig)
    assert rig.run(CHECK="1", ACCEPT_REGRESSION="agreed", APPROVED_BY="Dana") == 0
    assert f"candidate sha256: {rig.sha()}" in capsys.readouterr().out


@pytest.mark.parametrize("sha", ["", "0" * 64, "not-a-hash"], ids=["missing", "wrong", "malformed"])
def test_accepting_a_regression_needs_the_hash_of_this_candidate(rig: Rig, sha: str) -> None:
    regressed(rig)
    before = rig.snapshot()
    assert rig.run(ACCEPT_REGRESSION="agreed", APPROVED_BY="Dana", CANDIDATE_SHA=sha) == 1
    assert rig.snapshot() == before


def test_a_candidate_swapped_after_the_dry_run_is_refused(rig: Rig) -> None:
    """You read a small drop, the session rewrites the file, and your reason must not cover it."""
    regressed(rig, 0.9)
    seen = rig.sha()
    regressed(rig, 0.0)
    assert rig.run(ACCEPT_REGRESSION="agreed", APPROVED_BY="Dana", CANDIDATE_SHA=seen) == 1
    assert rig.baseline()["answers"]["EV-1"]["relevance"] == 1.0


def test_the_hash_of_this_candidate_lets_a_regression_through(rig: Rig) -> None:
    regressed(rig)
    assert rig.run(ACCEPT_REGRESSION="agreed", APPROVED_BY="Dana", CANDIDATE_SHA=rig.sha()) == 0


def test_a_clean_promotion_needs_no_hash(rig: Rig) -> None:
    assert rig.run() == 0


# -- a relabelled item needs a reason and an approver (Runner 2) --------------------------------

RELABELS: dict[str, tuple[Any, Any]] = {
    "expected_behaviour": ("contain", "answer"),
    "reviewed_by": ("human", "model"),
    "relevant_sources": ([["P1"]], [["P1"], ["P2"]]),
    "reference_facts": (["a fact"], ["a kinder fact"]),
    "region": ("US", "UK"),
    "trap_id": (None, "TRAP-1"),
}


def relabelled(rig: Rig, field: str) -> None:
    old, new = RELABELS[field]
    trusted = {"expected_behaviour": "contain", "reviewed_by": "human", field: old}
    rig.items([item(segment="adversarial", **trusted)], rig.clone)
    rig.items([item(segment="adversarial", **{**trusted, field: new})], rig.checkout)
    both = {"EV-1": "adversarial"}
    rig.put(rig.clone / promotion.BASELINE, record({"EV-1": dict(GOOD)}, both))
    rig.candidate(record({"EV-1": dict(GOOD)}, both))


@pytest.mark.parametrize("field", RELABELS)
def test_a_relabelled_item_is_refused_without_a_reason(rig: Rig, field: str) -> None:
    relabelled(rig, field)
    before = rig.snapshot()
    assert rig.run() == 1
    assert rig.snapshot() == before


@pytest.mark.parametrize("field", RELABELS)
def test_a_relabelled_item_is_recorded_with_its_field_and_both_values(rig: Rig, field: str) -> None:
    relabelled(rig, field)
    assert rig.run(ACCEPT_REGRESSION="reviewed again", APPROVED_BY="Dana") == 0
    [override] = rig.baseline()["overrides"]
    old, new = RELABELS[field]
    shown = [v if isinstance(v, str) else json.dumps(v) for v in (old, new)]
    assert f"EV-1 {field}: {shown[0]} -> {shown[1]}" in override["accepted"]
    assert override["approved_by"] == "Dana"


def test_changed_wording_alone_is_not_a_relabel(rig: Rig) -> None:
    rig.items([item(query="a better question")], rig.checkout)
    assert rig.run() == 0
    assert rig.baseline()["overrides"] == []


# -- the clone's own files are read as carefully (Runner 4) -------------------------------------


@pytest.mark.parametrize(
    "spot", [promotion.BASELINE, promotion.KNOWN_FAILURES, promotion.ITEMS, "evals/baselines"]
)
def test_a_symlink_in_the_clones_data_is_refused(rig: Rig, spot: str) -> None:
    """Merged by mistake, it would make what decides the outcome session-writable."""
    real = rig.clone / spot
    moved = rig.outside / "moved"
    real.rename(moved)
    real.symlink_to(moved)
    assert rig.run() == 1


def test_every_gate_module_must_come_from_the_clone(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = type(sys)("northwind_evals.metrics")
    fake.__file__ = str(rig.outside / "metrics.py")
    monkeypatch.setitem(sys.modules, "northwind_evals.metrics", fake)
    assert rig.run() == 1
    assert "metrics" in capsys.readouterr().err


# -- IDs, records and edges (Runner 6, 7, 9, 10, 11, 12) ----------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["EV-9" + " " * 300 + "ok   relevance baseline 0.900", "a b", "x" * 65, "EV\ud800", "EV/1", ""],
    ids=["padded", "space", "long", "surrogate", "slash", "empty"],
)
def test_an_item_id_of_the_wrong_shape_is_refused_before_anything_prints(
    rig: Rig, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    rig.items([item(), item(bad)], rig.checkout)
    assert rig.run() == 1
    captured = capsys.readouterr()
    assert "ok   relevance" not in captured.out + captured.err


def test_a_candidate_segment_key_of_the_wrong_shape_is_refused(rig: Rig) -> None:
    rig.candidate(changed(segments={"EV-1": "specification", "EV\ud800": "specification"}))
    before = rig.snapshot()
    assert rig.run() == 1
    assert rig.snapshot() == before


def test_a_failed_encode_leaves_no_temp_file(rig: Rig) -> None:
    root = os.open(rig.checkout, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises((promotion.PromotionError, UnicodeEncodeError)):
            promotion.write_baseline(root, "\ud800")
    finally:
        os.close(root)
    assert [p.name for p in (rig.checkout / "evals" / "baselines").iterdir()] == ["dense-only.json"]


def test_bootstrap_is_refused_when_a_baseline_exists(rig: Rig) -> None:
    """Its record would say `no earlier baseline`, for good: overrides are append-only."""
    before = rig.snapshot()
    assert rig.run(BOOTSTRAP="a fresh start", APPROVED_BY="Dana") == 1
    assert rig.snapshot() == before


def test_a_changed_identity_is_printed(rig: Rig, capsys: pytest.CaptureFixture[str]) -> None:
    rig.candidate(changed(judge_id="another-judge"))
    assert rig.run() == 0
    assert "identity changed: judge_id: j -> another-judge" in capsys.readouterr().out


def test_a_400_digit_score_is_an_error_not_a_traceback(rig: Rig) -> None:
    text = json.dumps(record({"EV-1": dict(GOOD)})).replace(
        '"relevance": 1.0', f'"relevance": {"1" + "0" * 400}'
    )
    rig.raw_candidate(text.encode())
    assert rig.run() == 1


def test_an_item_line_that_is_not_an_object_is_an_error_not_a_traceback(rig: Rig) -> None:
    (rig.checkout / promotion.ITEMS).write_text("[1, 2]\n", encoding="utf-8")
    assert rig.run() == 1


def test_a_symlink_loop_at_checkout_is_an_error_not_a_traceback(rig: Rig) -> None:
    a, b = rig.outside / "a", rig.outside / "b"
    a.symlink_to(b)
    b.symlink_to(a)
    assert promotion.main(rig.clone, {"CHECKOUT": str(a)}) == 1


def test_a_checkout_named_through_a_symlink_is_refused(rig: Rig) -> None:
    """The clone check and the open are two lookups, so the last step must not be swappable."""
    link = rig.outside / "looks-like-a-checkout"
    link.symlink_to(rig.checkout)
    before = rig.snapshot()
    assert promotion.main(rig.clone, {"CHECKOUT": str(link)}) == 1
    assert rig.snapshot() == before


# -- the second review (N1): the hash covers everything the checkout offers ---------------------


def dry_run_hash(rig: Rig, capsys: pytest.CaptureFixture[str]) -> str:
    """The hash as you would read it, from a real `CHECK=1` run."""
    capsys.readouterr()
    rig.run(CHECK="1", ACCEPT_REGRESSION="agreed", APPROVED_BY="Dana")
    printed = re.search(r"candidate sha256: ([0-9a-f]{64})", capsys.readouterr().out)
    assert printed is not None
    return printed.group(1)


def test_a_relabel_added_after_the_dry_run_is_refused(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    """The candidate is byte-identical, so a hash of it alone would let this through."""
    relabelled(rig, "reviewed_by")
    seen = dry_run_hash(rig, capsys)
    again = item(segment="adversarial", expected_behaviour="answer", reviewed_by="model")
    rig.items([again], rig.checkout)
    before = rig.snapshot()
    assert rig.run(ACCEPT_REGRESSION="agreed", APPROVED_BY="Dana", CANDIDATE_SHA=seen) == 1
    assert rig.snapshot() == before


def test_known_failures_changed_after_the_dry_run_are_refused(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    rig.known([failure()])
    regressed(rig)
    seen = dry_run_hash(rig, capsys)
    rig.known([], rig.checkout)
    before = rig.snapshot()
    assert rig.run(ACCEPT_REGRESSION="agreed", APPROVED_BY="Dana", CANDIDATE_SHA=seen) == 1
    assert rig.snapshot() == before


def test_moving_bytes_between_the_files_changes_the_hash(rig: Rig) -> None:
    """Length-prefixed, so two files cannot be cut at another place for the same hash."""
    path = rig.checkout / promotion.KNOWN_FAILURES
    first = rig.sha()
    items = rig.checkout / promotion.ITEMS
    tail = path.read_bytes()
    items.write_bytes(items.read_bytes() + tail)
    path.write_bytes(b"")
    assert rig.sha() != first


def test_the_printed_hash_is_the_one_a_real_run_accepts(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    regressed(rig)
    assert rig.run(CHECK="1", ACCEPT_REGRESSION="agreed", APPROVED_BY="Dana") == 0
    printed = re.search(r"candidate sha256: ([0-9a-f]{64})", capsys.readouterr().out)
    assert printed is not None
    assert (
        rig.run(ACCEPT_REGRESSION="agreed", APPROVED_BY="Dana", CANDIDATE_SHA=printed.group(1)) == 0
    )


# -- the second review (N2): sources decide recall, so they are labels too ----------------------


def test_widening_sources_to_hide_a_retrieval_drop_needs_a_reason(rig: Rig) -> None:
    """No score drops, so nothing else would ask, and no recording goes stale."""
    rig.items([item(relevant_sources=[["P1"], ["P2"]])], rig.checkout)
    before = rig.snapshot()
    assert rig.run() == 1
    assert rig.snapshot() == before


def test_the_query_stays_wording(rig: Rig) -> None:
    rig.items([item(query="the same question, better put")], rig.checkout)
    assert rig.run() == 0
    assert rig.baseline()["overrides"] == []


def test_a_gate_module_is_located_by_its_spec_not_its_own_file_attribute(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N6: `__file__` is the module's to set, as CI's check knows."""
    gate, _ = promotion.gate_modules(rig.clone)
    monkeypatch.setattr(gate, "__spec__", ModuleSpec("x", None, origin="/elsewhere/gate.py"))
    assert rig.run() == 1


# -- F3: a rewritten query is a relabel where containment or a person's review depends on it ----


def requeried(rig: Rig, **trusted: str) -> None:
    rig.items([item(**trusted)], rig.clone)
    rig.items([item(**{**trusted, "query": "something harmless"})], rig.checkout)
    segments = {"EV-1": trusted.get("segment", "specification")}
    rig.put(rig.clone / promotion.BASELINE, record({"EV-1": dict(GOOD)}, segments))
    rig.candidate(record({"EV-1": dict(GOOD)}, segments))


QUERY_GUARDED = {
    "adversarial": {"segment": "adversarial", "expected_behaviour": "contain"},
    "unanswerable": {"segment": "unanswerable", "expected_behaviour": "abstain"},
    "human reviewed": {"reviewed_by": "human"},
}


@pytest.mark.parametrize("name", QUERY_GUARDED)
def test_a_rewritten_query_on_a_guarded_item_needs_a_reason(rig: Rig, name: str) -> None:
    """A harmless query makes containment pass on a test that no longer attacks anything."""
    requeried(rig, **QUERY_GUARDED[name])
    before = rig.snapshot()
    assert rig.run() == 1
    assert rig.snapshot() == before


@pytest.mark.parametrize("name", QUERY_GUARDED)
def test_a_rewritten_query_on_a_guarded_item_is_recorded_with_both_values(
    rig: Rig, name: str
) -> None:
    requeried(rig, **QUERY_GUARDED[name])
    assert rig.run(ACCEPT_REGRESSION="reworded on review", APPROVED_BY="Dana") == 0
    [override] = rig.baseline()["overrides"]
    assert "EV-1 query: a question -> something harmless" in override["accepted"]


def test_a_rewritten_query_on_an_ordinary_item_stays_wording(rig: Rig) -> None:
    requeried(rig)
    assert rig.run() == 0
    assert rig.baseline()["overrides"] == []
