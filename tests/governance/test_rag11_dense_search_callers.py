"""RAG-11 (row #9, the user's Q1): `dense_search` stays as it is, so pin how it is called.

`dense_search(..., audience="shopper", region=None)` returns every region's chunks: the RAG-12
dense-only comparison arm. Only tests may call it that way. Outside tests, a call that is not
literally `audience="staff"` must pass a `region` that is not the literal `None`, and may not pass
`**kwargs`, which would hide either argument. Aliased imports and attribute calls are followed.

The walk covers every Python file in the repository outside tests, virtualenvs and node_modules,
so a new service is covered the day it lands. The passing run must find the one real caller today,
`evals/src/northwind_evals/cli.py` (`region=_region(item)`), so it cannot pass by finding nothing.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("RAG-11")

REPO = Path(__file__).resolve().parents[2]
SKIPPED_PARTS = {".venv", "node_modules", ".git", "__pycache__", "tests", ".llm-proxy-log"}
NAME = "dense_search"
KNOWN_CALLER = "evals/src/northwind_evals/cli.py"
RETRIEVAL = "services/rag/src/northwind_rag/retrieval.py"
# The private search helpers take `regions` directly and skip `_regions` (review pass 1, F6).
PRIVATE = {"_dense", "_keyword", "_hybrid"}


@dataclass(frozen=True)
class Call:
    path: str
    line: int
    problem: str | None


def source_files(root: Path) -> list[Path]:
    """Pruned while walking, so node_modules and virtualenvs are never read."""
    found: list[Path] = []
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_PARTS and not d.startswith(".venv"))
        found += [
            Path(here) / name
            for name in sorted(files)
            if name.endswith(".py") and not name.startswith("test_") and name != "conftest.py"
        ]
    return found


def aliases(tree: ast.AST) -> set[str]:
    """Local names bound to `dense_search` by an import, and the bare name itself."""
    names: set[str] = {NAME}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {a.asname or a.name for a in node.names if a.name == NAME}
    return names


def is_dense_search(func: ast.expr, local: set[str]) -> bool:
    if isinstance(func, ast.Name):
        return func.id in local
    return isinstance(func, ast.Attribute) and func.attr == NAME


def problem_of(call: ast.Call) -> str | None:
    keywords = {k.arg: k.value for k in call.keywords if k.arg is not None}
    if any(k.arg is None for k in call.keywords):
        return "passes **kwargs, which can hide audience or region"
    audience = keywords.get("audience")
    if isinstance(audience, ast.Constant) and audience.value == "staff":
        return None
    region = keywords.get("region")
    if region is None:
        return "a shopper (or unknown) audience with no region"
    if isinstance(region, ast.Constant) and region.value is None:
        return "a shopper (or unknown) audience with region=None"
    return None


def other_problems(tree: ast.AST, local: set[str], rel: str) -> list[Call]:
    """References that are not a plain call: a star import, the function passed or rebound
    (`f = dense_search`, `partial(dense_search, ...)`), `getattr(..., "dense_search")`, and a call
    to a private helper outside `retrieval.py`."""
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    found: list[Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            if node.module and node.module.startswith("northwind_rag"):
                found.append(Call(rel, node.lineno, "a star import from northwind_rag"))
        elif isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in called:
            name = node.id if isinstance(node, ast.Name) else node.attr
            if (name in local or name == NAME) and not isinstance(node.ctx, ast.Store):
                found.append(Call(rel, node.lineno, "dense_search used other than in a call"))
            elif name in PRIVATE and rel != RETRIEVAL and not isinstance(node.ctx, ast.Store):
                found.append(Call(rel, node.lineno, f"{name} used outside retrieval"))
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and any(isinstance(a, ast.Constant) and a.value == NAME for a in node.args)
            ):
                found.append(Call(rel, node.lineno, "getattr reaches dense_search"))
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in PRIVATE and rel != RETRIEVAL:
                found.append(Call(rel, node.lineno, f"calls the private {name} outside retrieval"))
    return found


def dense_search_calls(root: Path) -> list[Call]:
    found: list[Call] = []
    for path in source_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        local = aliases(tree)
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and is_dense_search(node.func, local):
                found.append(Call(rel, node.lineno, problem_of(node)))
        found += other_problems(tree, local, rel)
    return found


def test_no_non_test_code_calls_dense_search_as_a_shopper_without_a_region(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = dense_search_calls(REPO)
    with capsys.disabled():
        print("\nRAG-11 D4 dense_search callers outside tests:")
        for call in calls:
            print(f"  {call.path}:{call.line} {call.problem or 'ok'}")
    assert KNOWN_CALLER in {call.path for call in calls}, "the walk found no real caller"
    assert [c for c in calls if c.problem] == []


# ---- the pin catches each form (planted in a temporary tree) ----------------------------------


def plant(root: Path, code: str, where: str = "services/new/src/newsvc/mod.py") -> Path:
    path = root / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("form", "code"),
    [
        (
            "no region",
            "from northwind_rag.retrieval import dense_search\n"
            "dense_search(c, e, q, audience='shopper', k=5)\n",
        ),
        (
            "region=None",
            "from northwind_rag.retrieval import dense_search\n"
            "dense_search(c, e, q, audience='shopper', k=5, region=None)\n",
        ),
        (
            "**kwargs",
            "from northwind_rag.retrieval import dense_search\ndense_search(c, e, q, **options)\n",
        ),
        (
            "**kwargs beside a region",
            "from northwind_rag.retrieval import dense_search\n"
            "dense_search(c, e, q, region='US', **options)\n",
        ),
        (
            "an import alias",
            "from northwind_rag.retrieval import dense_search as ds\n"
            "ds(c, e, q, audience='shopper', k=5)\n",
        ),
        (
            "an attribute call",
            "from northwind_rag import retrieval\n"
            "retrieval.dense_search(c, e, q, audience='shopper', k=5)\n",
        ),
        (
            "a bare name with no import",
            "dense_search(c, e, q, audience='shopper', k=5)\n",
        ),
        (
            "a non-literal audience with no region",
            "from northwind_rag.retrieval import dense_search\n"
            "dense_search(c, e, q, audience=who, k=5)\n",
        ),
    ],
)
def test_the_pin_flags_a_planted_call(tmp_path: Path, form: str, code: str) -> None:
    plant(tmp_path, code)
    [call] = dense_search_calls(tmp_path)
    assert call.problem, form


@pytest.mark.parametrize(
    "code",
    [
        "from northwind_rag.retrieval import dense_search\n"
        "dense_search(c, e, q, audience='shopper', k=5, region=item.region)\n",
        "from northwind_rag.retrieval import dense_search\n"
        "dense_search(c, e, q, audience='staff', k=5)\n",
    ],
)
def test_the_pin_allows_a_region_or_the_staff_audience(tmp_path: Path, code: str) -> None:
    plant(tmp_path, code)
    [call] = dense_search_calls(tmp_path)
    assert call.problem is None


@pytest.mark.parametrize(
    ("form", "code"),
    [
        ("a star import", "from northwind_rag.retrieval import *\n"),
        (
            "rebinding",
            "from northwind_rag.retrieval import dense_search\nf = dense_search\nf(c, e, q)\n",
        ),
        (
            "functools.partial",
            "import functools\nfrom northwind_rag.retrieval import dense_search\n"
            "f = functools.partial(dense_search, audience='shopper')\n",
        ),
        (
            "getattr",
            "from northwind_rag import retrieval\n"
            "getattr(retrieval, 'dense_search')(c, e, q, audience='shopper', k=5)\n",
        ),
        (
            "a private helper",
            "from northwind_rag.retrieval import _dense\n"
            "_dense(c, e, q, audience='shopper', regions=None, k=5)\n",
        ),
        (
            "a private helper passed to partial",
            "import functools\nfrom northwind_rag.retrieval import _dense\n"
            "f = functools.partial(_dense, regions=None)\n",
        ),
        (
            "a private helper rebound",
            "from northwind_rag import retrieval\nf = retrieval._hybrid\n",
        ),
        (
            "a private helper as an attribute",
            "from northwind_rag import retrieval\nretrieval._hybrid(c, e, q, regions=None, k=5)\n",
        ),
    ],
)
def test_the_pin_flags_a_reference_that_is_not_a_plain_call(
    tmp_path: Path, form: str, code: str
) -> None:
    plant(tmp_path, code)
    assert [c for c in dense_search_calls(tmp_path) if c.problem], form


def test_retrieval_itself_may_call_its_private_helpers(tmp_path: Path) -> None:
    plant(tmp_path, "_dense(c, e, q, regions=None, k=5)\n", RETRIEVAL)
    assert [c for c in dense_search_calls(tmp_path) if c.problem] == []


def test_test_files_are_not_walked(tmp_path: Path) -> None:
    code = (
        "from northwind_rag.retrieval import dense_search\n"
        "dense_search(c, e, q, audience='shopper', k=5)\n"
    )
    plant(tmp_path, code, "services/new/tests/test_mod.py")
    plant(tmp_path, code, "services/new/src/newsvc/test_helper.py")
    plant(tmp_path, code, "services/new/conftest.py")
    assert dense_search_calls(tmp_path) == []
