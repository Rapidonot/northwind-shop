"""NFR-07: every repo path cited in docs/governance exists (GOV-1).

A citation that no longer resolves is a fact nobody can check. Text not on main cannot pass as a
repo path, so it is written another way: `req/<branch>:<path>` for a branch, a bare name such as
`mcp-rag` for something planned. `path.py::test_name` also checks the test still exists.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("NFR-07")

REPO = Path(__file__).resolve().parents[2]
GOVERNANCE = REPO / "docs" / "governance"
DOCS = ("threat-model.md", "tool-register.md", "data-flow.md")

BACKTICKED = re.compile(r"`([^`\n]+)`")
TOP_LEVEL = (
    r"\.github|\.claude|agents-external|apps|cassettes|data|docs|evals|infra|partner-agent"
    r"|services|tests|tools"
)
# Paths outside backticks: prose must not be a way around the check.
BARE = re.compile(rf"(?<![\w/.:`-])((?:{TOP_LEVEL})/[\w./*-]*[\w/*])")
LINK = re.compile(r"\]\((?!https?:|#)([^)\s]+)\)")
# Reference-style definitions, `[label]: path`, are links too.
REFERENCE_LINK = re.compile(r"^ {0,3}\[[^\]\n]+\]:[ \t]*<?(?!https?:|#)([^>\s]+)", re.M)

FILE_SUFFIXES = (".json", ".jsonl", ".lock", ".md", ".pdf", ".py", ".sh", ".sql", ".toml", ".yml")
FILE_NAMES = ("Makefile", "env.example", ".gitignore", ".gitattributes", ".python-version")
# Backticked text with a slash that is not a path in this repo.
NOT_REPO_PATHS = ("/", "~", "$", "http://", "https://", "req/", "origin/", "refs/", "settings/")


def is_repo_path(token: str) -> bool:
    if any(char.isspace() for char in token) or token.startswith(NOT_REPO_PATHS):
        return False
    path = token.partition("::")[0]
    return "/" in path or path.endswith(FILE_SUFFIXES) or path in FILE_NAMES


def cited_paths(text: str) -> set[str]:
    backticked = {token for token in BACKTICKED.findall(text) if is_repo_path(token)}
    return backticked | set(BARE.findall(text))


def linked_paths(text: str, doc_dir: Path) -> set[str]:
    """Markdown links, as repo-relative paths, so a link cannot dodge the check."""
    found = set()
    for target in [*LINK.findall(text), *REFERENCE_LINK.findall(text)]:
        resolved = (doc_dir / target.partition("#")[0]).resolve()
        found.add(str(resolved.relative_to(REPO)) if resolved.is_relative_to(REPO) else target)
    return found


def exists_with_case(repo: Path, path: str) -> bool:
    """Exists with exactly this spelling: macOS finds `Docs/A.md`, Linux CI does not."""
    here = repo
    for part in Path(os.path.normpath(path)).parts:
        if part != "." and (not here.is_dir() or part not in {e.name for e in here.iterdir()}):
            if part != "..":
                return False
        here = here / part
    return True


def problem(citation: str, repo: Path) -> str | None:
    path, _, test = citation.partition("::")
    target = repo / path
    if not target.resolve().is_relative_to(repo.resolve()):
        return f"{citation}: outside the repository"
    if "*" in path:
        return None if next(repo.glob(path), None) else f"{citation}: matches nothing"
    if not exists_with_case(repo, path):
        return f"{citation}: does not exist"
    if test:
        name = test.rpartition("::")[2].partition("[")[0]
        if not re.search(rf"\bdef {re.escape(name)}\b", target.read_text()):
            return f"{citation}: no such test"
    return None


def doc_citations(name: str) -> set[str]:
    doc = GOVERNANCE / name
    assert doc.is_file(), f"docs/governance/{name} does not exist"
    text = doc.read_text()
    return cited_paths(text) | linked_paths(text, doc.parent)


@pytest.mark.parametrize("name", DOCS)
def test_every_cited_path_exists(name: str) -> None:
    problems = sorted(filter(None, (problem(c, REPO) for c in doc_citations(name))))
    assert problems == []


@pytest.mark.parametrize("name", DOCS)
def test_each_doc_cites_the_repo(name: str) -> None:
    """Without this floor, a parser that finds nothing would pass the check above."""
    assert len(doc_citations(name)) >= 10


def test_every_markdown_file_in_the_folder_is_checked() -> None:
    assert {doc.name for doc in GOVERNANCE.glob("*.md")} == set(DOCS)


@pytest.mark.parametrize(
    "token",
    [
        "docs/PRD.md",
        "docs/reviews/*",
        "Makefile",
        "PROGRESS.md",
        "service/rag/typo.py",
        "tests/hooks/test_settings.py::test_x",
        "../outside.md",
    ],
)
def test_what_counts_as_a_repo_path(token: str) -> None:
    assert cited_paths(f"see `{token}` here") == {token}


@pytest.mark.parametrize(
    "token",
    [
        "req/RAG-02-enrichment:data/enrichment/internal.jsonl",
        "origin/main",
        "~/.northwind/ledger.jsonl",
        "/api/chat",
        "https://genai.owasp.org/llm-top-10/",
        "make -C ~/.northwind/runner ship",
        "POST /v1/messages",
        "northwind_common.llm",
        "mcp-rag",
    ],
)
def test_what_does_not_count_as_a_repo_path(token: str) -> None:
    assert cited_paths(f"see `{token}` here") == set()


def test_a_path_in_prose_is_checked_too() -> None:
    assert cited_paths("see docs/reviews/gone.md, then stop.") == {"docs/reviews/gone.md"}


def test_a_markdown_link_is_checked_too() -> None:
    assert linked_paths("[gone](../reviews/gone.md)", GOVERNANCE) == {"docs/reviews/gone.md"}


def test_a_link_to_a_web_page_is_left_alone() -> None:
    assert linked_paths("[owasp](https://genai.owasp.org/)", GOVERNANCE) == set()


def test_a_missing_file_is_reported(tmp_path: Path) -> None:
    assert problem("docs/gone.md", tmp_path) == "docs/gone.md: does not exist"


def test_a_path_out_of_the_repository_is_reported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside.md").write_text("x")
    assert problem("../outside.md", repo) == "../outside.md: outside the repository"


def test_a_glob_that_matches_nothing_is_reported(tmp_path: Path) -> None:
    assert problem("docs/*.md", tmp_path) == "docs/*.md: matches nothing"


def test_a_cited_test_must_exist(tmp_path: Path) -> None:
    (tmp_path / "t.py").write_text("def test_here() -> None: ...\n")
    assert problem("t.py::test_here", tmp_path) is None
    assert problem("t.py::Group::test_here[case]", tmp_path) is None
    assert problem("t.py::test_gone", tmp_path) == "t.py::test_gone: no such test"


@pytest.mark.parametrize("token", [".gitignore", ".gitattributes", ".python-version"])
def test_a_root_dotfile_counts_as_a_repo_path(token: str) -> None:
    assert cited_paths(f"see `{token}` here") == {token}


def test_a_cited_path_must_match_in_case(tmp_path: Path) -> None:
    """macOS resolves `Docs/A.md` to `docs/a.md`; the Linux CI runner does not."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("x")
    assert problem("docs/a.md", tmp_path) is None
    assert problem("Docs/a.md", tmp_path) == "Docs/a.md: does not exist"
    assert problem("docs/A.md", tmp_path) == "docs/A.md: does not exist"


def test_a_cited_test_in_a_path_with_the_wrong_case_is_reported(tmp_path: Path) -> None:
    (tmp_path / "t.py").write_text("def test_here() -> None: ...\n")
    assert problem("T.py::test_here", tmp_path) == "T.py::test_here: does not exist"


def test_a_reference_style_link_is_checked_too() -> None:
    assert linked_paths("[gone]: ../reviews/gone.md", GOVERNANCE) == {"docs/reviews/gone.md"}


def test_a_path_in_a_top_level_directory_not_yet_on_main_is_checked_in_prose() -> None:
    assert cited_paths("see partner-agent/src/gone.py now") == {"partner-agent/src/gone.py"}
    assert cited_paths("see agents-external/payment/gone.py now") == {
        "agents-external/payment/gone.py"
    }
