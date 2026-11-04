"""The one factory for every LLM client in the lab (FOUND-2, decision 0001).

Everything that talks to Claude is built here, so every call goes through the record/replay
proxy (`services/llm-replay`):

- `anthropic_client()`: a direct `anthropic` client with the proxy as base URL and the
  placeholder key.
- `agent_options()`: Agent SDK options whose `claude` CLI subprocess runs with an environment
  built from scratch. The SDK merges `os.environ` into the child environment, so the factory
  starts the bundled CLI through a generated launcher that runs `env -i` with exactly the
  variables below. Nothing else reaches the CLI: no `ANTHROPIC_*`, `CLAUDE_CODE_*` or
  `CLAUDECODE*` from the parent, no real key. With an isolated `CLAUDE_CONFIG_DIR` and `--bare`
  (auth strictly from ANTHROPIC_API_KEY; OAuth and keychain never read), a runtime call cannot
  fall back to the developer's Claude Code subscription.

The real API key never appears here. Only the proxy reads it, from .env.
"""

from __future__ import annotations

import os
import shlex
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLACEHOLDER_API_KEY = "northwind-proxy-placeholder"

# Taken from the parent environment, nothing else is.
PARENT_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "TMPDIR")
# Never inherited, even if a future allowlist entry matched them.
FORBIDDEN_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_", "CLAUDECODE")

# Models (config, not code paths): CLAUDE.md tech stack.
ASSISTANT_MODEL = os.environ.get("ASSISTANT_MODEL", "claude-sonnet-5-5")
ROUTINE_MODEL = os.environ.get("ROUTINE_MODEL", "claude-haiku-5-5")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-sonnet-5-5")


# The CLI's own default max_tokens is 128000 (measured in the FOUND-2 spike). Reservations are
# priced at max_tokens, so the factory always sets it. 8192 leaves room for adaptive thinking plus
# a long answer; raise it per agent when a task needs more.
DEFAULT_MAX_OUTPUT_TOKENS = int(os.environ.get("LLM_MAX_OUTPUT_TOKENS", "8192"))


def proxy_url() -> str:
    """LLM_PROXY_URL, else this worktree's dev proxy port (northwind_common.localenv)."""
    from northwind_common.localenv import proxy_port

    return os.environ.get("LLM_PROXY_URL") or f"http://127.0.0.1:{proxy_port()}"


def _forbidden(name: str) -> bool:
    return any(name.upper().startswith(p) for p in FORBIDDEN_PREFIXES)


def child_env(
    base_url: str,
    config_dir: Path,
    *,
    parent: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The complete environment for the CLI subprocess."""
    from claude_agent_sdk import __version__ as sdk_version

    src = os.environ if parent is None else parent
    env = {k: src[k] for k in PARENT_ENV_ALLOWLIST if k in src and not _forbidden(k)}
    env.update(
        {
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_API_KEY": PLACEHOLDER_API_KEY,
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "DISABLE_AUTOUPDATER": "1",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            # Set by the SDK for its stdio protocol; re-set here because `env -i` drops them.
            "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
            "CLAUDE_AGENT_SDK_VERSION": sdk_version,
            "CLAUDE_CODE_SDK_READS_SESSION_STATE": "1",
        }
    )
    if extra:
        env.update(extra)
    return env


def bundled_cli() -> Path:
    import claude_agent_sdk

    path = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    if not path.exists():
        raise FileNotFoundError(f"bundled claude CLI not found at {path}")
    return path


def write_launcher(directory: Path, env: Mapping[str, str], cli: Path) -> Path:
    """A shell launcher that runs the CLI under `env -i` with exactly `env`."""
    assignments = " ".join(shlex.quote(f"{k}={v}") for k, v in sorted(env.items()))
    script = f'#!/bin/sh\nexec /usr/bin/env -i {assignments} {shlex.quote(str(cli))} "$@"\n'
    path = directory / "claude-clean-env"
    path.write_text(script, encoding="utf-8")
    path.chmod(stat.S_IRWXU)
    return path


@dataclass(frozen=True)
class AgentRuntime:
    """Where one agent run lives: an isolated config dir, an empty work dir, the launcher."""

    root: Path
    config_dir: Path
    workdir: Path
    launcher: Path
    env: dict[str, str]


def agent_runtime(
    *,
    base_url: str | None = None,
    root: Path | None = None,
    extra_env: Mapping[str, str] | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> AgentRuntime:
    root = root or Path(tempfile.mkdtemp(prefix="nw-agent-"))
    config_dir = root / "claude-config"
    workdir = root / "work"
    config_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    env = child_env(
        base_url or proxy_url(),
        config_dir,
        extra={"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_output_tokens), **(extra_env or {})},
    )
    launcher = write_launcher(root, env, bundled_cli())
    return AgentRuntime(
        root=root, config_dir=config_dir, workdir=workdir, launcher=launcher, env=env
    )


def agent_options(
    *,
    system_prompt: str,
    model: str,
    tools: list[str],
    runtime: AgentRuntime | None = None,
    extra_args: Mapping[str, str | None] | None = None,
    **kwargs: Any,
) -> Any:
    """ClaudeAgentOptions for a lab agent. `system_prompt`, `model` and `tools` are required, so no
    run picks up Claude Code's default prompt, model or tools by accident.

    `tools` is the built-in set; `[]` means none. It must be a list: the SDK adds no flag for
    `None` and sends `--tools default` for any other type (FOUND-2 review 8). In-process tools go
    in `mcp_servers` and `allowed_tools`, not here."""
    from claude_agent_sdk import ClaudeAgentOptions

    if not isinstance(tools, list):
        raise TypeError(f"tools must be a list, not {type(tools).__name__}")
    rt = runtime or agent_runtime()
    args: dict[str, str | None] = {"bare": None}
    if extra_args:
        args.update(extra_args)
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        tools=tools,
        cli_path=rt.launcher,
        cwd=rt.workdir,
        setting_sources=[],
        extra_args=args,
        **kwargs,
    )


def anthropic_client(base_url: str | None = None, **kwargs: Any) -> Any:
    """A direct `anthropic.Anthropic` client that talks only to the proxy."""
    import anthropic

    return anthropic.Anthropic(
        base_url=base_url or proxy_url(), api_key=PLACEHOLDER_API_KEY, **kwargs
    )
