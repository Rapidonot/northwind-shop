"""`python -m northwind_llm_replay.cli spend|scan` (make llm-spend, make scan-cassettes)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from northwind_llm_replay.ledger import CHECKPOINT_EXCLUDED_SUITES, Ledger
from northwind_llm_replay.scan import scan_paths

CHECKPOINT_CALLS = 20
SCAN_ROOTS = ("cassettes", ".llm-ledger", ".llm-proxy-log")


def _secret(env_file: Path) -> str | None:
    """The real key, only for fragment matching; never printed. None in CI (no .env)."""
    if not env_file.exists():
        return None
    return dotenv_values(env_file).get("ANTHROPIC_API_KEY") or None


def cmd_spend(args: argparse.Namespace) -> int:
    budget = float(os.environ.get("LLM_BUDGET_USD", "90"))
    ledger = Ledger(args.ledger, budget_usd=budget, run_budget_usd=None, run_id="-", suite="-")
    t = ledger.totals()
    print(f"LLM spend (ledger {args.ledger})")
    print(f"  total          ${t.spent_usd:.6f} of LLM_BUDGET_USD ${budget:.2f}")
    print(f"  outstanding    ${t.outstanding_usd:.6f} (reserved, not settled)")
    print(f"  settled calls  {t.settled_calls}")
    excluded = ", ".join(sorted(CHECKPOINT_EXCLUDED_SUITES))
    print(
        f"  checkpoint     {t.checkpoint_calls}/{CHECKPOINT_CALLS} calls "
        f"(excludes suites: {excluded})"
    )
    for suite, usd in sorted(t.by_suite.items()):
        print(f"  suite {suite:<12} ${usd:.6f}")
    for model, usd in sorted(t.by_model.items()):
        print(f"  model {model:<20} ${usd:.6f}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    roots = [Path(p) for p in (args.paths or SCAN_ROOTS)]
    secret = _secret(args.env_file)
    findings = scan_paths(roots, secret=secret)
    for f in findings:
        print(f, file=sys.stderr)
    mode = "regex + key-fragment" if secret else "regex only (no .env)"
    status = "FAIL" if findings else "clean"
    print(f"scan {status}: {len(findings)} finding(s) in {', '.join(map(str, roots))} [{mode}]")
    return 1 if findings else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="northwind_llm_replay.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spend", help="ledger summary")
    sp.add_argument("--ledger", type=Path, default=Path(".llm-ledger/ledger.jsonl"))
    sp.set_defaults(func=cmd_spend)
    sc = sub.add_parser("scan", help="secret/PAN scan of cassettes, ledger and proxy log")
    sc.add_argument("paths", nargs="*")
    sc.add_argument("--env-file", type=Path, default=Path(".env"))
    sc.set_defaults(func=cmd_scan)
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
