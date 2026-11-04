"""FOUND-2 spike evidence that runs everywhere: the spike cassettes replay with no API key.

These run in CI (Linux), so they also show that the macOS-recorded requests hash the same on
another OS (decision 0001 §4 gate).
"""

from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from northwind_common.llm import agent_options, agent_runtime, anthropic_client
from northwind_llm_replay.proxy import ProxyConfig, RunningProxy
from northwind_llm_replay.spike_fixture import PROMPT, SPIKE_MAX_OUTPUT_TOKENS, SYSTEM_PROMPT

REPO = Path(__file__).resolve().parents[3]


def _no_key() -> str:
    raise AssertionError("replay mode must never load the API key")


def replay_proxy(tmp_path: Path) -> RunningProxy:
    cfg = ProxyConfig(
        mode="replay",
        suite="spike",
        cassette_root=REPO / "cassettes",
        env_file=tmp_path / "absent.env",
        key_loader=_no_key,
    )
    return RunningProxy(cfg)


async def _agent_text(options: object) -> str:
    from claude_agent_sdk import AssistantMessage, TextBlock, query

    text = ""
    async for msg in query(prompt=PROMPT, options=options):  # type: ignore[arg-type]
        if isinstance(msg, AssistantMessage):
            text += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
    return text


@pytest.mark.req("FOUND-2")
@pytest.mark.parametrize(
    ("label", "model"), [("spike-haiku", "claude-haiku-5-5"), ("spike-sonnet", "claude-sonnet-5-5")]
)
def test_agent_sdk_query_replays_spike_cassette(tmp_path: Path, label: str, model: str) -> None:
    with replay_proxy(tmp_path) as proxy:
        rt = agent_runtime(
            base_url=f"{proxy.url}/_t/{label}",
            root=tmp_path / "rt",
            max_output_tokens=SPIKE_MAX_OUTPUT_TOKENS,
        )
        opts = agent_options(
            system_prompt=SYSTEM_PROMPT, model=model, runtime=rt, tools=[], max_turns=1
        )
        text = anyio.run(_agent_text, opts)
        state = proxy.proxy.state
    assert state.misses == [], state.misses[0]["diff"] if state.misses else ""
    assert [c["outcome"] for c in state.calls] == ["hit"]
    assert [c["auth"] for c in state.calls] == ["placeholder"]
    assert text == "ready"


@pytest.mark.req("FOUND-2")
@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-sonnet-5-5"])
def test_direct_client_replays_spike_cassette(tmp_path: Path, model: str) -> None:
    with replay_proxy(tmp_path) as proxy:
        client = anthropic_client(base_url=proxy.url, max_retries=0)
        msg = client.messages.create(
            model=model,
            max_tokens=16,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": PROMPT}],
        )
        state = proxy.proxy.state
    assert state.misses == []
    assert "".join(b.text for b in msg.content if b.type == "text") == "ready"
