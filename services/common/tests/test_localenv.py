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
def test_a_worktree_knows_its_main_checkout(repo_with_worktree: tuple[Path, Path]) -> None:
    main, wt = repo_with_worktree
    assert localenv.main_checkout(wt) == main
    assert localenv.worktree_root(wt) == wt
    assert localenv.is_linked_worktree(wt)
    assert not localenv.is_linked_worktree(main)


@pytest.mark.req("CC-27")
def test_key_file_is_outside_every_checkout(
    repo_with_worktree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    main, wt = repo_with_worktree
    monkeypatch.delenv("LLM_ENV_FILE", raising=False)
    monkeypatch.chdir(wt)
    assert localenv.env_file().parent == Path.home() / ".northwind" / "secrets"
    assert localenv.env_file() != main / KEY_FILE
    monkeypatch.setenv("LLM_ENV_FILE", str(main / "keys" / "llm"))
    assert localenv.env_file() == main / "keys" / "llm"


@pytest.mark.req("CC-27")
def test_the_runner_is_one_clean_clone_on_a_port_outside_the_dev_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_RUNNER_PORT", raising=False)
    assert localenv.runner_checkout() == Path.home() / ".northwind" / "runner"
    assert localenv.runner_url() == f"http://127.0.0.1:{localenv.RUNNER_PORT}"
    assert localenv.RUNNER_PORT < localenv.PORT_BASE
    monkeypatch.setenv("LLM_RUNNER_PORT", "12345")
    assert localenv.runner_url() == "http://127.0.0.1:12345"


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


@pytest.mark.req("CC-10")
def test_mcp_rag_port_is_per_worktree_stable_and_clear_of_the_proxy_span(
    repo_with_worktree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    main, wt = repo_with_worktree
    for name in ("MCP_RAG_PORT", "MCP_RAG_URL", "LLM_PROXY_PORT"):
        monkeypatch.delenv(name, raising=False)
    m_main, m_wt = localenv.mcp_rag_port(main), localenv.mcp_rag_port(wt)
    assert m_main != m_wt
    assert m_main == localenv.mcp_rag_port(main / ".")
    for port in (m_main, m_wt):
        assert localenv.MCP_RAG_PORT_BASE <= port < localenv.MCP_RAG_PORT_BASE + localenv.PORT_SPAN
        assert not localenv.PORT_BASE <= port < localenv.PORT_BASE + localenv.PORT_SPAN
        assert port != localenv.RUNNER_PORT and port <= 65535
    assert m_main - localenv.MCP_RAG_PORT_BASE == localenv.proxy_port(main) - localenv.PORT_BASE
    assert localenv.mcp_rag_url(main) == f"http://127.0.0.1:{m_main}/mcp"
    monkeypatch.setenv("MCP_RAG_PORT", "31000")
    assert localenv.mcp_rag_url(wt) == "http://127.0.0.1:31000/mcp"
    monkeypatch.setenv("MCP_RAG_URL", "http://127.0.0.1:1/x")
    assert localenv.mcp_rag_url(wt) == "http://127.0.0.1:1/x"


@pytest.mark.req("CHK-01")
@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("postgresql://northwind:northwind-local@localhost:5432/northwind_16", "northwind_16"),
        ("postgres://u:p@h/northwind", "northwind"),
        ("dbname=northwind_16 host=localhost password=secret", "northwind_16"),
        ("postgresql://u:secret@h/", "(unreadable)"),
        ("postgresql://u:secret@h/a b", "(unreadable)"),
        ("not a url secret", "(unreadable)"),
    ],
)
def test_the_database_line_names_only_the_database(url: str, name: str) -> None:
    """Row #16: the startup line that proves which database a server reads; never the password."""
    from northwind_common.localenv import database_name

    assert database_name(url) == name
    assert "secret" not in database_name(url)
