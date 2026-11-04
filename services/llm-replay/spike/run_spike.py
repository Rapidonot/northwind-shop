"""FOUND-2 spike driver: one Agent SDK query (or one direct anthropic call) through the proxy.

Usage (from the repo root):
    uv run python services/llm-replay/spike/run_spike.py --mode replay --label r1
    uv run python services/llm-replay/spike/run_spike.py --mode live --model haiku --label l1

Every run, in every mode, writes each request the proxy saw to .llm-proxy-log/spike.jsonl
(gitignored, scanned) and a run summary to .llm-proxy-log/spike-runs.jsonl. Live spend goes to
the ledger under suite "spike" with run budget LLM_RUN_BUDGET_USD=1.00, enforced by the proxy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import anyio

sys.path.insert(0, str(Path(__file__).parent))

from egress import EgressLogger  # noqa: E402

from northwind_common.llm import agent_options, agent_runtime, anthropic_client  # noqa: E402
from northwind_llm_replay.ledger import Ledger, now_iso  # noqa: E402
from northwind_llm_replay.proxy import ProxyConfig, RunningProxy  # noqa: E402
from northwind_llm_replay.spike_fixture import PROMPT, SYSTEM_PROMPT  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
LOG_DIR = REPO / ".llm-proxy-log"
MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
SPIKE_RUN_ID = "spike-2026-11-04"
SPIKE_RUN_BUDGET_USD = 1.00


async def run_agent(
    base_url: str,
    egress_url: str,
    model: str,
    root: Path | None,
    extra_env: dict[str, str],
    prompt: str,
) -> dict[str, Any]:
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query

    env = {
        "HTTPS_PROXY": egress_url,
        "HTTP_PROXY": egress_url,
        "https_proxy": egress_url,
        "http_proxy": egress_url,
    }
    env.update(extra_env)
    max_out = int(extra_env.pop("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "128000"))
    rt = agent_runtime(base_url=base_url, root=root, extra_env=env, max_output_tokens=max_out)
    stderr: list[str] = []
    opts = agent_options(
        system_prompt=SYSTEM_PROMPT,
        model=model,
        runtime=rt,
        tools=[],
        max_turns=1,
        stderr=stderr.append,
    )
    out: dict[str, Any] = {"runtime_root": str(rt.root), "text": "", "result": None}
    try:
        async for msg in query(prompt=prompt, options=opts):
            if isinstance(msg, AssistantMessage):
                out["text"] += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
            elif isinstance(msg, ResultMessage):
                out["result"] = {
                    "is_error": msg.is_error,
                    "subtype": msg.subtype,
                    "num_turns": msg.num_turns,
                    "total_cost_usd": msg.total_cost_usd,
                    "usage": msg.usage,
                }
    except Exception as e:  # the spike records failures rather than hiding them
        out["exception"] = f"{type(e).__name__}: {str(e)[:500]}"
    out["stderr_tail"] = stderr[-20:]
    return out


def run_direct(base_url: str, model: str) -> dict[str, Any]:
    client = anthropic_client(base_url=base_url, max_retries=0)
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=16,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": PROMPT}],
        )
        return {
            "text": "".join(b.text for b in msg.content if b.type == "text"),
            "usage": msg.usage.model_dump(),
        }
    except Exception as e:
        return {"exception": f"{type(e).__name__}: {str(e)[:500]}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["replay", "record", "live"], default="replay")
    ap.add_argument("--model", choices=sorted(MODELS), default="haiku")
    ap.add_argument("--label", required=True)
    ap.add_argument(
        "--root", type=Path, default=None, help="runtime root (default: a fresh temp dir)"
    )
    ap.add_argument(
        "--direct", action="store_true", help="direct anthropic client instead of the Agent SDK"
    )
    ap.add_argument("--max-output-tokens", type=int, default=None)
    ap.add_argument("--prompt", default=PROMPT)
    ap.add_argument("--cassettes", type=Path, default=REPO / "cassettes")
    args = ap.parse_args()

    ledger = Ledger(
        REPO / ".llm-ledger" / "ledger.jsonl",
        budget_usd=90.0,
        run_budget_usd=SPIKE_RUN_BUDGET_USD,
        run_id=SPIKE_RUN_ID,
        suite="spike",
    )
    cfg = ProxyConfig(
        mode=args.mode,
        suite="spike",
        cassette_root=args.cassettes,
        ledger=ledger,
        env_file=REPO / ".env",
        log_path=LOG_DIR / "spike.jsonl",
    )
    extra_env: dict[str, str] = {}
    if args.max_output_tokens:
        extra_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(args.max_output_tokens)
    model = MODELS[args.model]
    with EgressLogger() as egress, RunningProxy(cfg) as proxy:
        base = f"{proxy.url}/_t/{args.label}"
        if args.direct:
            result = run_direct(base, model)
        else:
            result = anyio.run(
                run_agent, base, egress.url, model, args.root, extra_env, args.prompt
            )
        summary = {
            "ts": now_iso(),
            "label": args.label,
            "mode": args.mode,
            "model": model,
            "direct": args.direct,
            "calls": proxy.proxy.state.calls,
            "misses": [{"hash": m["hash"], "path": m["path"]} for m in proxy.proxy.state.misses],
            "egress": egress.hits,
            "scan_failures": proxy.proxy.state.scan_failures,
            **result,
        }
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / "spike-runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, sort_keys=True) + "\n")
    t = ledger.totals()
    print(
        json.dumps(
            {
                k: summary[k]
                for k in ("label", "mode", "model", "misses", "egress", "scan_failures")
            },
            indent=1,
        )
    )
    for c in summary["calls"]:
        print(
            f"  {c['method']} {c['path']} model={c['model']} max_tokens={c['max_tokens']} "
            f"stream={c['stream']} auth={c['auth']} outcome={c.get('outcome')} "
            f"cost={c.get('cost_usd')}"
        )
    print(
        "  text:",
        repr(result.get("text")),
        "| exception:",
        result.get("exception"),
        "| result:",
        result.get("result"),
    )
    print(f"  ledger: spike run spent ${t.run_spent_usd:.6f}; total ${t.spent_usd:.6f}")


if __name__ == "__main__":
    main()
