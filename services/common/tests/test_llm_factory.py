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
    "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK",  # HARDEN-1
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


@pytest.mark.req("HARDEN-1")
def test_child_env_sets_only_the_refusal_fallback_flag(tmp_path: Path) -> None:
    """Measured on the bundled CLI (docs/reviews/2026-11-12-HARDEN-1-failing-first.md):
    this flag
    alone kept the fallback-credit beta off; DISABLE_REFUSAL_RETRY sent it on the first request."""
    env = child_env("http://127.0.0.1:1/", tmp_path, parent=HOSTILE_PARENT)
    assert env["CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"] == "1"
    assert "CLAUDE_CODE_DISABLE_REFUSAL_RETRY" not in env
    assert "CLAUDE_CODE_NO_MODEL_FALLBACK" not in env


@pytest.mark.req("HARDEN-1")
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK", "0"),
        ("CLAUDE_CODE_DISABLE_REFUSAL_RETRY", "1"),
        ("CLAUDE_CODE_NO_MODEL_FALLBACK", "1"),
        ("claude_code_disable_refusal_retry", "1"),
    ],
)
def test_extra_env_cannot_undo_the_refusal_setting(tmp_path: Path, key: str, value: str) -> None:
    """Combinations bring the beta back (the flag plus NO_MODEL_FALLBACK does), so no caller may
    set these, through child_env or agent_runtime (advisor F3)."""
    with pytest.raises(ValueError, match="refusal"):
        child_env("http://127.0.0.1:1/", tmp_path, extra={key: value})
    with pytest.raises(ValueError, match="refusal"):
        agent_runtime(base_url="http://127.0.0.1:1/", root=tmp_path / "rt", extra_env={key: value})


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
@pytest.mark.parametrize(
    "key",
    [
        "bare",
        "no-session-persistence",
        "tools",
        "--bare",
        "--no-session-persistence",
        "--tools",
        "BARE",
        "no_session_persistence",
        "Tools",
        "bare=false",
        "tools=Bash",
        "--tools=Bash,Read",
        "no-session-persistence=false",
        " tools ",
        "setting-sources",
        "--settings=/tmp/x.json",
        "mcp_config",
        "permission-mode",
        "--permission-mode=bypassPermissions",
        "dangerously-skip-permissions",
        "allow-dangerously-skip-permissions",
        "add-dir",
        "allowedTools",
        "allowed-tools",
        "strict-mcp-config",
        "--strict_mcp_config",
        "disallowedTools",
    ],
)
@pytest.mark.parametrize("value", [None, "", "false", "Bash"])
def test_extra_args_cannot_touch_a_pinned_flag(tmp_path: Path, key: str, value: str | None) -> None:
    rt = agent_runtime(base_url="http://127.0.0.1:9/", root=tmp_path / "rt")
    with pytest.raises(ValueError, match="extra_args may not set"):
        agent_options(
            system_prompt="sp",
            model="claude-haiku-5-5",
            tools=[],
            runtime=rt,
            extra_args={key: value},
        )


@pytest.mark.req("SA-02")
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("cwd", "/elsewhere"),
        ("env", {"X": "1"}),
        ("add_dirs", ["/elsewhere"]),
        ("setting_sources", ["project"]),
        ("settings", "{}"),
        ("cli_path", "/bin/sh"),
        ("disallowed_tools", []),
        ("hooks", {}),
        ("can_use_tool", print),
        ("stderr", print),
        ("agents", {}),
        ("plugins", []),
        ("sandbox", {}),
        ("resume", "x"),
        ("continue_conversation", True),
        ("fork_session", True),
        ("session_store", object()),
        ("user", "root"),
    ],
)
def test_anything_outside_the_allowlist_is_refused(tmp_path: Path, name: str, value: Any) -> None:
    rt = agent_runtime(base_url="http://127.0.0.1:9/", root=tmp_path / "rt")
    extra: dict[str, Any] = {name: value}
    with pytest.raises(TypeError, match=name):
        agent_options(system_prompt="sp", model="claude-haiku-5-5", tools=[], runtime=rt, **extra)


@pytest.mark.req("SA-02")
@pytest.mark.parametrize(
    "mode", ["bypassPermissions", "acceptEdits", "default", "plan", "auto", "dontask", ""]
)
def test_only_dont_ask_is_an_accepted_permission_mode(tmp_path: Path, mode: str) -> None:
    rt = agent_runtime(base_url="http://127.0.0.1:9/", root=tmp_path / "rt")
    extra: dict[str, Any] = {"permission_mode": mode}
    with pytest.raises(TypeError, match="permission_mode"):
        agent_options(system_prompt="sp", model="claude-haiku-5-5", tools=[], runtime=rt, **extra)


@pytest.mark.req("SA-02")
def test_the_allowed_options_reach_the_sdk(tmp_path: Path) -> None:
    rt = agent_runtime(base_url="http://127.0.0.1:9/", root=tmp_path / "rt")
    opts = agent_options(
        system_prompt="sp",
        model="claude-haiku-5-5",
        tools=[],
        runtime=rt,
        mcp_servers={"s": {"type": "sdk"}},
        allowed_tools=["mcp__s__t"],
        max_turns=4,
        include_partial_messages=True,
        permission_mode="dontAsk",
    )
    assert opts.mcp_servers == {"s": {"type": "sdk"}}
    assert opts.allowed_tools == ["mcp__s__t"]
    assert opts.max_turns == 4
    assert opts.include_partial_messages is True
    assert opts.permission_mode == "dontAsk"


@pytest.mark.req("SA-02")
def test_a_pinned_flag_among_others_is_still_refused(tmp_path: Path) -> None:
    rt = agent_runtime(base_url="http://127.0.0.1:9/", root=tmp_path / "rt")
    with pytest.raises(ValueError, match="bare"):
        agent_options(
            system_prompt="sp",
            model="claude-haiku-5-5",
            tools=[],
            runtime=rt,
            extra_args={"debug": None, "bare": None},
        )


@pytest.mark.req("SA-02")
def test_other_extra_args_pass_through_beside_the_pinned_flags(tmp_path: Path) -> None:
    rt = agent_runtime(base_url="http://127.0.0.1:9/", root=tmp_path / "rt")
    opts = agent_options(
        system_prompt="sp",
        model="claude-haiku-5-5",
        tools=[],
        runtime=rt,
        extra_args={"debug": None},
    )
    assert opts.extra_args == {
        "bare": None,
        "no-session-persistence": None,
        "strict-mcp-config": None,
        "debug": None,
    }


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
    assert "no-session-persistence" in opts.extra_args
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
