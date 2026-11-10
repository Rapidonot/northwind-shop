"""The server-side search cap (decision 0007; RAG-10; CC-10 C10, B3)."""

from __future__ import annotations

import asyncio

import pytest

from northwind_mcp_rag.ledger import MAX_SEARCHES, SearchLedger

pytestmark = [pytest.mark.req("RAG-10"), pytest.mark.req("CC-10")]

EXP = 1_000


def test_the_cap_is_three() -> None:
    assert MAX_SEARCHES == 3


def test_a_turn_gets_three_searches_then_none() -> None:
    ledger = SearchLedger(clock=lambda: 0.0)

    async def go() -> list[bool]:
        return [await ledger.take("turn-a", EXP) for _ in range(5)]

    assert asyncio.run(go()) == [True, True, True, False, False]
    assert ledger.used("turn-a") == 3


def test_turns_are_counted_apart() -> None:
    ledger = SearchLedger(clock=lambda: 0.0)

    async def go() -> list[bool]:
        a = [await ledger.take("turn-a", EXP) for _ in range(3)]
        return [*a, await ledger.take("turn-b", EXP), await ledger.take("turn-a", EXP)]

    assert asyncio.run(go()) == [True, True, True, True, False]


def test_concurrent_searches_never_exceed_three() -> None:
    ledger = SearchLedger(clock=lambda: 0.0)

    async def go() -> list[bool]:
        return list(await asyncio.gather(*(ledger.take("turn-a", EXP) for _ in range(25))))

    results = asyncio.run(go())
    assert results.count(True) == 3 and ledger.used("turn-a") == 3


def test_an_expired_turn_is_forgotten() -> None:
    now = [0.0]
    ledger = SearchLedger(clock=lambda: now[0])

    async def go() -> None:
        for _ in range(3):
            await ledger.take("old", 10)
        now[0] = 11.0
        await ledger.take("new", 100)

    asyncio.run(go())
    assert ledger.used("old") == 0 and len(ledger) == 1
