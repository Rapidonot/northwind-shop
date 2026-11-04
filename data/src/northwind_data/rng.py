"""Seeded randomness (FOUND-1 determinism).

Each stream is keyed by what it is for (`rng(seed, "product", "rain_shell#3")`), so streams never
share state and adding one record does not reshuffle the others. Seeding `random.Random` with a
str is stable across processes and PYTHONHASHSEED (it uses sha512, not the built-in
string hash).
"""

from __future__ import annotations

import random
from functools import lru_cache

from faker import Faker


def rng(seed: int, *parts: str) -> random.Random:
    # Reproducible synthetic data, not security: a seeded PRNG is the point.
    return random.Random(":".join((str(seed), *parts)))  # noqa: S311


@lru_cache(maxsize=1)
def _faker() -> Faker:
    return Faker("en_US")


def fake(seed: int, *parts: str) -> Faker:
    """The shared Faker instance, reseeded for this one use, so call order doesn't matter."""
    instance = _faker()
    instance.seed_instance(":".join((str(seed), *parts)))
    return instance


def canary(seed: int, key: str) -> str:
    """Unique marker planted in internal-only content; red-team tests grep outputs for it."""
    return f"NWCANARY-{rng(seed, 'canary', key).getrandbits(48):012x}"
