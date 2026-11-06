"""Price a Messages API call from its `usage` (FOUND-2 spend guard, NFR-16).

Source: https://platform.claude.com/docs/en/about-claude/pricing, read 2026-11-04.
Prices are USD per million tokens. Batch rates are not here: the proxy refuses the Batches
endpoint and RAG-02 does not use it (decision 0010), so there is nothing to price.

Claude Haiku 5.5 has two tiers. A request whose prompt (input + cache reads + cache writes)
is over 100,000 tokens pays the higher rates for every token type, including output.
Claude Sonnet 5.5 has one tier for its whole 1M context.

Unknown models raise instead of guessing, so the proxy fails closed on a model it cannot price.

Standard library only: the runner's `.venv-runner` holds just this package and llm-replay (RAG-13).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Rates:
    input: float
    output: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float

    @property
    def max_input(self) -> float:
        """The highest rate any prompt token can be billed at (for worst-case reservations)."""
        return max(self.input, self.cache_write_5m, self.cache_write_1h, self.cache_read)


@dataclass(frozen=True)
class ModelPrice:
    base: Rates
    # (threshold, rates): prompts with more than `threshold` tokens use `rates`.
    long_prompt: tuple[int, Rates] | None = None

    def rates_for(self, prompt_tokens: int) -> Rates:
        if self.long_prompt and prompt_tokens > self.long_prompt[0]:
            return self.long_prompt[1]
        return self.base


PRICES: dict[str, ModelPrice] = {
    "claude-sonnet-5-5": ModelPrice(
        base=Rates(
            input=2.00, output=10.00, cache_write_5m=2.50, cache_write_1h=4.00, cache_read=0.10
        )
    ),
    "claude-haiku-5-5": ModelPrice(
        base=Rates(
            input=0.10, output=0.50, cache_write_5m=0.125, cache_write_1h=0.20, cache_read=0.01
        ),
        long_prompt=(
            100_000,
            Rates(
                input=0.50, output=2.50, cache_write_5m=0.625, cache_write_1h=1.00, cache_read=0.05
            ),
        ),
    ),
}

PER_TOKEN = 1 / 1_000_000


class UnpricedModelError(ValueError):
    """The proxy has no price for this model, so it will not spend on it."""


def price_for(model: str) -> ModelPrice:
    try:
        return PRICES[model]
    except KeyError:
        raise UnpricedModelError(
            f"no price for model {model!r}; add it to pricing.PRICES"
        ) from None


def _int(usage: Mapping[str, Any], key: str) -> int:
    value = usage.get(key) or 0
    return int(value)


COST_DECIMALS = 9  # finer than the cheapest token rate


def _fixed(usd: float) -> float:
    """Fixed precision (FOUND-2 review 10, pulled forward by RAG-12). A raw float's long digit run
    can pass the scanner's Luhn check, which would refuse a recording already paid for."""
    return round(usd, COST_DECIMALS)


def cost_usd(model: str, usage: Mapping[str, Any]) -> float:
    """Cost of one response, from the Messages API `usage` object."""
    price = price_for(model)
    uncached = _int(usage, "input_tokens")
    read = _int(usage, "cache_read_input_tokens")
    written = _int(usage, "cache_creation_input_tokens")
    detail = usage.get("cache_creation") or {}
    w1h = _int(detail, "ephemeral_1h_input_tokens")
    # Anything not reported as 1h is billed as a 5-minute write (the default TTL).
    w5m = max(written - w1h, _int(detail, "ephemeral_5m_input_tokens"))
    prompt = uncached + read + w5m + w1h
    r = price.rates_for(prompt)
    total = (
        uncached * r.input
        + read * r.cache_read
        + w5m * r.cache_write_5m
        + w1h * r.cache_write_1h
        + _int(usage, "output_tokens") * r.output
    )
    return _fixed(total * PER_TOKEN)


def estimate_prompt_tokens(body_bytes: int) -> int:
    """Upper-bound token estimate for a request body. Real text runs ~4 bytes per token;
    dividing by 2 keeps the estimate on the safe side for JSON and non-English text."""
    return math.ceil(body_bytes / 2)


def worst_case_usd(model: str, body_bytes: int, max_tokens: int) -> float:
    """The most a request can cost: every prompt token at the dearest input rate and the full
    `max_tokens` of output, at the long-prompt tier if the estimate crosses it."""
    price = price_for(model)
    prompt = estimate_prompt_tokens(body_bytes)
    r = price.rates_for(prompt)
    return _fixed((prompt * r.max_input + max_tokens * r.output) * PER_TOKEN)
