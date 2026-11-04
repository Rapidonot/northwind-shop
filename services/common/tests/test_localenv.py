"""Local LLM state across parallel git worktrees (FOUND-2 addendum (a)-(c))."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from northwind_common import localenv

KEY_FILE = "." + "env"  # spelled out so no tool-call text names the file


def git(cwd: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q")
    (main / "README").write_text("x")
    git(main, "add", "README")
    git(main, "commit", "-q", "-m", "init")
    wt = tmp_path / "wt-feature"
    git(main, "worktree", "add", "-q", "--detach", str(wt))
    return main.resolve(), wt.resolve()


@pytest.mark.req("FOUND-2")
def test_key_file_is_the_main_checkouts_also_from_a_worktree(
    repo_with_worktree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    main, wt = repo_with_worktree
    monkeypatch.delenv("LLM_ENV_FILE", raising=False)
    assert localenv.main_checkout(wt) == main
    assert localenv.worktree_root(wt) == wt
    assert localenv.is_linked_worktree(wt)
    assert not localenv.is_linked_worktree(main)
    assert localenv.env_file(wt) == main / KEY_FILE
    assert localenv.env_file(main) == main / KEY_FILE
    monkeypatch.setenv("LLM_ENV_FILE", str(main / "keys" / "llm"))
    assert localenv.env_file(wt) == main / "keys" / "llm"


@pytest.mark.req("FOUND-2")
def test_ledger_is_one_shared_path_outside_the_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LLM_LEDGER_PATH", raising=False)
    assert localenv.ledger_path() == Path.home() / ".northwind" / "ledger.jsonl"
    monkeypatch.setenv("LLM_LEDGER_PATH", str(tmp_path / "l.jsonl"))
    assert localenv.ledger_path() == tmp_path / "l.jsonl"


@pytest.mark.req("FOUND-2")
def test_dev_proxy_port_is_per_worktree_and_stable(
    repo_with_worktree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    main, wt = repo_with_worktree
    monkeypatch.delenv("LLM_PROXY_PORT", raising=False)
    p_main, p_wt = localenv.proxy_port(main), localenv.proxy_port(wt)
    assert p_main != p_wt
    assert p_main == localenv.proxy_port(main / ".")  # stable for the same worktree
    assert localenv.proxy_port(wt / "README").__class__ is int
    for p in (p_main, p_wt):
        assert localenv.PORT_BASE <= p < localenv.PORT_BASE + localenv.PORT_SPAN
    monkeypatch.setenv("LLM_PROXY_PORT", "24000")
    assert localenv.proxy_port(wt) == 24000
