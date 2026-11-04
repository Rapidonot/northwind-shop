"""`python -m northwind_llm_replay.cli spend|scan|serve` (make llm-spend|scan-cassettes|llm-proxy).

Paths come from `northwind_common.localenv`: the shared ledger (LLM_LEDGER_PATH), the one key
file (LLM_ENV_FILE, default the main checkout's), and this worktree's dev proxy port
(LLM_PROXY_PORT).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

from dotenv import dotenv_values

from northwind_common import localenv
from northwind_llm_replay.ledger import CHECKPOINT_EXCLUDED_SUITES, Ledger
from northwind_llm_replay.scan import scan_paths

CHECKPOINT_CALLS = 20
PROXY_LOG_DIR = Path(".llm-proxy-log")


def _secret(env_file: Path) -> str | None:
    """The real key, only for fragment matching; never printed. None in CI (no key file)."""
    if not env_file.exists():
        return None
    return dotenv_values(env_file).get("ANTHROPIC_API_KEY") or None


def _budget() -> float:
    return float(os.environ.get("LLM_BUDGET_USD", "90"))


def cmd_spend(args: argparse.Namespace) -> int:
    budget = _budget()
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
    if args.paths:
        roots = [Path(p) for p in args.paths]
    else:
        roots = [Path("cassettes"), localenv.ledger_path(), PROXY_LOG_DIR]
    secret = _secret(args.env_file)
    findings = scan_paths(roots, secret=secret)
    for f in findings:
        print(f, file=sys.stderr)
    mode = "regex + key-fragment" if secret else "regex only (no key file)"
    status = "FAIL" if findings else "clean"
    print(f"scan {status}: {len(findings)} finding(s) in {', '.join(map(str, roots))} [{mode}]")
    return 1 if findings else 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Long-running dev proxy for this worktree (gateway/assistant point LLM_PROXY_URL at it)."""
    from northwind_llm_replay.proxy import ProxyConfig, RunningProxy

    mode = os.environ.get("LLM_MODE", "replay")
    ledger = None
    if mode != "replay":
        ledger = Ledger(
            localenv.ledger_path(),
            budget_usd=_budget(),
            run_budget_usd=float(os.environ.get("LLM_RUN_BUDGET_USD", "5.00")),
            run_id=os.environ.get("LLM_RUN_ID", f"dev-{localenv.worktree_root().name}"),
            suite=args.suite,
        )
    cfg = ProxyConfig(
        mode=mode,
        suite=args.suite,
        cassette_root=Path("cassettes"),
        ledger=ledger,
        env_file=localenv.env_file(),
        log_path=None if mode == "replay" else PROXY_LOG_DIR / f"{args.suite}.jsonl",
    )
    port = localenv.proxy_port()
    with RunningProxy(cfg, port=port) as running:
        print(f"LLM proxy ({mode}, suite {args.suite}) on {running.url}", flush=True)
        print(f"  export LLM_PROXY_URL={running.url}", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="northwind_llm_replay.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spend", help="ledger summary")
    sp.add_argument("--ledger", type=Path, default=None)
    sp.set_defaults(func=cmd_spend)
    sc = sub.add_parser("scan", help="secret/PAN scan of cassettes, ledger and proxy log")
    sc.add_argument("paths", nargs="*")
    sc.add_argument("--env-file", type=Path, default=None)
    sc.set_defaults(func=cmd_scan)
    sv = sub.add_parser("serve", help="dev proxy on this worktree's port (LLM_MODE decides)")
    sv.add_argument("--suite", default="dev")
    sv.set_defaults(func=cmd_serve)
    args = ap.parse_args(argv)
    if getattr(args, "ledger", "unset") is None:
        args.ledger = localenv.ledger_path()
    if getattr(args, "env_file", "unset") is None:
        args.env_file = localenv.env_file()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
