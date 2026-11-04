"""The pytest plugin: CI forces replay, suites select tests, misses fail with a diff (FOUND-2)."""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]

SUITE_TESTS = """
import pytest
from northwind_common.llm import anthropic_client

@pytest.mark.llm_suite("alpha")
def test_alpha_miss(llm_proxy):
    client = anthropic_client(max_retries=0)
    try:
        client.messages.create(model="claude-haiku-5-5", max_tokens=8,
                               messages=[{"role": "user", "content": "never recorded"}])
    except Exception:
        pass

@pytest.mark.llm_suite("beta")
def test_beta(): pass

def test_plain(): pass

def test_scripted(scripted_llm):
    scripted_llm.reply("canned")
    msg = anthropic_client(max_retries=0).messages.create(
        model="claude-haiku-5-5", max_tokens=8, messages=[{"role": "user", "content": "hi"}])
    assert msg.content[0].text == "canned"
"""


@pytest.mark.req("FOUND-2")
@pytest.mark.parametrize("mode", ["record", "live"])
def test_ci_refuses_spending_modes(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("LLM_MODE", mode)
    pytester.makepyfile("def test_x(): pass")
    result = pytester.runpytest_subprocess()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*CI runs LLM tests in replay only*"])


@pytest.mark.req("FOUND-2")
def test_unknown_mode_is_a_usage_error(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MODE", "yolo")
    pytester.makepyfile("def test_x(): pass")
    assert pytester.runpytest_subprocess().ret == pytest.ExitCode.USAGE_ERROR


@pytest.mark.req("FOUND-2")
def test_miss_fails_the_test_with_a_diff_and_scripted_llm_needs_no_cassette(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_MODE", raising=False)
    pytester.makepyfile(SUITE_TESTS)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=4, errors=1)  # the miss errors at teardown
    result.stdout.fnmatch_lines(["*LLM cassette miss*suite 'alpha'*make record SUITE=alpha*"])


@pytest.mark.req("FOUND-2")
def test_llm_suite_option_selects_one_suite(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_MODE", raising=False)
    pytester.makepyfile(SUITE_TESTS)
    result = pytester.runpytest_subprocess("--llm-suite", "beta")
    result.assert_outcomes(passed=1, deselected=3)
