"""Row 12c: a refinement may change or drop only for an item whose saved context changed.

#32 re-records answers for items whose first-search context changed with a new DATA_VERSION, and
the answerer then replaces those items' refinements with the same prompt. The exemption is per
item: the item's `chunks` in `evals/contexts/dense-only.jsonl` must differ from the base both
committed at HEAD and on disk. Any other item stays add-only, and a deleted refinements file is
never exempt (12a's F2).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from northwind_evals.integrity import integrity_problems, replaced_refinements

pytestmark = pytest.mark.req("RAG-12")

BASELINE = Path("evals/baselines/dense-only.json")
CONTEXTS = Path("evals/contexts/dense-only.jsonl")
REFINEMENTS = Path("evals/contexts/refinements.ndjson")


def git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)  # noqa: S603, S607


def write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def commit(message: str) -> None:
    git("add", "-A")
    git("commit", "-q", "--allow-empty", "-m", message)


def baseline_with(prompt: str) -> str:
    answerer = f"assistant:v1:claude-sonnet-5-5:prompt-{prompt}:check-0cb61e75"
    return json.dumps({"overrides": [], "answerer_id": answerer})


def context(item_id: str, chunk_id: str, index_version: str = "v1") -> str:
    chunk = {"chunk_id": chunk_id, "source_id": "P1", "source_type": "product", "text": "Shell."}
    record = {
        "chunks": [chunk],
        "index_version": index_version,
        "item_id": item_id,
        "retriever_id": "dense-only",
    }
    return json.dumps(record, sort_keys=True)


def entry(item_id: str, n: int, text: str = "Great.") -> str:
    chunk = {"chunk_id": "R9#0", "source_id": "R9", "source_type": "review", "text": text}
    record = {"chunks": [chunk], "item_id": item_id, "n": n, "query": "q"}
    return json.dumps(record, sort_keys=True)


BASE_CONTEXTS = [context("EV-1", "product:P1:0"), context("EV-2", "product:P2:0")]
EV1_CHANGED = [context("EV-1", "product:P7:0", "v2"), context("EV-2", "product:P2:0", "v2")]
BASE_REFINEMENTS = [entry("EV-1", 1), entry("EV-1", 2), entry("EV-2", 1)]


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    write(BASELINE, [baseline_with("aaaaaaaa")])
    write(CONTEXTS, BASE_CONTEXTS)
    write(REFINEMENTS, BASE_REFINEMENTS)
    commit("base")
    git("switch", "-q", "-c", "feature")
    return tmp_path


def reported(replaced: list[dict[str, object]]) -> list[tuple[object, object, object]]:
    return [(r["item_id"], r["n"], r["reason"]) for r in replaced]


def test_t1_a_changed_context_lets_its_refinements_change_and_reports_each(repo: Path) -> None:
    write(CONTEXTS, EV1_CHANGED)
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-2", 1), entry("EV-3", 1)])
    commit("re-record EV-1 after its context changed")
    assert integrity_problems("main", BASELINE) == []
    replaced = replaced_refinements("main", BASELINE)
    assert reported(replaced) == [("EV-1", 1, "context"), ("EV-1", 2, "context")]
    changed, dropped = replaced
    assert len(changed["old"]) == 16 and changed["new"] not in ("-", changed["old"])
    assert dropped["new"] == "-"


def test_t2_an_unchanged_context_keeps_refinements_add_only(repo: Path) -> None:
    # A new index_version alone is not a changed context (D1): only `chunks` counts.
    write(CONTEXTS, [context("EV-1", "product:P1:0", "v2"), context("EV-2", "product:P2:0", "v2")])
    write(REFINEMENTS, [entry("EV-1", 1, "Tampered."), entry("EV-1", 2), entry("EV-2", 1)])
    commit("edit")
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (HEAD)" in problems
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (disk)" in problems
    assert replaced_refinements("main", BASELINE) == []


def test_t3_a_context_changed_on_disk_only_does_not_exempt(repo: Path) -> None:
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-1", 2), entry("EV-2", 1)])
    commit("refinement only")
    write(CONTEXTS, EV1_CHANGED)  # not committed
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (HEAD)" in problems
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (disk)" in problems
    assert replaced_refinements("main", BASELINE) == []


def test_t4_a_context_changed_at_head_only_does_not_exempt(repo: Path) -> None:
    write(CONTEXTS, EV1_CHANGED)
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-1", 2), entry("EV-2", 1)])
    commit("both")
    write(CONTEXTS, BASE_CONTEXTS)  # disk reverted, not committed
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (HEAD)" in problems
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (disk)" in problems
    assert replaced_refinements("main", BASELINE) == []


def test_t5_one_items_context_change_never_exempts_another(repo: Path) -> None:
    write(CONTEXTS, EV1_CHANGED)
    write(
        REFINEMENTS,
        [entry("EV-1", 1, "Re-recorded."), entry("EV-1", 2), entry("EV-2", 1, "Tampered.")],
    )
    commit("EV-1 context, EV-2 refinement")
    assert integrity_problems("main", BASELINE) == [
        f"{REFINEMENTS}: refinement EV-2#1 changed (HEAD)",
        f"{REFINEMENTS}: refinement EV-2#1 changed (disk)",
    ]
    assert reported(replaced_refinements("main", BASELINE)) == [("EV-1", 1, "context")]


def test_t6a_a_deleted_file_is_refused_under_the_answerer_exemption(repo: Path) -> None:
    write(BASELINE, [baseline_with("bbbbbbbb")])
    git("rm", "-q", str(REFINEMENTS))
    commit("promote and drop the file")
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: deleted (HEAD); refinements may only be added" in problems
    assert f"{REFINEMENTS}: deleted (disk); refinements may only be added" in problems


def test_t6b_a_deleted_file_is_refused_under_the_context_exemption(repo: Path) -> None:
    write(CONTEXTS, EV1_CHANGED)
    git("rm", "-q", str(REFINEMENTS))
    commit("context and drop the file")
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: deleted (HEAD); refinements may only be added" in problems
    assert f"{REFINEMENTS}: deleted (disk); refinements may only be added" in problems


def test_t7_a_deleted_contexts_file_exempts_nothing(repo: Path) -> None:
    git("rm", "-q", str(CONTEXTS))
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-1", 2), entry("EV-2", 1)])
    commit("drop contexts")
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (HEAD)" in problems
    assert replaced_refinements("main", BASELINE) == []


def test_t8_a_duplicate_context_item_is_refused(repo: Path) -> None:
    write(CONTEXTS, [*EV1_CHANGED, context("EV-1", "product:P1:0")])
    with pytest.raises(RuntimeError, match="twice"):
        integrity_problems("main", BASELINE)


def test_t9_the_answerer_exemption_reports_reason_answerer(repo: Path) -> None:
    # Both exemptions hold for EV-1: the broader one, "answerer", is reported (D2).
    write(BASELINE, [baseline_with("bbbbbbbb")])
    write(CONTEXTS, EV1_CHANGED)
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-2", 1, "Re-recorded.")])
    commit("promote a re-recorded answerer")
    assert integrity_problems("main", BASELINE) == []
    assert reported(replaced_refinements("main", BASELINE)) == [
        ("EV-1", 1, "answerer"),
        ("EV-1", 2, "answerer"),
        ("EV-2", 1, "answerer"),
    ]


def test_t10_cmd_integrity_prints_each_context_replaced_refinement(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse

    from northwind_evals import cli

    monkeypatch.setattr(cli, "added_frozen_vectors", lambda _commit: {})
    monkeypatch.setattr(cli, "added_known_failures", lambda _commit, _path: [])
    write(CONTEXTS, EV1_CHANGED)
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-1", 2), entry("EV-2", 1)])
    commit("re-record EV-1")
    assert cli.cmd_integrity(argparse.Namespace(base="main")) == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if "refinement replaced:" in line]
    assert len(lines) == 1
    assert '"item_id": "EV-1"' in lines[0] and '"reason": "context"' in lines[0]


def test_t11_same_chunks_with_new_index_and_retriever_ids_is_not_a_change(repo: Path) -> None:
    # D1: only `chunks` counts; a line that differs only in index_version and retriever_id is the
    # same context, so EV-1's refinements stay add-only.
    renamed = [
        json.dumps({**json.loads(line), "index_version": "v9", "retriever_id": "dense-v9"})
        for line in BASE_CONTEXTS
    ]
    write(CONTEXTS, renamed)
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-1", 2), entry("EV-2", 1)])
    commit("new index ids only")
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (HEAD)" in problems
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (disk)" in problems
    assert replaced_refinements("main", BASELINE) == []


def changed_set() -> set[str]:
    from northwind_evals.integrity import context_changed_items, merge_base

    return context_changed_items(merge_base("main"))


def test_t12_an_item_removed_from_the_contexts_is_not_exempt(repo: Path) -> None:
    write(CONTEXTS, [context("EV-2", "product:P2:0")])  # EV-1 gone at HEAD and on disk
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-1", 2), entry("EV-2", 1)])
    commit("EV-1 removed from contexts")
    assert changed_set() == set()
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (HEAD)" in problems
    assert replaced_refinements("main", BASELINE) == []


def test_t13_an_unreadable_contexts_line_raises(repo: Path) -> None:
    write(CONTEXTS, [*EV1_CHANGED, "{not json"])
    with pytest.raises(RuntimeError):
        integrity_problems("main", BASELINE)


def test_t14_a_base_without_a_contexts_file_keeps_refinements_add_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    write(BASELINE, [baseline_with("aaaaaaaa")])
    write(REFINEMENTS, BASE_REFINEMENTS)
    commit("base without contexts")
    git("switch", "-q", "-c", "feature")
    write(CONTEXTS, EV1_CHANGED)
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-1", 2), entry("EV-2", 1)])
    commit("add contexts and change a refinement")
    assert changed_set() == set()
    problems = integrity_problems("main", BASELINE)
    assert f"{REFINEMENTS}: refinement EV-1#1 changed (HEAD)" in problems
    assert replaced_refinements("main", BASELINE) == []


def test_t15_refinements_for_a_new_item_pass_and_are_not_reported(repo: Path) -> None:
    write(CONTEXTS, [*EV1_CHANGED, context("EV-3", "product:P3:0")])
    write(REFINEMENTS, [*BASE_REFINEMENTS, entry("EV-3", 1)])
    commit("new item with a refinement")
    assert integrity_problems("main", BASELINE) == []
    assert replaced_refinements("main", BASELINE) == []


def test_t16_a_chunk_text_change_with_the_same_chunk_ids_counts_as_changed(repo: Path) -> None:
    edited = json.loads(BASE_CONTEXTS[0])
    edited["chunks"][0]["text"] = "Different shell text."
    write(CONTEXTS, [json.dumps(edited, sort_keys=True), BASE_CONTEXTS[1]])
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-1", 2), entry("EV-2", 1)])
    commit("same chunk ids, new text")
    assert changed_set() == {"EV-1"}
    assert integrity_problems("main", BASELINE) == []
    assert reported(replaced_refinements("main", BASELINE)) == [("EV-1", 1, "context")]


def test_t17_head_and_disk_changed_differently_still_exempts(repo: Path) -> None:
    write(CONTEXTS, EV1_CHANGED)
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-1", 2), entry("EV-2", 1)])
    commit("EV-1 context changed")
    write(CONTEXTS, [context("EV-1", "product:P8:0", "v3"), context("EV-2", "product:P2:0")])
    assert changed_set() == {"EV-1"}
    assert integrity_problems("main", BASELINE) == []
    assert reported(replaced_refinements("main", BASELINE)) == [("EV-1", 1, "context")]


def test_t18_replaced_lists_only_exempt_items_while_problems_name_the_rest(repo: Path) -> None:
    write(CONTEXTS, EV1_CHANGED)
    write(REFINEMENTS, [entry("EV-1", 1, "Re-recorded."), entry("EV-1", 2), entry("EV-2", 2)])
    commit("EV-1 re-recorded, EV-2#1 dropped")
    assert integrity_problems("main", BASELINE) == [
        f"{REFINEMENTS}: refinement EV-2#1 removed (HEAD)",
        f"{REFINEMENTS}: refinement EV-2#1 removed (disk)",
    ]
    assert {r["item_id"] for r in replaced_refinements("main", BASELINE)} == {"EV-1"}


@pytest.mark.parametrize("brk", ["\n", "\r", "\u2028"], ids=["lf", "cr", "u2028"])
def test_t19_a_duplicate_context_id_is_quoted_so_it_cannot_pose_as_output(
    repo: Path, brk: str
) -> None:
    # Security review F3 (re-review L3: every line break, the whole message): the id is branch
    # text, and a line break in it must not start a new log line.
    forged = f"EV-1{brk}  eval-integrity passed against main"
    write(
        CONTEXTS, [*BASE_CONTEXTS, context(forged, "product:P1:0"), context(forged, "product:P2:0")]
    )
    with pytest.raises(RuntimeError) as raised:
        integrity_problems("main", BASELINE)
    assert str(raised.value) == f"{CONTEXTS} (disk) holds context {json.dumps(forged)} twice"
    assert brk not in str(raised.value)
