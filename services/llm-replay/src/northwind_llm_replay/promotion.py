"""`make -C ~/.northwind/runner eval-promote`: replace the baseline, from the clean clone (row 5b).

Promotion runs in your terminal, with no sandbox, so no code from a checkout may run here (decision
0009). This runs on `.venv-runner`, and takes the gate's rules from the clone's own `evals/src`:
`gate.py` and `items.py` are stdlib only, so neither the database nor the RAG code loads.

The checkout is data. Three files are read from it, the one baseline is written to it, and git never
runs in it, because your terminal's git trusts the config it finds there (decision 0006). What
decides the outcome comes from the clone: the old baseline, and the lists a checkout may only shrink
or add to. Every path below the checkout is opened one folder at a time with `O_NOFOLLOW`, so a
symlink the session planted cannot send this read or this write anywhere else.

This cannot prove a candidate's scores are real. Forged answer scores fail `score_drift` in CI
unless they come with a forged new cassette, which `eval-integrity` does not refuse. Retrieval has
no such check: a small drop within tolerance passes with nothing forged, and until row #8 measures
whether a drift check is stable across machines, the tolerance is the only guard on it (5a F7).
So reading the PR that carries the `baseline-change` label is the check on both.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

from northwind_llm_replay.scan import printable

BASELINE = "evals/baselines/dense-only.json"
CANDIDATE = ".llm-proxy-log/baseline-candidate.json"
ITEMS = "evals/fixtures/starter_set.jsonl"
KNOWN_FAILURES = "evals/fixtures/known_failures.json"
BASELINE_NAME = "dense-only"
MAX_BYTES = 16 * 1024 * 1024
CHECKOUT_PATH = re.compile(r"/[A-Za-z0-9._/-]+")
METRIC_NAME = re.compile(r"[a-z0-9_@]{1,32}")
# Printed, so a long run of spaces must not be able to wrap into a line of its own.
ITEM_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
# These decide what an item is tested for, so a change to one is not a change of wording. Widening
# `relevant_sources` stales no recording, because it never reaches a model. The query is wording too
# unless containment or a person's review depends on it: see `item_set`. `why` reaches no gate.
LABEL_FIELDS = (
    "expected_behaviour",
    "reviewed_by",
    "relevant_sources",
    "reference_facts",
    "region",
    "trap_id",
)
MAX_ID_CHARS = 200
IDENTITY_FIELDS = ("retriever_id", "index_version", "answerer_id", "judge_id")
CANDIDATE_FIELDS = {
    "name",
    *IDENTITY_FIELDS,
    "retrieval",
    "answers",
    "summary",
    "segments",
    "overrides",
}


class PromotionError(Exception):
    """Why nothing was promoted. Never holds text read from a file: only names and positions."""


def gate_modules(clone: Path) -> tuple[ModuleType, ModuleType]:
    """The gate's rules, from the clone and nowhere else. Appended, never inserted, so nothing in
    `evals/src` can shadow the stdlib. An earlier entry could shadow this package instead, so the
    result is checked, by the module's spec, because a module sets its own `__file__`. CI makes the
    same check on main's copy (`guard/assert-main-code.py`)."""
    source = (clone / "evals" / "src").resolve()
    if str(source) not in sys.path:
        sys.path.append(str(source))
    try:
        package = importlib.import_module("northwind_evals")
        gate = importlib.import_module("northwind_evals.gate")
        items = importlib.import_module("northwind_evals.items")
        metrics = importlib.import_module("northwind_evals.metrics")
    except ImportError as error:
        raise PromotionError(f"cannot load the gate from {source}: {error}") from None
    for module in (package, gate, items, metrics):
        where = Path(getattr(module.__spec__, "origin", None) or "").resolve()
        if not where.is_relative_to(source):
            raise PromotionError(f"{module.__name__} loaded from {where}, not from {source}")
    return gate, items


def free_text(env: Mapping[str, str], name: str) -> str:
    """Literal: nothing here trims it, and it never passes through argv, so `--check` is a value.
    Blank is refused because a blank approver fails every later gate and can never be removed:
    overrides are append-only."""
    value = env.get(name, "")
    if value and (not value.strip() or not value.isprintable()):
        raise PromotionError(f"{name} must be printable text, not blank")
    return value


def checkout_path(clone: Path, value: str) -> Path:
    if not CHECKOUT_PATH.fullmatch(value):
        raise PromotionError("CHECKOUT must be an absolute path: use $HOME, not ~")
    path = Path(value)
    if ".." in path.parts:
        raise PromotionError("CHECKOUT must not contain ..")
    try:
        resolved = path.resolve(strict=True)
        here = clone.stat()
        inside = any(os.path.samestat(p.stat(), here) for p in (resolved, *resolved.parents))
    except (OSError, RuntimeError):  # RuntimeError: a symlink loop
        raise PromotionError("cannot read CHECKOUT") from None
    if inside:
        raise PromotionError("CHECKOUT is the runner clone, which must stay clean")
    return path


