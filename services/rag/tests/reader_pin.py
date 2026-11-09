"""Who may read the index, scanned from the repository root (decision 0010; row #10, C1).

`context` is for ranking only. What holds a paraphrase finding at medium is that nothing but the
two readers below ever runs SQL against `rag_chunks`, and nothing dumps a whole `Chunk`. Every
check takes a root, so tests can plant a violation in a temporary tree and see it caught.

Paths are repo-relative and matched in full: a file named `store.py` elsewhere is not the store.
The source roots are found by a walk, not listed, so a new service is scanned with no edit here.
"""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RAG = "services/rag/src/northwind_rag"

# The only Python files that may name `rag_chunks` in SQL.
SQL_READERS = frozenset({f"{RAG}/retrieval.py", f"{RAG}/store.py"})
# The only other files that may, by full path: the migrations that exist today.
MIGRATIONS = frozenset(
    {
        "infra/db/migrations/0001_rag.sql",
        "infra/db/migrations/0002_rag_context.sql",
        "infra/db/migrations/0003_rag_fts.sql",
    }
)
# SELECT or WITH strings inside the readers that may name a context column, by (path, scope).
# `sync_chunks` diffs the index on `embed_hash`; `plain_chunk_rows` names `context` only to keep
# enriched rows out (`context IS NULL`). Neither returns context.
CONTEXT_SELECTS = frozenset(
    {(f"{RAG}/store.py", "sync_chunks"), (f"{RAG}/store.py", "_PLAIN_ROWS")}
)
# The index writers: the only files that may touch `.context`, `embed_text` or `embed_hash`.
CONTEXT_WRITERS = frozenset(
    {f"{RAG}/enrichment.py", f"{RAG}/models.py", f"{RAG}/sources.py", f"{RAG}/store.py"}
)
# The only modules that may import `Chunk`, besides `models.py`, which defines it.
CHUNK_IMPORTERS = frozenset(
    {
        f"{RAG}/chunking.py",
        f"{RAG}/embedding.py",
        f"{RAG}/enrichment.py",
        f"{RAG}/ingest.py",
        f"{RAG}/sources.py",
        f"{RAG}/store.py",
        "evals/src/northwind_evals/enrich.py",
    }
)
# What these return holds chunks (or is the index write), so it is never dumped whole.
CHUNK_PRODUCERS = frozenset({"build_chunks", "apply_cache", "sync_chunks"})
DUMPS = frozenset({"asdict", "astuple", "vars"})

PRUNED_NAMES = frozenset({".git", "node_modules", "__pycache__", "cassettes", "docs", "tests"})
PRUNED_PATHS = frozenset({"data/fixtures", "data/enrichment", "data/embeddings"})
SUFFIXES = frozenset({".py", ".sql", ".sh", ".yml", ".yaml"})
FILE_NAMES = frozenset({"Makefile"})

RAG_CHUNKS = re.compile(r"\brag_chunks\b", re.IGNORECASE)
SELECT_OR_WITH = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
CONTEXT_COLUMN = re.compile(r"\b(context|embed_text|embed_hash)\b", re.IGNORECASE)
CONTEXT_TOUCH = re.compile(r"\.context\b|\bembed_text\b|\bembed_hash\b|['\"]context['\"]")
CHUNKISH = re.compile(r"chunk", re.IGNORECASE)


def _pruned(rel: str, name: str) -> bool:
    return (
        name in PRUNED_NAMES
        or name.startswith(".venv")
        or (name.startswith(".") and name.endswith("cache"))
        or rel in PRUNED_PATHS
    )


def source_files(root: Path) -> list[str]:
    """Every scanned file under `root`, repo-relative, sorted."""
    found: list[str] = []
    for directory, subdirs, files in os.walk(root):
        base = Path(directory).relative_to(root)
        subdirs[:] = sorted(d for d in subdirs if not _pruned((base / d).as_posix(), d))
        for name in files:
            if name in FILE_NAMES or Path(name).suffix in SUFFIXES:
                found.append((base / name).as_posix())
    return sorted(found)


def python_files(root: Path) -> list[str]:
    return [rel for rel in source_files(root) if rel.endswith(".py")]


def _parse(root: Path, rel: str) -> ast.Module:
    return ast.parse((root / rel).read_text(encoding="utf-8"), filename=rel)


