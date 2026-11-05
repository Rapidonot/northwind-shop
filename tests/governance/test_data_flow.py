"""NFR-07: the data-flow table stays current (GOV-1).

Fails when a directory under `services/` or a spending Makefile target has no row in
`docs/governance/data-flow.md`. Spending targets are derived from the Makefile, so a new one needs
no edit here. The check runs one way only: the table may name what is not on main yet, such as `make
enrich` from row #7.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("NFR-07")

REPO = Path(__file__).resolve().parents[2]
DATA_FLOW = REPO / "docs" / "governance" / "data-flow.md"
INVENTORY = "Services and spending commands"

# One or more target names, so `a b:` is one rule for both. An assignment (`X := y`) is not a rule.
RULE = re.compile(r"^((?:[A-Za-z0-9_.-]+[ \t]+)*[A-Za-z0-9_.-]+)[ \t]*:(?!=)(.*)$")
SUB_MAKE = re.compile(r"(?:\$\(MAKE\)|\bmake\b)(.*)")
# `runner-check` and `runner-sync` are not `runner`, so a word boundary is not enough.
SPENDING_RECIPE = re.compile(
    r"\bLLM_MODE=[\"']?(?:record|live)\b|\bnorthwind_llm_replay\.cli\s+(?:runner|serve)(?=\s|$)"
)
SPENDING_HELP = re.compile(r"\bspend(?:s|ing)\b", re.IGNORECASE)


def service_dirs(services: Path) -> set[str]:
    return {
        entry.name
        for entry in services.iterdir()
        if entry.is_dir() and not entry.name.startswith((".", "__"))
    }


def spending_targets(makefile: str) -> set[str]:
    """Targets that can reach a spending window: by recipe, by their own `##` help text, or through
    a target that does (a prerequisite, or a sub-make in the recipe)."""
    spends: set[str] = set()
    needs: dict[str, set[str]] = {}
    current: list[str] = []
    for line in makefile.splitlines():
        rule = RULE.match(line)
        if rule:
            current = rule.group(1).split()
            head, _, help_text = rule.group(2).partition("## ")
            prerequisites, _, inline = head.partition(";")
            reaches = {w for w in prerequisites.split() if not re.search(r"[=$%]", w)}
            reaches |= sub_make_targets(inline)
            hit = bool(SPENDING_HELP.search(help_text) or SPENDING_RECIPE.search(inline))
            for name in current:
                needs.setdefault(name, set()).update(reaches)
                if hit:
                    spends.add(name)
        elif line.startswith("\t") and current:
            for name in current:
                needs.setdefault(name, set()).update(sub_make_targets(line))
                if SPENDING_RECIPE.search(line):
                    spends.add(name)
    while grown := {n for n, deps in needs.items() if n not in spends and deps & spends}:
        spends |= grown
    return {target for target in spends if not target.startswith(".")}


def sub_make_targets(recipe: str) -> set[str]:
    """Targets named by `make ...` or `$(MAKE) ...` outside quotes (usage text may say `make x`)."""
    unquoted = re.sub(r'"[^"]*"|\'[^\']*\'', " ", recipe)
    found: set[str] = set()
    for call in SUB_MAKE.finditer(unquoted):
        words = re.split(r"[;&|)]", call.group(1))[0].split()
        found |= {w for w in words if not w.startswith(("-", "/", "~", "$")) and "=" not in w}
    return found


def inventory_entries(doc: str) -> set[str]:
    """Backticked text in the first column of the inventory table, and nowhere else."""
    section = re.search(rf"^## {re.escape(INVENTORY)}\n(.*?)(?=^## |\Z)", doc, re.M | re.S)
    assert section, f"data-flow.md has no `## {INVENTORY}` section"
    entries: set[str] = set()
    for line in section.group(1).splitlines():
        if line.startswith("|"):
            entries.update(re.findall(r"`([^`]+)`", line.split("|")[1]))
    return entries


def read_table() -> set[str]:
    assert DATA_FLOW.is_file(), "docs/governance/data-flow.md does not exist"
    return inventory_entries(DATA_FLOW.read_text())


def test_every_service_directory_has_a_row() -> None:
    wanted = {f"services/{name}/" for name in service_dirs(REPO / "services")}
    assert wanted - read_table() == set()


def test_every_spending_target_has_a_row() -> None:
    wanted = {f"make {name}" for name in spending_targets((REPO / "Makefile").read_text())}
    assert wanted - read_table() == set()


def test_the_makefile_parser_still_finds_the_known_spending_targets() -> None:
    """Without this floor, a parser that finds nothing would pass the check above."""
    found = spending_targets((REPO / "Makefile").read_text())
    assert {"record", "eval-live", "llm-runner"} <= found


def test_the_services_scan_still_finds_the_known_services() -> None:
    assert {"assistant", "common", "gateway", "llm-replay", "rag"} <= service_dirs(
        REPO / "services"
    )


@pytest.mark.parametrize(
    ("recipe", "spends"),
    [
        ("\tLLM_MODE=record uv run pytest", True),
        ("\tLLM_MODE=live uv run python -m northwind_evals.enrich", True),  # row #7's form
        ('\tLLM_MODE="live" uv run python -m x', True),
        ("\t$(RUNNER_PYTHON) -m northwind_llm_replay.cli runner --suite x", True),
        ("\tuv run python -m northwind_llm_replay.cli serve", True),
        ("\tLLM_MODE=replay uv run pytest", False),
        ("\tuv run python -m northwind_llm_replay.cli runner-check", False),
        ("\tuv run python -m northwind_llm_replay.cli spend", False),
    ],
)
def test_a_recipe_is_classified_by_what_it_runs(recipe: str, spends: bool) -> None:
    assert spending_targets(f"sample:\n{recipe}\n") == ({"sample"} if spends else set())


@pytest.mark.parametrize(
    ("rule", "spends"),
    [
        ("sample: ## Do a thing (spends API credit)", True),
        ("sample: ## Open one spending window", True),
        ("sample: ## LLM spend from the shared ledger", False),
        ("sample: ## Rebuild from recordings", False),
    ],
)
def test_help_text_can_mark_a_target_as_spending(rule: str, spends: bool) -> None:
    assert spending_targets(f"{rule}\n") == ({"sample"} if spends else set())


def test_an_assignment_or_special_target_is_not_a_spending_target() -> None:
    makefile = ".PHONY: record\n\tLLM_MODE=record\nRUNNER_DIR := LLM_MODE=record\n"
    assert spending_targets(makefile) == set()


def test_only_the_first_column_of_the_table_counts() -> None:
    doc = (
        f"`make record` in prose\n\n## {INVENTORY}\n\n"
        "| Entry | Notes |\n|---|---|\n| `services/rag/` | see `make eval-live` |\n\n"
        "## Later\n\n| `make llm-runner` | outside the section |\n"
    )
    assert inventory_entries(doc) == {"services/rag/"}


def test_a_longer_name_does_not_stand_in_for_a_target() -> None:
    doc = f"## {INVENTORY}\n\n| `make recording` | |\n"
    assert "make record" not in inventory_entries(doc)


def test_a_doc_with_no_inventory_section_fails() -> None:
    with pytest.raises(AssertionError, match="no `## Services and spending commands`"):
        inventory_entries("| `services/rag/` | |\n")


@pytest.mark.parametrize(
    ("makefile", "expected"),
    [
        # One rule, two targets: the recipe belongs to both, not to whatever rule came before.
        ("other:\n\ttrue\nrecord eval-live:\n\tLLM_MODE=record x\n", {"record", "eval-live"}),
        # The recipe on the rule line, after a semicolon.
        ("sample: ; LLM_MODE=record uv run pytest\n", {"sample"}),
        ("sample: dep ; uv run python -m northwind_llm_replay.cli runner\n", {"sample"}),
        # A target that needs a spending target spends when it runs.
        ("record:\n\tLLM_MODE=record x\nrelease: record\n\ttrue\n", {"record", "release"}),
        ("record:\n\tLLM_MODE=record x\nrelease: lint record\n\ttrue\n", {"record", "release"}),
        # A target that calls one through a sub-make spends too.
        ("record:\n\tLLM_MODE=record x\nrelease:\n\t$(MAKE) record\n", {"record", "release"}),
        (
            "record:\n\tLLM_MODE=record x\nrelease:\n\t@make -s record SUITE=x\n",
            {"record", "release"},
        ),
        # ...but a prerequisite or sub-make of a target that does not spend does not.
        ("lint:\n\ttrue\nrelease: lint\n\t$(MAKE) lint\n", set()),
    ],
)
def test_a_target_that_reaches_a_spending_target_is_one(makefile: str, expected: set[str]) -> None:
    assert spending_targets(makefile) == expected