def open_folder(root: int, parts: Sequence[str]) -> int:
    """A descriptor for a folder below `root`, refusing a symlink at every step."""
    folder = os.dup(root)
    try:
        for part in parts:
            step = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
            os.close(folder)
            folder = step
    except OSError:
        os.close(folder)
        raise
    return folder


def read_data(root: int, relative: str) -> bytes:
    """One regular file with one name. `O_NONBLOCK` because a FIFO would hang the open."""
    *parts, name = relative.split("/")
    try:
        folder = open_folder(root, parts)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        finally:
            os.close(folder)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise PromotionError(f"{relative}: {error.strerror}") from None
    info = os.fstat(fd)  # before `fdopen`, which raises on a folder
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise PromotionError(f"{relative} is not a regular file with one name")
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise PromotionError(f"{relative} is larger than {MAX_BYTES} bytes")
    return data


def write_baseline(root: int, text: str) -> None:
    """The only write. A temp file and a rename inside the folder's descriptor, so a failure leaves
    the old baseline whole, and no path is resolved again after the walk."""
    *parts, name = BASELINE.split("/")
    temp = f".{name}.{os.getpid()}.tmp"
    try:
        data = text.encode("utf-8")  # before the temp file exists, so a failure leaves none
    except UnicodeEncodeError:
        raise PromotionError("the baseline cannot be encoded") from None
    try:
        folder = open_folder(root, parts)
    except OSError as error:
        raise PromotionError(f"{BASELINE}: {error.strerror}") from None
    try:
        try:
            current = os.stat(name, dir_fd=folder, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(current.st_mode):
                raise PromotionError(f"{BASELINE} is not a regular file")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(temp, flags, 0o644, dir_fd=folder)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, name, src_dir_fd=folder, dst_dir_fd=folder)
        except OSError:
            os.unlink(temp, dir_fd=folder)
            raise
    except OSError as error:
        raise PromotionError(f"{BASELINE}: {error.strerror}") from None
    finally:
        os.close(folder)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("a duplicate key")
        found[key] = value
    return found


def _no_constant(_name: str) -> None:
    raise ValueError("NaN or Infinity")


def parse_json(data: bytes | str, label: str) -> Any:
    """Strict: `json` otherwise keeps the last of two equal keys and reads `NaN` as a number. The
    error names the file only, so text from a file the session wrote never reaches the terminal."""
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except (ValueError, RecursionError):
        raise PromotionError(f"{label} is not valid strict JSON") from None


