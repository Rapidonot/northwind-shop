"""The LLM client factory (FOUND-2; user changes [5] and [6]; decision 0001 billing resolution)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from northwind_common.llm import (
    PARENT_ENV_ALLOWLIST,
    PLACEHOLDER_API_KEY,
    agent_options,
    agent_runtime,
    anthropic_client,
    child_env,
    write_launcher,
)

FIXED_KEYS = {
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "DISABLE_TELEMETRY",
    "DISABLE_ERROR_REPORTING",
    "DISABLE_AUTOUPDATER",
    "NO_PROXY",
    "no_proxy",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_AGENT_SDK_VERSION",
    "CLAUDE_CODE_SDK_READS_SESSION_STATE",
}

# What a parent process (e.g. a Claude Code session, a shell with an exported key) may carry.
HOSTILE_PARENT = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/dev",
    "LANG": "C.UTF-8",
    "ANTHROPIC_API_KEY": "sk-ant-" + "parentkey0000000000",
    "ANTHROPIC_AUTH_TOKEN": "parent-oauth-token",
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "CLAUDE_CODE_OAUTH_TOKEN": "parent-oauth",
    "CLAUDE_CODE_SESSION_ID": "parent-session",
    "CLAUDE_CODE_USE_BEDROCK": "parent-bedrock",
    "CLAUDECODE": "parent-claudecode",
    "CLAUDECODE_EXTRA": "parent-extra",
    "UNRELATED_SECRET": "nope",
}


@pytest.mark.req("FOUND-2")
def test_child_env_is_built_from_scratch(tmp_path: Path) -> None:
    env = child_env("http://127.0.0.1:1234/_t/x", tmp_path, parent=HOSTILE_PARENT)
    assert set(env) == set(PARENT_ENV_ALLOWLIST) - {"TMPDIR"} | FIXED_KEYS
    assert env["ANTHROPIC_API_KEY"] == PLACEHOLDER_API_KEY
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:1234/_t/x"
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path)
    assert env["NO_PROXY"] == "127.0.0.1,localhost"
    leaked = {v for k, v in HOSTILE_PARENT.items() if k not in PARENT_ENV_ALLOWLIST}
    assert leaked.isdisjoint(env.values())


@pytest.mark.req("FOUND-2")
def test_launcher_runs_the_cli_with_exactly_that_env(tmp_path: Path) -> None:
    """The SDK merges os.environ into the child env; the env -i launcher undoes that."""
    env = child_env("http://127.0.0.1:1/", tmp_path, parent=HOSTILE_PARENT)
    launcher = write_launcher(tmp_path, env, Path("/usr/bin/env"))  # "CLI" that prints its env
    out = subprocess.run(  # noqa: S603
        [str(launcher)],
        env={**HOSTILE_PARENT, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    seen = dict(line.split("=", 1) for line in out.splitlines())
    assert seen == env


@pytest.mark.req("SA-02")
def test_agent_options_require_tools(tmp_path: Path) -> None:
    """FOUND-2 review 8: with no `tools` the SDK adds no --tools flag at all."""
    rt = agent_runtime(base_url="http://127.0.0.1:9/", root=tmp_path / "rt")
    with pytest.raises(TypeError):
        agent_options(system_prompt="sp", model="claude-haiku-5-5", runtime=rt)  # type: ignore[call-arg]


@pytest.mark.req("SA-02")
@pytest.mark.parametrize("tools", [None, (), ("Read",), "Read", {"type": "preset"}])
def test_agent_options_refuse_anything_but_a_list(tmp_path: Path, tools: Any) -> None:
    """The SDK sends `--tools default` for any non-list, so a tuple means every default tool."""
    rt = agent_runtime(base_url="http://127.0.0.1:9/", root=tmp_path / "rt")
    with pytest.raises(TypeError, match="tools"):
        agent_options(system_prompt="sp", model="claude-haiku-5-5", tools=tools, runtime=rt)


@pytest.mark.req("SA-02")
def test_an_empty_tools_list_reaches_the_cli_as_no_tools(tmp_path: Path) -> None:
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    rt = agent_runtime(base_url="http://127.0.0.1:9/", root=tmp_path / "rt")
    opts = agent_options(system_prompt="sp", model="claude-haiku-5-5", tools=[], runtime=rt)
    command = SubprocessCLITransport(prompt="hi", options=opts)._build_command()
    assert command[command.index("--tools") + 1] == ""


@pytest.mark.req("FOUND-2")
def test_agent_options_use_launcher_bare_mode_and_no_settings(tmp_path: Path) -> None:
    rt = agent_runtime(base_url="http://127.0.0.1:9/", root=tmp_path / "rt")
    opts = agent_options(system_prompt="sp", model="claude-haiku-5-5", tools=[], runtime=rt)
    assert Path(str(opts.cli_path)) == rt.launcher
    assert opts.setting_sources == []
    assert "bare" in opts.extra_args and opts.extra_args["bare"] is None
    assert opts.system_prompt == "sp"
    assert Path(str(opts.cwd)) == rt.workdir
    assert rt.config_dir.is_dir() and rt.config_dir.parent == tmp_path / "rt"
    # [4]: the CLI would send max_tokens=128000 by default; the factory always sets it.
    assert rt.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8192"
    assert (
        agent_runtime(root=tmp_path / "r2", max_output_tokens=1024).env[
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS"
        ]
        == "1024"
    )


@pytest.mark.req("FOUND-2")
def test_anthropic_client_sends_only_the_placeholder_even_with_parent_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "parentkey0000000000")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "parent-oauth-token")
    client = anthropic_client(base_url="http://127.0.0.1:9")
    assert str(client.base_url).startswith("http://127.0.0.1:9")
    assert client.auth_headers == {"Authorization": f"Bearer {PLACEHOLDER_API_KEY}"}


@pytest.mark.req("FOUND-2")
def test_clients_default_to_this_worktrees_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)
    monkeypatch.setenv("LLM_PROXY_PORT", "23456")
    assert str(anthropic_client().base_url).startswith("http://127.0.0.1:23456")
    monkeypatch.setenv("LLM_PROXY_URL", "http://127.0.0.1:1/_t/abc")
    assert str(anthropic_client().base_url).startswith("http://127.0.0.1:1/_t/abc")