def _fold(node: ast.AST) -> str | None:
    """A string expression's literal text: constants, f-string literal parts (`{}` for each
    value) and constant `+`. None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_fold(v) or "{}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold(node.left), _fold(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def strings(tree: ast.Module) -> Iterator[tuple[str, str]]:
    """(scope, text) for every string expression; the scope is the enclosing function or class,
    or the module-level name assigned, else `<module>`."""

    def visit(node: ast.AST, scope: str) -> Iterator[tuple[str, str]]:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            scope = node.name
        text = _fold(node)
        if text is not None:
            yield scope, text
        for child in ast.iter_child_nodes(node):
            yield from visit(child, scope)

    for statement in tree.body:
        scope = "<module>"
        if isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name):
            scope = statement.targets[0].id
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            scope = statement.target.id
        yield from visit(statement, scope)


def raw_sql_readers(root: Path) -> set[str]:
    """Files outside the two readers and the migrations that name `rag_chunks`."""
    bad: set[str] = set()
    for rel in source_files(root):
        if rel.endswith(".py"):
            if rel in SQL_READERS:
                continue
            if any(RAG_CHUNKS.search(text) for _, text in strings(_parse(root, rel))):
                bad.add(rel)
        elif rel not in MIGRATIONS and RAG_CHUNKS.search(
            (root / rel).read_text(encoding="utf-8", errors="replace")
        ):
            bad.add(rel)
    return bad


def context_selects(root: Path) -> set[tuple[str, str]]:
    """SELECT or WITH strings in the readers that name a context column, by (path, scope)."""
    found: set[tuple[str, str]] = set()
    for rel in sorted(SQL_READERS):
        if not (root / rel).is_file():
            continue
        for scope, text in strings(_parse(root, rel)):
            if SELECT_OR_WITH.search(text) and CONTEXT_COLUMN.search(text):
                found.add((rel, scope))
    return found - CONTEXT_SELECTS


def _identifier(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _calls_producer(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call) and _identifier(n.func) in CHUNK_PRODUCERS for n in ast.walk(node)
    )


def _tainted(body: ast.AST) -> set[str]:
    """Names bound, inside one function (or module), to what a chunk producer returns, or to an
    item of it; found to a fixpoint."""
    names: set[str] = set()

    def holds_chunks(node: ast.AST) -> bool:
        return _calls_producer(node) or any(
            isinstance(n, ast.Name) and n.id in names for n in ast.walk(node)
        )

    def bound(target: ast.AST) -> set[str]:
        return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}

    while True:
        before = len(names)
        for node in ast.walk(body):
            if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign) and node.value:
                if holds_chunks(node.value):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        names |= bound(target)
            elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
                if holds_chunks(node.iter):
                    names |= bound(node.target)
        if len(names) == before:
            return names


def _scopes(tree: ast.Module) -> Iterator[ast.AST]:
    yield tree
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def chunk_dumps(root: Path) -> set[str]:
    """`asdict`, `astuple`, `vars` or `.__dict__` on a chunk, as `path:line`.

    A chunk is (a) anything named like one, (b) `self` inside `class Chunk`, or (c) anything a
    function binds from `build_chunks`, `apply_cache` or `sync_chunks`. Aliasing across functions
    is not followed (logged on row #10)."""
    bad: set[str] = set()
    for rel in python_files(root):
        tree = _parse(root, rel)
        chunk_selves: set[int] = set()
        for cls in ast.walk(tree):
            if isinstance(cls, ast.ClassDef) and cls.name == "Chunk":
                chunk_selves |= {
                    id(n) for n in ast.walk(cls) if isinstance(n, ast.Name) and n.id == "self"
                }
        for scope in _scopes(tree):
            tainted = _tainted(scope)

            def is_chunk(
                node: ast.AST, tainted: set[str] = tainted, selves: set[int] = chunk_selves
            ) -> bool:
                name = _identifier(node)
                return (
                    (name is not None and CHUNKISH.search(name) is not None)
                    or id(node) in selves
                    or (isinstance(node, ast.Name) and node.id in tainted)
                    or _calls_producer(node)
                )

            for node in ast.walk(scope):
                if (
                    isinstance(node, ast.Call)
                    and _identifier(node.func) in DUMPS
                    and node.args
                    and is_chunk(node.args[0])
                ) or (
                    isinstance(node, ast.Attribute)
                    and node.attr == "__dict__"
                    and is_chunk(node.value)
                ):
                    bad.add(f"{rel}:{node.lineno}")
    return bad


def chunk_importers(root: Path) -> set[str]:
    """Modules that import `Chunk` (or the models module, then use `.Chunk`) without being on
    the allow-list."""
    bad: set[str] = set()
    for rel in python_files(root):
        if rel == f"{RAG}/models.py" or rel in CHUNK_IMPORTERS:
            continue
        tree = _parse(root, rel)
        has_models = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = {a.name for a in node.names}
                if node.module == "northwind_rag.models" and names & {"Chunk", "*"}:
                    bad.add(rel)
                if node.module == "northwind_rag.models" or (
                    node.module == "northwind_rag" and "models" in names
                ):
                    has_models = True
            elif isinstance(node, ast.Import) and any(
                a.name == "northwind_rag.models" for a in node.names
            ):
                has_models = True
        if has_models and any(
            isinstance(n, ast.Attribute) and n.attr == "Chunk" for n in ast.walk(tree)
        ):
            bad.add(rel)
    return bad


def context_readers(root: Path) -> set[str]:
    """Python files outside the index writers that touch `.context`, `embed_text` or
    `embed_hash`."""
    return {
        rel
        for rel in python_files(root)
        if rel not in CONTEXT_WRITERS
        and CONTEXT_TOUCH.search((root / rel).read_text(encoding="utf-8"))
    }
