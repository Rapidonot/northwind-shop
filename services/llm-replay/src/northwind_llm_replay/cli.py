"""`python -m northwind_llm_replay.cli spend|scan|serve|runner|runner-check|promote`.

Behind make llm-spend, scan-cassettes, llm-proxy, llm-runner, runner-check and eval-promote. Paths
come from `northwind_common.localenv`: the shared ledger (LLM_LEDGER_PATH), the one key file
(LLM_ENV_FILE), this worktree's dev proxy port (LLM_PROXY_PORT) and the runner (LLM_RUNNER_PORT).
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
from northwind_llm_replay.scan import load_canaries, printable, scan_paths

CHECKPOINT_CALLS = 20
PROXY_LOG_DIR = Path(".llm-proxy-log")
# Committed eval files carry retrieved text and labels, the enrichment cache carries model output,
# and the frozen vectors feed every index, so they get the regex, PAN and canary scan too. They
# read as raw text, not decoded JSON.
EVAL_ROOTS = [
    Path("evals/contexts"),
    Path("evals/fixtures"),
    Path("evals/baselines"),
    Path("docs/reviews/RAG-12-eval-review.md"),
    Path("data/enrichment"),
    Path("data/embeddings"),
]


def _secret(env_file: Path) -> str | None:
    """The real key, only for fragment matching; never printed.

    None in CI (no key file) and in Claude's sandbox, which denies reading it (CC-27).
    """
    try:
        if not env_file.exists():
            return None
        return dotenv_values(env_file).get("ANTHROPIC_API_KEY") or None
    except PermissionError:
        return None


def _budget() -> float:
    return float(os.environ.get("LLM_BUDGET_USD", "90"))


def cmd_spend(args: argparse.Namespace) -> int:
    budget = _budget()
    ledger = Ledger(args.ledger, budget_usd=budget, run_budget_usd=None, run_id="-", suite="-")
    t = ledger.read_totals()
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
    print("  cache and tokens, settled calls:")
    for suite, row in sorted(ledger.usage_by_suite().items()):
        rate = "n/a" if row.cache_hit_rate is None else f"{row.cache_hit_rate:.1%}"
        per_call = "n/a" if row.cost_per_call is None else f"${row.cost_per_call:.6f}"
        print(
            f"  suite {suite:<16} calls {row.calls} "
            f"(no usage: {row.unreported}, unsent: {row.unsent}) "
            f"input {row.input_tokens} read {row.cache_read_tokens} "
            f"write {row.cache_write_tokens} output {row.output_tokens} "
            f"hit {rate} per call {per_call}"
        )
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    if args.paths:
        roots = [Path(p) for p in args.paths]
    else:
        cassettes = args.cassettes_dir or Path("cassettes")
        roots = [*EVAL_ROOTS, cassettes, localenv.ledger_path(), PROXY_LOG_DIR]
    secret = _secret(args.env_file)
    canaries = load_canaries(localenv.worktree_root(), required=True)
    findings = scan_paths(roots, secret=secret, canaries=canaries, ledger=localenv.ledger_path())
    # One line, no path and last: a session can write one guess per line or per file, so where a
    # fragment is, or its place among the other findings, must not show.
    for f in findings:
        if f.kind != "api-key-fragment":
            print(f, file=sys.stderr)
    if any(f.kind == "api-key-fragment" for f in findings):
        print("a piece of the key is in these files: ROTATE THE KEY", file=sys.stderr)
    mode = "regex + key-fragment" if secret else "regex only (no readable key file)"
    status = "FAIL" if findings else "clean"
    where = ", ".join(printable(str(root)) for root in roots)
    print(
        f"scan {status}: {len(findings)} finding(s) in {where} [{mode}, {len(canaries)} canaries]"
    )
    return 1 if findings else 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Long-running dev proxy for this worktree (gateway/assistant point LLM_PROXY_URL at it)."""
    from northwind_llm_replay.proxy import RunningProxy
    from northwind_llm_replay.runner import RunnerUnavailableError, proxy_config

    mode = os.environ.get("LLM_MODE", "replay")
    try:
        cfg = proxy_config(
            mode,
            args.suite,
            cassette_root=Path("cassettes"),
            log_path=None if mode == "replay" else PROXY_LOG_DIR / f"{args.suite}.jsonl",
        )
    except RunnerUnavailableError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    port = localenv.proxy_port()
    with RunningProxy(cfg, port=port) as running:
        print(f"LLM proxy ({mode}, suite {args.suite}) on {running.url}", flush=True)
        print(f"  export LLM_PROXY_URL={running.url}", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    return 0


def cmd_runner(args: argparse.Namespace) -> int:
    """The key-holding runner: one window, from the clean clone, in the user's own terminal."""
    from northwind_llm_replay.runner import serve_window

    return serve_window(args.suite, args.run_budget, args.minutes, args.idle_minutes, _budget())


def cmd_runner_check(args: argparse.Namespace) -> int:
    from northwind_llm_replay import runner_check

    python = args.python or localenv.runner_checkout() / ".venv-runner" / "bin" / "python"
    return runner_check.main(python)


def cmd_promote(_: argparse.Namespace) -> int:
    from northwind_llm_replay import promotion

    return promotion.main(localenv.runner_checkout(), os.environ)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="northwind_llm_replay.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spend", help="ledger summary")
    sp.add_argument("--ledger", type=Path, default=None)
    sp.set_defaults(func=cmd_spend)
    sc = sub.add_parser("scan", help="secret/PAN scan of cassettes, ledger and proxy log")
    sc.add_argument("paths", nargs="*")
    sc.add_argument("--env-file", type=Path, default=None)
    sc.add_argument("--cassettes-dir", type=Path, default=None, help="scan these cassettes instead")
    sc.set_defaults(func=cmd_scan)
    sv = sub.add_parser("serve", help="dev proxy on this worktree's port (LLM_MODE decides)")
    sv.add_argument("--suite", default="dev")
    sv.set_defaults(func=cmd_serve)
    rn = sub.add_parser("runner", help="the key-holding runner: opens one window")
    rn.add_argument("--suite", required=True)
    rn.add_argument("--run-budget", type=float, required=True)
    rn.add_argument("--minutes", type=float, default=30.0)
    rn.add_argument("--idle-minutes", type=float, default=10.0)
    rn.set_defaults(func=cmd_runner)
    rc = sub.add_parser("runner-check", help="measure that the runner's code is not writable here")
    rc.add_argument("--python", type=Path, default=None)
    rc.set_defaults(func=cmd_runner_check)
    pm = sub.add_parser("promote", help="replace the baseline, from the runner clone only")
    pm.set_defaults(func=cmd_promote)
    args = ap.parse_args(argv)
    if getattr(args, "ledger", "unset") is None:
        args.ledger = localenv.ledger_path()
    if getattr(args, "env_file", "unset") is None:
        args.env_file = localenv.env_file()
    try:
        return int(args.func(args))
    except (ValueError, FileNotFoundError) as e:  # bad config: refuse, with no traceback
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
