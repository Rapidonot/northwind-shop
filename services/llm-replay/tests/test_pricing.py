"""Ledger pricing from `usage` (FOUND-2, NFR-16). Prices: official pricing page, 2026-11-04."""

from __future__ import annotations

import pytest

from northwind_llm_replay.pricing import (
    UnpricedModelError,
    cost_usd,
    estimate_prompt_tokens,
    worst_case_usd,
)

M = 1_000_000


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_sonnet_rates_per_token_type() -> None:
    usage = {
        "input_tokens": M,
        "output_tokens": M,
        "cache_read_input_tokens": M,
        "cache_creation_input_tokens": 2 * M,
        "cache_creation": {"ephemeral_5m_input_tokens": M, "ephemeral_1h_input_tokens": M},
    }
    # 2 in + 10 out + 0.10 read (0.05x) + 2.50 5m write + 4 1h write
    assert cost_usd("claude-sonnet-5-5", usage) == pytest.approx(18.60)


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_cache_writes_default_to_5m_without_breakdown() -> None:
    assert cost_usd("claude-sonnet-5-5", {"cache_creation_input_tokens": M}) == pytest.approx(2.50)


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_haiku_base_tier() -> None:
    usage = {"input_tokens": 100_000, "output_tokens": 1_000}
    assert cost_usd("claude-haiku-5-5", usage) == pytest.approx(
        100_000 * 0.10 / M + 1_000 * 0.50 / M
    )


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_haiku_long_prompt_tier_counts_cache_tokens_and_applies_to_all_types() -> None:
    # 60K uncached + 41K cache read = 101K prompt tokens: over 100K, so every rate is the high tier.
    usage = {"input_tokens": 60_000, "cache_read_input_tokens": 41_000, "output_tokens": 2_000}
    expected = (60_000 * 0.50 + 41_000 * 0.05 + 2_000 * 2.50) / M
    assert cost_usd("claude-haiku-5-5", usage) == pytest.approx(expected)
    # Exactly 100K stays on the base tier.
    at = {"input_tokens": 59_000, "cache_read_input_tokens": 41_000, "output_tokens": 2_000}
    assert cost_usd("claude-haiku-5-5", at) == pytest.approx(
        (59_000 * 0.10 + 41_000 * 0.01 + 2_000 * 0.50) / M
    )


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_unknown_model_is_refused_not_guessed() -> None:
    with pytest.raises(UnpricedModelError):
        cost_usd("claude-sonnet-9", {"input_tokens": 1})
    with pytest.raises(UnpricedModelError):
        worst_case_usd("claude-sonnet-9", 100, 100)


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_worst_case_uses_dearest_input_rate_and_full_max_tokens() -> None:
    # 2,000 body bytes -> 1,000 estimated tokens at $4 (1h write) + 8,192 x $10.
    assert estimate_prompt_tokens(2_000) == 1_000
    assert worst_case_usd("claude-sonnet-5-5", 2_000, 8_192) == pytest.approx(
        (1_000 * 4 + 8_192 * 10) / M
    )
    # CLI default max_tokens (SPIKE.md §5): a single Sonnet call could cost > $1.28.
    assert worst_case_usd("claude-sonnet-5-5", 0, 128_000) == pytest.approx(1.28)


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_worst_case_switches_haiku_tier_on_estimated_prompt() -> None:
    small = worst_case_usd("claude-haiku-5-5", 200_000, 1_000)  # 100K est: base tier
    big = worst_case_usd("claude-haiku-5-5", 200_002, 1_000)  # 100,001 est: high tier
    assert small == pytest.approx((100_000 * 0.20 + 1_000 * 0.50) / M)
    assert big == pytest.approx((100_001 * 1.00 + 1_000 * 2.50) / M)
