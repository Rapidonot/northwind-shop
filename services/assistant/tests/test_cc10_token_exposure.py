"""Where the per-turn token goes, besides argv (CC-10; the security review's unconfirmed items 1 and
3): never into the CLI's config dir or work dir, never on stderr, and never through an environment
proxy from the gateway's side."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from northwind_assistant import rag_client
from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import McpRagSearcher, RagEndpoint
from northwind_assistant.tools import ALLOWED_TOOL
from northwind_assistant.turn import stream_answer
from northwind_common.llm import agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("NFR-02")]

US = Caller(region="US")


class One:
    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        return [RetrievedChunk("c1", "document", "FAQ-001", "faq", "Wash cold.")]


def test_the_token_is_in_no_file_of_the_runtime_and_not_on_stderr(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    minted: list[str] = []
    real_mint = RagEndpoint.mint

    def spy(self: RagEndpoint, caller: Caller) -> str:
        token = real_mint(self, caller)
        minted.append(token)
        return token

    monkeypatch.setattr(RagEndpoint, "mint", spy)
    with ScriptedLLM() as llm, serve_retriever(One(), Caller) as (url, _):
        llm.reply_tool_use(ALLOWED_TOOL, {"query": "q"})
        llm.reply_stream("Wash cold [FAQ-001].")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path / "rt")

        async def go() -> None:
            async for _ in stream_answer("hi", US, RagEndpoint(url, KEY), runtime):
                pass

        asyncio.run(go())
    [token] = minted
    signature = token.split(".")[2]
    holding = [
        p
        for p in (tmp_path / "rt").rglob("*")
        if p.is_file() and signature in p.read_text("utf-8", "replace")
    ]
    assert holding == []
    out, err = capfd.readouterr()
    assert signature not in out and signature not in err
    assert len(llm.requests) == 2  # the CLI really connected and called the tool


def test_an_environment_proxy_never_carries_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead proxy in the environment: the search still reaches mcp-rag directly."""
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    with serve_retriever(One(), Caller) as (url, _):
        searcher = McpRagSearcher(url, RagEndpoint(url, KEY).mint(US))
        result = asyncio.run(searcher.search("q"))
    assert result.is_error is False and "FAQ-001" in result.text


def test_the_httpx2_loggers_are_held_too() -> None:
    assert {"httpx2", "httpcore2"} <= set(rag_client.QUIET_LOGGERS)
