"""pytest plugin for LLM record/replay (FOUND-2). Auto-loaded through the `pytest11` entry point.

- `LLM_MODE` = replay (default) | record | live. With `CI` set, anything but replay is a usage
  error, so CI can never spend.
- `llm_proxy` fixture: a proxy for the test's suite (`@pytest.mark.llm_suite("<name>")`, cassettes
  under cassettes/<name>/). It sets `LLM_PROXY_URL` so `northwind_common.llm` clients use it. Each
  test gets its own `/_t/<node id>` base URL. A cassette miss fails the test with the diff against
  the nearest cassette, and so does a cassette the scanner refused to write.
  Sequence-matched replays (`LLM_REPLAY_MATCH=sequence`) pass with a StaleReplayWarning.
- `scripted_llm` fixture: canned answers for plumbing tests, no cassette needed.
- `--llm-suite NAME`: run only the tests of one suite (`make record SUITE=NAME`).
"""

from __future__ import annotations

import os
import uuid
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from northwind_common import localenv
from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.proxy import (
    MODES,
    ProxyConfig,
    ProxyState,
    RunningProxy,
    encode_test_id,
)
from northwind_llm_replay.scripted import ScriptedLLM

DEFAULT_RUN_BUDGET_USD = "5.00"  # SPIKE.md §5: a 20-call slice at the ~$0.20 worst case
DEFAULT_BUDGET_USD = "90"
_SESSION_RUN_ID = f"pytest-{uuid.uuid4().hex[:12]}"


class StaleReplayWarning(UserWarning):
    """A recording was served by test ID and order, not by request hash: the result is stale."""


def llm_mode() -> str:
    return os.environ.get("LLM_MODE", "replay")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--llm-suite", default=None, help="only run tests marked llm_suite(NAME)")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "llm_suite(name): cassette suite (cassettes/<name>/) for the llm_proxy fixture"
    )
    mode = llm_mode()
    if mode not in MODES:
        raise pytest.UsageError(f"LLM_MODE must be one of {MODES}, not {mode!r}")
    if os.environ.get("CI") and mode != "replay":
        raise pytest.UsageError("CI runs LLM tests in replay only (decision 0001); unset LLM_MODE")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    suite = config.getoption("--llm-suite")
    if not suite:
        return
    keep: list[pytest.Item] = []
    drop: list[pytest.Item] = []
    for item in items:
        marker = item.get_closest_marker("llm_suite")
        (keep if marker and marker.args and marker.args[0] == suite else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


@dataclass
class LLMProxyHandle:
    base_url: str
    suite: str
    mode: str
    running: RunningProxy

    @property
    def state(self) -> ProxyState:
        return self.running.proxy.state


@pytest.fixture
def llm_proxy(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[LLMProxyHandle]:
    marker = request.node.get_closest_marker("llm_suite")
    if marker is None or not marker.args:
        pytest.fail("tests using llm_proxy need @pytest.mark.llm_suite('<suite>')")
    suite = str(marker.args[0])
    mode = llm_mode()
    repo = Path(request.config.rootpath)
    ledger = None
    if mode != "replay":
        ledger = Ledger(
            localenv.ledger_path(),
            budget_usd=float(os.environ.get("LLM_BUDGET_USD", DEFAULT_BUDGET_USD)),
            run_budget_usd=float(os.environ.get("LLM_RUN_BUDGET_USD", DEFAULT_RUN_BUDGET_USD)),
            run_id=os.environ.get("LLM_RUN_ID", _SESSION_RUN_ID),
            suite=suite,
        )
    cfg = ProxyConfig(
        mode=mode,
        suite=suite,
        cassette_root=repo / "cassettes",
        ledger=ledger,
        env_file=localenv.env_file(repo),
        log_path=None if mode == "replay" else repo / ".llm-proxy-log" / f"{suite}.jsonl",
        match=os.environ.get("LLM_REPLAY_MATCH", "hash"),
    )
    with RunningProxy(cfg) as running:
        handle = LLMProxyHandle(
            base_url=f"{running.url}/_t/{encode_test_id(request.node.nodeid)}",
            suite=suite,
            mode=mode,
            running=running,
        )
        monkeypatch.setenv("LLM_PROXY_URL", handle.base_url)
        yield handle
    state = handle.state
    if state.scan_failures:
        pytest.fail("cassette refused by the secret/PAN scanner: " + "; ".join(state.scan_failures))
    if state.misses:
        first = state.misses[0]
        pytest.fail(
            f"LLM cassette miss ({len(state.misses)} request(s); suite {suite!r}, mode {mode}). "
            f"The model input changed: re-record on purpose with `make record SUITE={suite}`.\n"
            f"Diff of the first miss against the nearest cassette:\n{first['diff']}",
            pytrace=False,
        )
    if state.stale:
        request.node.user_properties.append(("llm_stale", list(state.stale)))
        warnings.warn(
            StaleReplayWarning(f"served by sequence, not by hash (stale): {state.stale}"),
            stacklevel=1,
        )


@pytest.fixture
def scripted_llm(monkeypatch: pytest.MonkeyPatch) -> Iterator[ScriptedLLM]:
    with ScriptedLLM() as llm:
        monkeypatch.setenv("LLM_PROXY_URL", llm.url)
        yield llm
