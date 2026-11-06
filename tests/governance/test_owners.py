"""NFR-07: every gap in docs/governance has an owner, and landed work reads as landed (GOV-1).

The `Owners` table in `threat-model.md` holds what no `PROGRESS.md` row named. An owner is a real
row, a Window B NFR, AUTO-2, or `needs the user`, each with a reason. These tests catch a made-up
owner, a reason left blank, and a cell still calling 5b or #7 in flight. They cannot tell whether an
owner is the right one, and they do not read the row's own text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("NFR-07")

REPO = Path(__file__).resolve().parents[2]
GOVERNANCE = REPO / "docs" / "governance"
DOCS = ("threat-model.md", "tool-register.md", "data-flow.md")
THREAT_MODEL = GOVERNANCE / "threat-model.md"

MERGED_BRANCHES = ("req/RAG-02-enrichment", "req/5b-promotion-control")
NO_OWNER = re.compile(r"no row owns", re.IGNORECASE)
BASELINE = re.compile(r"\*\*Baseline:\*\* main at `([0-9a-f]{7,40})`")
# `#7`, `#7's` and `5b`, but not `#70` or `5bx`.
LANDED_ROW = re.compile(r"(?<![\w#])(?:#7|5b)(?![\w])")

ROW_OWNER = re.compile(r"#(\w+)")
NFR_OWNER = re.compile(r"Window B P6, (NFR-\d\d)")
NEEDS_THE_USER = "needs the user"
AUTO_2 = "AUTO-2"


def owner_rows(doc: str) -> list[tuple[str, str, str]]:
    """(item, owner, why) from the table under `## Owners`, and nowhere else."""
    section = re.search(r"^## Owners\n(.*?)(?=^## |\Z)", doc, re.M | re.S)
    assert section, "threat-model.md has no `## Owners` section"
    lines = [line for line in section.group(1).splitlines() if line.startswith("|")]
    assert lines[:1] == ["| Item | Owner | Why |"], "the Owners table has the wrong columns"
    cells = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:]]
    assert all(len(row) == 3 for row in cells), "an Owners row does not have three cells"
    return [(item, owner, why) for item, owner, why in cells]


def progress_rows(progress: str) -> set[str]:
    return set(re.findall(r"^\| (\w+) \| ", progress, re.M))


def known_nfrs(prd: str) -> set[str]:
    return set(re.findall(r"^\| (NFR-\d\d) \|", prd, re.M))


def owner_problem(owner: str, rows: set[str], nfrs: set[str]) -> str | None:
    if owner in (NEEDS_THE_USER, AUTO_2):
        return None
    row = ROW_OWNER.fullmatch(owner)
    if row:
        return None if row.group(1) in rows else f"{owner}: no such row in PROGRESS.md"
    nfr = NFR_OWNER.fullmatch(owner)
    if nfr:
        return None if nfr.group(1) in nfrs else f"{owner}: no such NFR in the PRD"
    return f"{owner!r}: not a row, a Window B NFR, AUTO-2 or {NEEDS_THE_USER!r}"


def stale_lines(doc: str) -> list[str]:
    """Lines calling a row that has landed in flight."""
    return [
        line for line in doc.splitlines() if "in flight" in line.lower() and LANDED_ROW.search(line)
    ]


@pytest.fixture(scope="module")
def rows() -> list[tuple[str, str, str]]:
    return owner_rows(THREAT_MODEL.read_text())


@pytest.mark.parametrize("name", DOCS)
def test_no_doc_leaves_a_gap_without_an_owner(name: str) -> None:
    assert NO_OWNER.findall((GOVERNANCE / name).read_text()) == []


def test_every_owner_is_real(rows: list[tuple[str, str, str]]) -> None:
    progress = (REPO / "PROGRESS.md").read_text()
    assert "| A2 | AUTO-2 " in progress, "AUTO-2 is no longer row A2"
    known_rows = progress_rows(progress)
    nfrs = known_nfrs((REPO / "docs" / "PRD.md").read_text())
    problems = [owner_problem(owner, known_rows, nfrs) for _, owner, _ in rows]
    assert [problem for problem in problems if problem] == []


def test_every_item_says_why(rows: list[tuple[str, str, str]]) -> None:
    assert [item for item, _, why in rows if not why] == []


def test_no_item_is_listed_twice(rows: list[tuple[str, str, str]]) -> None:
    items = [item for item, _, _ in rows]
    assert len(items) == len(set(items))


def test_the_table_uses_every_kind_of_owner(rows: list[tuple[str, str, str]]) -> None:
    """Without this floor, a parser that finds nothing would pass the checks above."""
    owners = {owner for _, owner, _ in rows}
    assert len(rows) >= 25
    assert {NEEDS_THE_USER, AUTO_2} <= owners
    assert any(ROW_OWNER.fullmatch(owner) for owner in owners)
    assert any(NFR_OWNER.fullmatch(owner) for owner in owners)


@pytest.mark.parametrize("name", DOCS)
def test_no_doc_cites_a_branch_that_has_merged(name: str) -> None:
    text = (GOVERNANCE / name).read_text()
    assert [branch for branch in MERGED_BRANCHES if branch in text] == []


@pytest.mark.parametrize("name", DOCS)
def test_nothing_that_landed_is_called_in_flight(name: str) -> None:
    assert stale_lines((GOVERNANCE / name).read_text()) == []


def test_the_three_docs_share_one_baseline() -> None:
    found = [BASELINE.findall((GOVERNANCE / name).read_text()) for name in DOCS]
    assert all(len(baseline) == 1 for baseline in found), found
    assert len({baseline[0] for baseline in found}) == 1, found


# The checks themselves, on sample text.

HEAD = "## Owners\n\n| Item | Owner | Why |\n|---|---|---|\n"
ROWS = {"10", "9a", "A2"}
NFRS = {"NFR-04"}


@pytest.mark.parametrize("owner", ["#10", "#9a", "AUTO-2", "needs the user", "Window B P6, NFR-04"])
def test_a_real_owner_passes(owner: str) -> None:
    assert owner_problem(owner, ROWS, NFRS) is None


@pytest.mark.parametrize(
    ("owner", "expected"),
    [
        ("#999", "no such row"),
        ("Window B P6, NFR-99", "no such NFR"),
        ("Nobody", "not a row"),
        ("", "not a row"),
        ("#10 or #11", "not a row"),
        ("AUTO-3", "not a row"),
        ("Needs the user", "not a row"),
    ],
)
def test_a_made_up_owner_is_caught(owner: str, expected: str) -> None:
    problem = owner_problem(owner, ROWS, NFRS)
    assert problem is not None and expected in problem


def test_only_the_owners_section_counts() -> None:
    outside = "| Item | Owner | Why |\n|---|---|---|\n| a | b | c |\n\n"
    doc = f"{outside}{HEAD}| x | #10 | y |\n\n## Next\n"
    assert owner_rows(doc) == [("x", "#10", "y")]


def test_a_doc_with_no_owners_section_fails() -> None:
    with pytest.raises(AssertionError, match="no `## Owners` section"):
        owner_rows("| Item | Owner | Why |\n")


def test_a_row_with_a_missing_cell_fails() -> None:
    with pytest.raises(AssertionError, match="does not have three cells"):
        owner_rows(f"{HEAD}| x | #10 |\n")


@pytest.mark.parametrize(
    "line",
    [
        "| #7 in flight |",
        "In flight, 5b, not on main",
        "**#7 in flight:** text",
        "in flight (#7's run)",
    ],
)
def test_a_landed_row_called_in_flight_is_caught(line: str) -> None:
    assert stale_lines(line) == [line]


@pytest.mark.parametrize(
    "line",
    ["#7 landed at 6f920a1", "in flight, #70", "in flight, #17", "in flight means on a branch"],
)
def test_other_lines_are_left_alone(line: str) -> None:
    assert stale_lines(line) == []


def test_every_row_id_is_read_from_a_progress_table() -> None:
    sample = "| 5b | RAG-12 | x |\n| A2 | AUTO-2 | x |\n| # | ID | Scope |\n- | not a row | x |\n"
    assert progress_rows(sample) == {"5b", "A2"}
