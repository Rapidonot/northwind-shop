"""NFR-09: every tool has a risk tier, a limit and an approver (GOV-1).

PRD §8 NFR-09: "Risk-tier every tool: read-only, reversible write, irreversible or monetary.
Monetary tools always need a deterministic limit and a confirmation or approval". The evidence is
`docs/governance/tool-register.md`. Columns are found from each table's header, because the
"Landed" and "Planned" tables carry an extra Row column. Tables with no Tier column (Open items)
are ignored. A limit of "values `unconfirmed`" is honest, so it is allowed: the PRD names those
limits but gives no value yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("NFR-09")

REPO = Path(__file__).resolve().parents[2]
REGISTER = REPO / "docs" / "governance" / "tool-register.md"

TIERS = ("read-only", "reversible write", "irreversible or monetary")
MONETARY = "irreversible or monetary"
REQUIRED_COLUMNS = ("Tier", "Limit", "Approver")
# Cells that say nothing. A bare "none" is allowed as an approver for a read-only tool only.
PLACEHOLDERS = {"", "-", "–", "—", "n/a", "na", "tbd", "todo", "?"}

# "None: local" is still no approver, on a monetary row.
NO_APPROVER = "none"

BACKTICKED = re.compile(r"`([^`]+)`")
SEPARATOR = re.compile(r"^\|?[\s:|-]+\|?$")


@dataclass(frozen=True)
class Row:
    line: int
    cells: dict[str, str]

    @property
    def names(self) -> set[str]:
        return set(BACKTICKED.findall(self.cells["Tool"]))


def split_cells(line: str) -> list[str]:
    body = line.strip()
    body = body.removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", body)]


def tables(doc: str) -> list[tuple[int, list[str], list[tuple[int, list[str]]]]]:
    """Every pipe table as (header line, header cells, [(line, cells), ...])."""
    found: list[tuple[int, list[str], list[tuple[int, list[str]]]]] = []
    block: list[tuple[int, str]] = []
    for number, line in enumerate([*doc.splitlines(), ""], start=1):
        if line.startswith("|"):
            block.append((number, line))
            continue
        if len(block) >= 2 and SEPARATOR.match(block[1][1]):
            header = split_cells(block[0][1])
            found.append((block[0][0], header, [(n, split_cells(text)) for n, text in block[2:]]))
        block = []
    return found


def register_rows(doc: str) -> tuple[list[Row], list[str]]:
    """Rows of every table that has a Tier column, plus problems with the tables themselves."""
    rows: list[Row] = []
    problems: list[str] = []
    for header_line, header, body in tables(doc):
        if "Tier" not in header:
            continue
        missing = [name for name in REQUIRED_COLUMNS if name not in header]
        if missing:
            problems.append(
                f"line {header_line}: table has Tier but no {', '.join(missing)} column"
            )
            continue
        for number, cells in body:
            if len(cells) != len(header):
                problems.append(f"line {number}: {len(cells)} cells under {len(header)} columns")
            padded = (cells + [""] * len(header))[: len(header)]
            rows.append(Row(number, {"Tool": padded[0], **dict(zip(header, padded, strict=True))}))
    return rows, problems


def plain(cell: str) -> str:
    return cell.replace("`", "").replace("*", "").strip().rstrip(".").strip().lower()


def row_problems(row: Row) -> list[str]:
    found: list[str] = []
    where = f"line {row.line}"
    tiers = BACKTICKED.findall(row.cells["Tier"])
    if len(tiers) != 1 or row.cells["Tier"].strip() != f"`{tiers[0]}`" or tiers[0] not in TIERS:
        found.append(f"{where}: tier {row.cells['Tier']!r} is not exactly one of {TIERS}")
    if plain(row.cells["Limit"]) in PLACEHOLDERS:
        found.append(f"{where}: empty limit")
    if plain(row.cells["Approver"]) in PLACEHOLDERS:
        found.append(f"{where}: empty approver")
    if tiers == [MONETARY]:
        if plain(row.cells["Limit"]) in PLACEHOLDERS | {"none"}:
            found.append(f"{where}: monetary tool with no limit")
        if plain(row.cells["Approver"]).startswith(NO_APPROVER):
            found.append(f"{where}: monetary tool with no approver")
        elif plain(row.cells["Approver"]) in PLACEHOLDERS:
            found.append(f"{where}: monetary tool with no approver")
    return found


def check(doc: str) -> list[str]:
    rows, problems = register_rows(doc)
    return problems + [p for row in rows for p in row_problems(row)]


def real_rows() -> list[Row]:
    assert REGISTER.is_file(), "docs/governance/tool-register.md does not exist"
    return register_rows(REGISTER.read_text())[0]


def tier_of(name: str) -> set[str]:
    return {
        BACKTICKED.findall(row.cells["Tier"])[0]
        for row in real_rows()
        if name in row.names and BACKTICKED.findall(row.cells["Tier"])
    }


# The real register.


def test_every_row_has_one_valid_tier_a_limit_and_an_approver() -> None:
    assert check(REGISTER.read_text()) == []


def test_every_monetary_row_has_a_real_limit() -> None:
    monetary = [r for r in real_rows() if MONETARY in r.cells["Tier"]]
    assert monetary, "no monetary rows found"
    bad = [r.line for r in monetary if plain(r.cells["Limit"]) in PLACEHOLDERS | {"none"}]
    assert bad == []


def test_every_monetary_row_has_an_approver() -> None:
    monetary = [r for r in real_rows() if MONETARY in r.cells["Tier"]]
    bad = [
        r.line
        for r in monetary
        if plain(r.cells["Approver"]) in PLACEHOLDERS
        or plain(r.cells["Approver"]).startswith(NO_APPROVER)
    ]
    assert bad == []


def test_the_parser_still_finds_the_register() -> None:
    """Without this floor, a parser that finds no rows would pass every check above."""
    rows = real_rows()
    assert len(rows) >= 30
    assert len({r.line for r in rows}) == len(rows)
    assert {t for r in rows for t in BACKTICKED.findall(r.cells["Tier"])} == set(TIERS)
    headers = [h for _, h, _ in tables(REGISTER.read_text()) if "Tier" in h]
    assert len(headers) >= 5
    assert any("Row" in h for h in headers), "the Landed and Planned tables carry a Row column"


def test_open_items_are_not_read_as_a_register_table() -> None:
    doc = REGISTER.read_text()
    open_items = [h for _, h, _ in tables(doc) if "Item" in h]
    assert open_items, "the Open items table was not found"
    assert all("Tier" not in h for h in open_items)


def test_retrieve_context_is_registered_as_read_only() -> None:
    assert tier_of("retrieve_context") == {"read-only"}


@pytest.mark.parametrize(
    "target",
    [
        "record",
        "eval-live",
        "enrich",
        "llm-runner",
        "llm-proxy",
        "eval-promote",
        "ship",
        "finish",
        "runner-sync",
    ],
)
def test_a_spending_or_merging_target_is_irreversible_or_monetary(target: str) -> None:
    assert tier_of(f"make {target}") == {MONETARY}


def makefile_targets(makefile: str) -> set[str]:
    rules = re.findall(r"^([a-zA-Z0-9_-]+(?: [a-zA-Z0-9_-]+)*):(?!=)", makefile, re.M)
    return {name for rule in rules for name in rule.split()}


def test_every_makefile_target_has_a_register_row() -> None:
    """Compares with the Makefile itself, so a new target cannot go unregistered."""
    targets = makefile_targets((REPO / "Makefile").read_text())
    assert {"record", "ship", "eval-promote", "lint"} <= targets, "the target scan found too little"
    listed = {name for row in real_rows() for name in row.names}
    assert {f"make {target}" for target in targets} - listed == set()


def test_the_tool_the_assistant_really_offers_has_a_register_row() -> None:
    """Compares with the code: every `TOOL_NAME` constant is a tool an agent can call."""
    source = REPO / "services/assistant/src/northwind_assistant/tools.py"
    tools = set(re.findall(r'^TOOL_NAME = "([^"]+)"$', source.read_text(), re.M))
    assert tools, "the tool scan found nothing"
    code = [path.read_text() for path in (REPO / "services").glob("*/src/**/*.py")]
    assert sum(text.count("@tool(") for text in code) == len(tools), "a tool has another name"
    # Only this table counts: a planned row naming the same tool says nothing about today.
    today = REGISTER.read_text().partition("## Agent tools today")[2].partition("\n## ")[0]
    assert tools - {name for row in register_rows(today)[0] for name in row.names} == set()


def test_a_target_scan_sees_rules_and_skips_assignments() -> None:
    assert makefile_targets("a-b: x\nVAR := y\n.PHONY: z\n\ttab: no\nc:\n") == {"a-b", "c"}


def test_a_rule_with_several_names_gives_each_one() -> None:
    assert makefile_targets("one two_x: dep\n") == {"one", "two_x"}


@pytest.mark.parametrize("path", ["evals/baselines/", "evals/fixtures/known_failures.json"])
def test_editing_a_gate_input_keeps_the_tier_of_a_promotion(path: str) -> None:
    assert tier_of(path) == {MONETARY}


def test_none_with_a_note_is_still_no_approver_on_a_monetary_row() -> None:
    row = "| `a` | `irreversible or monetary` | One call | None: local | x |"
    assert any("no approver" in problem for problem in check(sample(row)))


# The checker itself, on sample text.

HEAD = "| Tool | Tier | Limit | Approver | Enforced at |\n|---|---|---|---|---|\n"


def sample(row: str, head: str = HEAD) -> str:
    return f"## Sample\n\n{head}{row}\n"


GOOD = "| `a` | `read-only` | Own rows | None needed: read-only | x |"


def test_a_good_row_passes() -> None:
    assert check(sample(GOOD)) == []


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ("| `a` | `read-only` |  | None | x |", "empty limit"),
        ("| `a` | `read-only` | - | None | x |", "empty limit"),
        ("| `a` | `read-only` | Own rows |  | x |", "empty approver"),
        ("| `a` | `readonly` | Own rows | None | x |", "is not exactly one of"),
        ("| `a` | read-only | Own rows | None | x |", "is not exactly one of"),
        ("| `a` | `read-only` `reversible write` | Own rows | None | x |", "is not exactly one of"),
        ("| `a` | `read-only`, mostly | Own rows | None | x |", "is not exactly one of"),
        ("|  `a` |  | Own rows | None | x |", "is not exactly one of"),
        ("| `a` | `irreversible or monetary` | none | The user | x |", "no limit"),
        ("| `a` | `irreversible or monetary` | **None.** | The user | x |", "no limit"),
        ("| `a` | `irreversible or monetary` |  | The user | x |", "no limit"),
        ("| `a` | `irreversible or monetary` | 5 a day | none | x |", "no approver"),
        ("| `a` | `irreversible or monetary` | 5 a day |", "cells under"),
    ],
)
def test_a_bad_row_is_caught(row: str, expected: str) -> None:
    assert any(expected in p for p in check(sample(row)))


def test_a_monetary_row_with_a_real_limit_and_approver_passes() -> None:
    row = "| `a` | `irreversible or monetary` | 5 a day | The shopper | x |"
    assert check(sample(row)) == []


def test_an_unconfirmed_limit_is_honest_and_allowed() -> None:
    limit = "Spend ceiling, values `unconfirmed`"
    row = f"| `a` | `irreversible or monetary` | {limit} | The shopper | x |"
    assert check(sample(row)) == []


def test_columns_are_found_from_the_header_not_by_position() -> None:
    head = "| Tool | Row | Tier | Limit | Approver | Enforced |\n|---|---|---|---|---|---|\n"
    assert check(sample("| `a` | #1 | `reversible write` | Cap | The user | x |", head)) == []
    bad = check(sample("| `a` | #1 | `reversible write` |  | The user | x |", head))
    assert any("empty limit" in p for p in bad)
    # With the extra column, "Row" must not be mistaken for the tier.
    bad = check(sample("| `a` | `read-only` | `reversible write` | Cap | The user | x |", head))
    assert bad == []


def test_a_table_with_a_tier_column_but_no_limit_column_is_caught() -> None:
    head = "| Tool | Tier | Approver |\n|---|---|---|\n"
    assert any("no Limit" in p for p in check(sample("| `a` | `read-only` | None |", head)))


def test_a_table_with_no_tier_column_is_ignored() -> None:
    doc = "## Open items\n\n| Item | Owner |\n|---|---|\n| nothing here | |\n"
    assert register_rows(doc) == ([], [])
    assert check(doc) == []


def test_a_doc_with_no_register_table_yields_no_rows() -> None:
    """The real-register floor depends on this: an empty parse is visible, not a pass."""
    assert register_rows("# Tool register\n\nNo tables.\n")[0] == []
