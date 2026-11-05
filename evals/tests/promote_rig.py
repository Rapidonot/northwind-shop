"""Runs the promote tests' scenarios through the real entry point (row 5b, decision 0009).

The tests in `test_cli.py` and `test_rag12_followup.py` set up one flat temp tree. Promotion needs a
clone and a checkout, so this lays those out from it, calls `promotion.main` and copies the result
back. Their assertions are unchanged. Loaded by path: pytest runs with --import-mode=importlib, so
sibling imports do not resolve. Not collected: the name does not start with `test_`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from northwind_evals import cli
from northwind_llm_replay import promotion

REPO = Path(__file__).resolve().parents[2]
FLAGS = {
    "--accept-regression": "ACCEPT_REGRESSION",
    "--bootstrap": "BOOTSTRAP",
    "--approved-by": "APPROVED_BY",
}


def run(tmp: Path, *args: str) -> int:
    clone, checkout = tmp / "clone", tmp / "checkout"
    shutil.rmtree(clone, ignore_errors=True)
    shutil.rmtree(checkout, ignore_errors=True)
    for root in (clone, checkout):
        (root / "evals" / "fixtures").mkdir(parents=True)
        (root / "evals" / "baselines").mkdir()
        shutil.copy(cli.ITEMS, root / promotion.ITEMS)
        shutil.copy(cli.KNOWN_FAILURES, root / promotion.KNOWN_FAILURES)
    (clone / "evals" / "src").symlink_to(REPO / "evals" / "src")
    if cli.BASELINE.exists():
        shutil.copy(cli.BASELINE, clone / promotion.BASELINE)
    if cli.CANDIDATE.exists():
        (checkout / ".llm-proxy-log").mkdir()
        shutil.copy(cli.CANDIDATE, checkout / promotion.CANDIDATE)

    env = {"CHECKOUT": str(checkout)}
    words = list(args)
    while words:
        word = words.pop(0)
        if word == "--check":
            env["CHECK"] = "1"
        else:
            env[FLAGS[word]] = words.pop(0)
    if "ACCEPT_REGRESSION" in env and cli.CANDIDATE.exists():
        files = (cli.CANDIDATE, cli.ITEMS, cli.KNOWN_FAILURES)
        env["CANDIDATE_SHA"] = promotion.input_digest(*(f.read_bytes() for f in files))
    code = promotion.main(clone, env)
    written = checkout / promotion.BASELINE
    if written.exists():
        shutil.copy(written, cli.BASELINE)
    return code
