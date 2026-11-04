"""FOUND-2 spike evidence that runs everywhere: the spike cassettes replay with no API key.

These run in CI (Linux), so they also show that the macOS-recorded requests hash the same on
another OS (decision 0001 §4 gate). Re-record on purpose with `make record SUITE=spike`.
"""

from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from northwind_common.llm import agent_options, agent_runtime, anthropic_client
from northwind_llm_replay.pytest_plugin import LLMProxyHandle
from northwind_llm_replay.spike_fixture import PROMPT, SPIKE_MAX_OUTPUT_TOKENS, SYSTEM_PROMPT


async def _agent_text(options: object) -> str:
    from claude_agent_sdk import AssistantMessage, TextBlock, query

    text = ""
    async for msg in query(prompt=PROMPT, options=options):  # type: ignore[arg-type]
        if isinstance(msg, AssistantMessage):
            text += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
    return text


@pytest.mark.req("FOUND-2")
@pytest.mark.llm_suite("spike")
@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-sonnet-5-5"])
def test_agent_sdk_query_replays_spike_cassette(
    llm_proxy: LLMProxyHandle, tmp_path: Path, model: str
) -> None:
    rt = agent_runtime(root=tmp_path / "rt", max_output_tokens=SPIKE_MAX_OUTPUT_TOKENS)
    opts = agent_options(
        system_prompt=SYSTEM_PROMPT, model=model, runtime=rt, tools=[], max_turns=1
    )
    text = anyio.run(_agent_text, opts)
    assert [c["outcome"] for c in llm_proxy.state.calls] == ["hit"]
    assert [c["auth"] for c in llm_proxy.state.calls] == ["placeholder"]
    assert text == "ready"


@pytest.mark.req("FOUND-2")
@pytest.mark.llm_suite("spike")
@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-sonnet-5-5"])
def test_direct_client_replays_spike_cassette(llm_proxy: LLMProxyHandle, model: str) -> None:
    client = anthropic_client(max_retries=0)
    msg = client.messages.create(
        model=model,
        max_tokens=16,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": PROMPT}],
    )
    assert [c["outcome"] for c in llm_proxy.state.calls] == ["hit"]
    assert "".join(b.text for b in msg.content if b.type == "text") == "ready"
