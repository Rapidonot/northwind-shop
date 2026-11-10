"""A planted MCP config adds no server (CC-10, A3; NFR-01). The real CLI and a scripted model.

The planted server is a real HTTP MCP server offering a tool, so if the CLI loaded the planted
config the tool would be in the first request.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import mcp.types as t
import pytest
import uvicorn
from claude_agent_sdk import query
from mcp.server.lowlevel import Server

from northwind_common.llm import agent_options, agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("NFR-01")]

CALLS: list[str] = []


def planted_server() -> Server[Any]:
    async def list_tools(ctx: Any, params: Any) -> t.ListToolsResult:
        CALLS.append("tools/list")
        return t.ListToolsResult(
            tools=[
                t.Tool(name="exfiltrate", description="planted", input_schema={"type": "object"})
            ]
        )

    return Server("planted", on_list_tools=list_tools)


@contextlib.contextmanager
def serve(server: Server[Any]) -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    config = uvicorn.Config(
        server.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"
    )
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    while not uv.started:
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        uv.should_exit = True
        thread.join(timeout=10)


def test_a_planted_mcp_config_adds_no_server(tmp_path: Path) -> None:
    CALLS.clear()
    with serve(planted_server()) as url, ScriptedLLM() as llm:
        llm.reply("ok")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path / "rt")
        planted = {"mcpServers": {"planted": {"type": "http", "url": url}}}
        (runtime.workdir / ".mcp.json").write_text(json.dumps(planted), encoding="utf-8")
        (runtime.config_dir / ".claude.json").write_text(json.dumps(planted), encoding="utf-8")
        options = agent_options(
            system_prompt="sp", model="claude-haiku-5-5", tools=[], runtime=runtime
        )

        async def go() -> None:
            async for _ in query(prompt="hi", options=options):
                pass

        asyncio.run(go())
        [first, *_] = llm.requests
    names = [tool["name"] for tool in first["body"].get("tools", [])]
    assert not [n for n in names if "planted" in n or "exfiltrate" in n]
    assert CALLS == []


def test_the_same_server_given_through_the_factory_is_seen(tmp_path: Path) -> None:
    """The control: the planted server and the check above both work, so a pass is not vacuous."""
    CALLS.clear()
    with serve(planted_server()) as url, ScriptedLLM() as llm:
        llm.reply("ok")
        runtime = agent_runtime(base_url=llm.url, root=tmp_path / "rt")
        options = agent_options(
            system_prompt="sp",
            model="claude-haiku-5-5",
            tools=[],
            runtime=runtime,
            mcp_servers={"planted": {"type": "http", "url": url}},
        )

        async def go() -> None:
            async for _ in query(prompt="hi", options=options):
                pass

        asyncio.run(go())
        [first, *_] = llm.requests
    names = [tool["name"] for tool in first["body"].get("tools", [])]
    assert names == ["mcp__planted__exfiltrate"]
    assert "tools/list" in CALLS
