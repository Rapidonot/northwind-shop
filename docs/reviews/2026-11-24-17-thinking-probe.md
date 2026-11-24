# Row #17: D-A probe, can the assistant's thinking be disabled or capped? ($0, scripted)

2026-11-24, at origin/main `4379687`, claude-agent-sdk 0.2.178 with its bundled CLI, model `claude-sonnet-5-5`.
I ran `agent_options()` against `ScriptedLLM`. A logging launcher recorded the CLI's argv, and the request body the
scripted upstream received was checked with `northwind_llm_replay.runner._settings_allowed`. No LLM call, no spend.

## Result

| Setting | Reaches the CLI argv | `thinking` in the request body (verbatim) | `output_config` | `_settings_allowed` |
|---|---|---|---|---|
| default | (none) | `{"type": "adaptive", "display": "updates"}` | `{"effort": "medium"}` | True |
| `extra_args {"thinking": "disabled"}` | `--thinking disabled` | `{"type": "adaptive", "display": "updates"}` | `{"effort": "medium"}` | True |
| SDK `thinking={"type": "disabled"}` | `--thinking disabled` | `{"type": "adaptive", "display": "updates"}` | `{"effort": "medium"}` | True |
| `extra_args {"max-thinking-tokens": "1024"}` | `--max-thinking-tokens 1024` | `{"type": "adaptive", "display": "updates"}` | `{"effort": "medium"}` | True |
| SDK `thinking={"type": "enabled", "budget_tokens": 1024}` | `--max-thinking-tokens 1024` | `{"type": "adaptive", "display": "updates"}` | `{"effort": "medium"}` | True |
| `extra_args {"effort": "low"}` (reference) | `--effort low` | `{"type": "adaptive", "display": "updates"}` | `{"effort": "low"}` | True |

- **The CLI ignores both flags for this model.** It gets `--thinking disabled` and `--max-thinking-tokens 1024`
  and still sends adaptive thinking. So thinking can be neither disabled nor capped through `agent_options()`.
- **The runner never sees anything it would refuse.** Had the CLI sent `{"type": "disabled"}` or
  `{"type": "enabled", "budget_tokens": …}`, `_settings_allowed` would refuse it: `THINKING_TYPES` is
  `adaptive`/`between_tools` and `THINKING_KEYS` is `type`/`display` (`runner.py:102-103`, `:366`).
- **The one lever that reaches the request is `effort`** (`output_config.effort`). The runner checks only that key's
  name, not its value, so `low` would be forwarded. Its effect on answers is untested.

## Raw output (`probe_thinking2.py`, rerun with every variant through the argv log; the CLI's "Unknown child process" lines removed)

```
== default (no extra_args)
  argv thinking/effort: []
  thinking: {"type": "adaptive", "display": "updates"} | output_config: {"effort": "medium"} | _settings_allowed: True
== extra_args max-thinking-tokens=1024
  argv thinking/effort: ['--max-thinking-tokens', '1024']
  thinking: {"type": "adaptive", "display": "updates"} | output_config: {"effort": "medium"} | _settings_allowed: True
== extra_args thinking=disabled
  argv thinking/effort: ['--thinking', 'disabled']
  thinking: {"type": "adaptive", "display": "updates"} | output_config: {"effort": "medium"} | _settings_allowed: True
== SDK thinking={"type":"disabled"}
  argv thinking/effort: ['--thinking', 'disabled']
  thinking: {"type": "adaptive", "display": "updates"} | output_config: {"effort": "medium"} | _settings_allowed: True
== SDK thinking={"type":"enabled","budget_tokens":1024}
  argv thinking/effort: ['--max-thinking-tokens', '1024']
  thinking: {"type": "adaptive", "display": "updates"} | output_config: {"effort": "medium"} | _settings_allowed: True
== extra_args effort=low (reference)
  argv thinking/effort: ['--effort', 'low']
  thinking: {"type": "adaptive", "display": "updates"} | output_config: {"effort": "low"} | _settings_allowed: True
```

## Scripts

Run with `uv run --offline python <script>`. `probe_thinking.py` was the first pass (body only, no argv log); its three rows gave the same bodies as above.

### probe_thinking2.py