def _short_text(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_ID_CHARS and value.isprintable()


def _score(value: Any) -> bool:
    """Checked after parsing, because `1e400` parses to infinity. One score above 1 would raise a
    mean enough to hide a real regression in the same metric."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= 1
    except OverflowError:  # an integer too large for a float
        return False


def _score_table(table: Any, label: str) -> None:
    if not isinstance(table, dict):
        raise PromotionError(f"the candidate's {label} is not a table")
    for item_id, scores in table.items():
        if not ITEM_ID.fullmatch(item_id) or not isinstance(scores, dict):
            raise PromotionError(f"the candidate's {label} holds a bad entry")
        for metric, value in scores.items():
            if not METRIC_NAME.fullmatch(metric) or not _score(value):
                shown = printable(item_id)
                raise PromotionError(f"the candidate's {label} for {shown} holds a bad score")


def parse_candidate(gate: ModuleType, data: bytes) -> Any:
    record = parse_json(data, "the candidate")
    if not isinstance(record, dict) or record.keys() != CANDIDATE_FIELDS:
        raise PromotionError("the candidate does not have the baseline's fields")
    if record["name"] != BASELINE_NAME:
        raise PromotionError(f"the candidate is not named {BASELINE_NAME}")
    if not all(_short_text(record[field]) for field in IDENTITY_FIELDS):
        raise PromotionError("the candidate's retriever, index, answerer or judge ID is bad")
    if record["overrides"] != []:
        raise PromotionError("a candidate holds no overrides: only promotion records them")
    if not isinstance(record["summary"], dict):
        raise PromotionError("the candidate's summary is not a table")  # recomputed below
    _score_table(record["retrieval"], "retrieval")
    _score_table(record["answers"], "answers")
    segments = record["segments"]
    if not isinstance(segments, dict) or not all(
        ITEM_ID.fullmatch(k) and _short_text(v) for k, v in segments.items()
    ):
        raise PromotionError("the candidate's segments are not a table of names")
    return gate.Baseline(**record)


def parse_items(items: ModuleType, data: bytes, label: str) -> list[Any]:
    """The same schema as `load_items`, for bytes that were read through a descriptor."""
    try:
        lines = data.decode("utf-8").splitlines()
        parsed = [items.parse_item(parse_json(line, label)) for line in lines if line.strip()]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PromotionError(f"{label} does not match the item schema") from None
    ids = [item.id for item in parsed]
    if not all(ITEM_ID.fullmatch(item_id) for item_id in ids):
        raise PromotionError(f"{label} holds an item ID of the wrong shape")
    if len(ids) != len(set(ids)):
        raise PromotionError(f"{label} has duplicate item IDs")
    return parsed


def label_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def input_digest(*files: bytes) -> str:
    """What an approval covers: every file the checkout can change. Length-prefixed, so bytes cannot
    move from one file to the next for the same hash."""
    digest = hashlib.sha256()
    for data in files:
        digest.update(len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def item_set(
    trusted: Sequence[Any], offered: Sequence[Any], guarded: Sequence[str]
) -> tuple[list[str], list[str]]:
    """What the checkout's set changes, as (lines to print, relabels). It may add items and change
    wording, because a new adversarial item has to land with its baseline. It may not lose an item
    or move one to another segment. A relabel is possible, for the same reason, but it decides what
    an item is tested for: flipping `contain` to `answer` is the move a segment change would be. So
    it needs a reason and an approver, and is recorded with both of its values. The query counts as
    a relabel on a `guarded` segment or a human-reviewed item: a harmless query lets containment
    pass on a test that no longer attacks anything."""
    now = {item.id: item for item in offered}
    lines: list[str] = []
    relabels: list[str] = []
    for item in trusted:
        if item.id not in now:
            raise PromotionError(f"item {item.id} is not in the checkout's set")
        if now[item.id].segment != item.segment:
            raise PromotionError(f"item {item.id} changed segment in the checkout")
        for field in LABEL_FIELDS:
            old, new = getattr(item, field), getattr(now[item.id], field)
            if old != new:
                relabels.append(f"{item.id} {field}: {label_text(old)} -> {label_text(new)}")
        if now[item.id].query != item.query and (
            item.segment in guarded or item.reviewed_by == "human"
        ):
            relabels.append(f"{item.id} query: {item.query} -> {now[item.id].query}")
        if now[item.id] != item:
            lines.append(f"item changed: {item.id}")
    known = {item.id for item in trusted}
    return lines + [f"item added: {i}" for i in now if i not in known], relabels


def known_failures(gate: ModuleType, trusted: Any, offered: Any) -> list[Any]:
    """Shrink-only, word for word. The checkout's list is used, because fixing a listed item means
    removing its entry in the same PR: left in, `now passes` would refuse the promotion for good."""
    if not isinstance(offered, list) or not isinstance(trusted, list):
        raise PromotionError("known failures must be a list")
    if any(entry not in trusted for entry in offered):
        raise PromotionError("the checkout's known failures hold an entry the clone does not")
    return [gate.KnownFailure(**entry) for entry in offered]


def print_rows(result: Any) -> None:
    for metric, before, now, ok in result.rows:
        status = "ok  " if ok else "FAIL"
        print(
            f"  {status} {printable(metric):<13} baseline {before:.3f}  current {now:.3f}",
        )


def open_root(path: Path, flags: int = 0) -> int:
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | flags)
    except OSError as error:
        raise PromotionError(f"cannot read {path.name}: {error.strerror}") from None


def promote(clone: Path, env: Mapping[str, str]) -> int:
    gate, items_module = gate_modules(clone)
    reason = free_text(env, "ACCEPT_REGRESSION")
    bootstrap = free_text(env, "BOOTSTRAP")
    approver = free_text(env, "APPROVED_BY")
    checkout = checkout_path(clone, env.get("CHECKOUT", ""))
    trusted = open_root(clone)
    root = -1
    try:
        # `O_NOFOLLOW`, and the same check on what was really opened: the name was resolved above,
        # and a link swapped in since must not move the read or the write.
        root = open_root(checkout, os.O_NOFOLLOW)
        if os.path.samestat(os.fstat(root), os.fstat(trusted)):
            raise PromotionError("CHECKOUT is the runner clone, which must stay clean")
        return decide(gate, items_module, trusted, root, checkout, env, reason, bootstrap, approver)
    finally:
        os.close(trusted)
        if root != -1:
            os.close(root)


def decide(
    gate: ModuleType,
    items_module: ModuleType,
    clone: int,
    root: int,
    checkout: Path,
    env: Mapping[str, str],
    reason: str,
    bootstrap: str,
    approver: str,
) -> int:
    """A regression or a relabel needs a recorded reason and approver, and the hash of the candidate
    you read. A containment failure, a dropped item or a citation score under the floor cannot be
    overridden here at all. `clone` and `root` are descriptors: the clone's data is read the same
    way as the checkout's, so a link merged by mistake cannot make it session-writable."""
    try:
        candidate_data = read_data(root, CANDIDATE)
    except FileNotFoundError:
        raise PromotionError(
            "no candidate: run `make eval-rescore` or `make eval-live` in the checkout first"
        ) from None
    candidate = parse_candidate(gate, candidate_data)
    try:
        offered_items = read_data(root, ITEMS)
        offered_known = read_data(root, KNOWN_FAILURES)
        trusted_items = read_data(clone, ITEMS)
        trusted_known = read_data(clone, KNOWN_FAILURES)
    except FileNotFoundError as error:
        raise PromotionError(f"no {Path(error.filename or '').name}") from None
    digest = input_digest(candidate_data, offered_items, offered_known)
    print(f"candidate sha256: {digest}")
    try:
        trusted_baseline: bytes | None = read_data(clone, BASELINE)
    except FileNotFoundError:
        trusted_baseline = None
    trusted = parse_items(items_module, trusted_items, "the clone's item set")
    items = parse_items(items_module, offered_items, "the checkout's item set")
    lines, relabels = item_set(trusted, items, gate.ZERO_TOLERANCE_SEGMENTS)
    for line in lines:
        print(f"  {line}")
    known = known_failures(
        gate,
        parse_json(trusted_known, "the clone's known failures"),
        parse_json(offered_known, "the checkout's known failures"),
    )
    segments = {item.id: item.segment for item in items}
    if candidate.segments != segments:
        raise PromotionError("the candidate's segments do not match the item set")
    if not set(candidate.retrieval) <= set(segments):
        raise PromotionError("the candidate scores retrieval for an item not in the set")

    final: list[str] = gate.coverage_problems(candidate, items)
    final += gate.malformed_behaviour(candidate.answers)
    overridable: list[str] = list(relabels)
    overrides: list[dict[str, Any]] = []
    if (reason or bootstrap) and not approver:
        final.append("a recorded reason needs APPROVED_BY")
    if trusted_baseline is not None:
        baseline = gate.Baseline(**parse_json(trusted_baseline, "the clone's baseline"))
        overrides = list(baseline.overrides)
        final += [f"would stop gating {i}" for i in gate.dropped_items(baseline, candidate, items)]
        final += gate.identity_problems(baseline, items)
        final += gate.override_problems(baseline)
        if bootstrap:
            final.append("BOOTSTRAP is for a first baseline, and one exists")
        for field in IDENTITY_FIELDS:
            old, new = getattr(baseline, field), getattr(candidate, field)
            if old != new:
                print(printable(f"  identity changed: {field}: {old} -> {new}"))
    elif not bootstrap:
        final.append("no baseline to compare with: BOOTSTRAP=<reason> starts a new one")
        baseline = candidate
    else:
        baseline = candidate
    result = gate.compare(baseline, candidate.retrieval, candidate.answers, [], [], segments, known)
    print_rows(result)
    final += result.containment
    final += [f"{metric} is missing from the candidate" for metric in result.lost_metrics]
    floor = [p for p in result.problems if p.startswith("citation_ok ")]
    final += floor  # hard rule 5 is absolute: no reason accepts a baseline under the floor
    overridable += [p for p in result.problems if p not in floor]
    overridable += [f"{row[0]} regressed" for row in result.rows if not row[3]]
    checking = bool(env.get("CHECK"))
    if overridable and reason and not checking and env.get("CANDIDATE_SHA") != digest:
        final.append("a reason covers what you read: pass CANDIDATE_SHA from a CHECK=1 run")
    for problem in [*final, *overridable]:
        print(f"  {printable(problem)}", file=sys.stderr)
    if final or (overridable and not reason):
        print("not promoted", file=sys.stderr)
        return 1
    if checking:
        print("promotion check passed; nothing was changed")
        return 0
    today = date.today().isoformat()
    if bootstrap:
        accepted = ["no earlier baseline"]
        overrides.append(
            {"on": today, "reason": bootstrap, "approved_by": approver, "accepted": accepted}
        )
    if overridable:
        overrides.append(
            {"on": today, "reason": reason, "approved_by": approver, "accepted": overridable}
        )
    summary = gate.summary_of(candidate.retrieval, candidate.answers, candidate.segments)
    final_baseline = replace(candidate, overrides=overrides, summary=summary)
    text = json.dumps(final_baseline.__dict__, indent=2, sort_keys=True, ensure_ascii=False)
    write_baseline(root, text + "\n")
    print(f"promoted to {printable(str(checkout / BASELINE))}")
    print("the candidate stays where it was. The PR needs the baseline-change label, from you.")
    return 0


def main(clone: Path, env: Mapping[str, str]) -> int:
    try:
        return promote(clone, env)
    except PromotionError as error:
        print(f"not promoted: {printable(str(error))}", file=sys.stderr)
        return 1