```python
"""D-A probe, part 2: argv check, the SDK's own `thinking` field, and effort for reference."""

import dataclasses, json, os, tempfile
from pathlib import Path
import anyio
from northwind_common.llm import ASSISTANT_MODEL, agent_options, agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_llm_replay.runner import _settings_allowed


def logged(rt, log):
    wrap = rt.root / "logged-launcher"
    wrap.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {log}\nexec {rt.launcher} "$@"\n')
    wrap.chmod(0o700)
    return wrap


VARIANTS = {
    "default (no extra_args)": dict(),
    "extra_args max-thinking-tokens=1024": dict(extra={"max-thinking-tokens": "1024"}),
    "extra_args thinking=disabled": dict(extra={"thinking": "disabled"}),
    'SDK thinking={"type":"disabled"}': dict(sdk={"thinking": {"type": "disabled"}}),
    'SDK thinking={"type":"enabled","budget_tokens":1024}': dict(
        sdk={"thinking": {"type": "enabled", "budget_tokens": 1024}}
    ),
    "extra_args effort=low (reference)": dict(extra={"effort": "low"}),
}


async def turn(opts):
    from claude_agent_sdk import query

    async for _ in query(prompt="hello", options=opts):
        pass


for label, v in VARIANTS.items():
    with ScriptedLLM() as up, tempfile.TemporaryDirectory() as root:
        for _ in range(4):
            up.reply_stream("hi", model=ASSISTANT_MODEL)
        rt = agent_runtime(base_url=up.url, root=Path(root))
        log = Path(root) / "argv.txt"
        opts = agent_options(
            system_prompt="You are a test.",
            model=ASSISTANT_MODEL,
            runtime=rt,
            tools=[],
            max_turns=1,
            extra_args=v.get("extra"),
        )
        opts = dataclasses.replace(opts, cli_path=logged(rt, log), **v.get("sdk", {}))
        try:
            anyio.run(turn, opts)
        except Exception as e:
            print(label, "turn error:", type(e).__name__)
        argv = log.read_text().split("\n") if log.exists() else []
        flags = [
            a
            for i, a in enumerate(argv)
            if "think" in a
            or "effort" in a
            or (i and ("think" in argv[i - 1] or "effort" in argv[i - 1]))
        ]
        print(f"== {label}\n  argv thinking/effort: {flags}")
        for r in (r for r in up.requests if "messages" in (r.get("body") or {})):
            b = r["body"]
            print(
                "  thinking:",
                json.dumps(b.get("thinking", "<absent>")),
                "| output_config:",
                json.dumps(b.get("output_config", "<absent>")),
                "| _settings_allowed:",
                _settings_allowed(b),
            )
```

### probe_thinking.py

```python
"""D-A probe ($0): the body the CLI sends for each thinking setting, checked by the runner."""

import json, sys, tempfile
from pathlib import Path
import anyio
from northwind_common.llm import ASSISTANT_MODEL, agent_options, agent_runtime
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_llm_replay.runner import _settings_allowed

VARIANTS = {
    "default (no extra_args)": None,
    'extra_args {"thinking": "disabled"}': {"thinking": "disabled"},
    'extra_args {"max-thinking-tokens": "1024"}': {"max-thinking-tokens": "1024"},
}


async def turn(opts):
    from claude_agent_sdk import query

    async for _ in query(prompt="hello", options=opts):
        pass


for label, extra in VARIANTS.items():
    with ScriptedLLM() as up, tempfile.TemporaryDirectory() as root:
        for _ in range(4):
            up.reply_stream("hi", model=ASSISTANT_MODEL)
        rt = agent_runtime(base_url=up.url, root=Path(root))
        opts = agent_options(
            system_prompt="You are a test.",
            model=ASSISTANT_MODEL,
            runtime=rt,
            tools=[],
            max_turns=1,
            extra_args=extra,
        )
        try:
            anyio.run(turn, opts)
        except Exception as e:
            print(label, "turn error:", type(e).__name__)
        msgs = [r for r in up.requests if "messages" in (r.get("body") or {})]
        print(f"== {label}: {len(msgs)} message request(s)")
        for r in msgs:
            b = r["body"]
            print(
                "  thinking:",
                json.dumps(b.get("thinking", "<absent>")),
                "| output_config:",
                json.dumps(b.get("output_config", "<absent>")),
                "| context_management:",
                json.dumps(b.get("context_management", "<absent>")),
                "| _settings_allowed:",
                _settings_allowed(b),
            )
```
