"""Row #18: the hold guard's `count_live` and `main()` exit codes, from the criteria.

`count_live` runs on a throwaway schema; `main()` is driven by patching `psycopg.connect` or
`count_live` (it takes no connector), and an unreachable URL for exit 2.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest

from northwind_backend.inventory import HOLD, Line, live_holds
from northwind_backend.inventory.live_holds import DEFAULT_DATABASE_URL, count_live, main
from northwind_backend.inventory.postgres import PostgresInventory, apply_schema, load_stock

pytestmark = [pytest.mark.integration, pytest.mark.req("BE-04")]

START = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
ROWS = [
    {"variant_id": "P0001-BLK-REG-M", "node": "DC-CA", "region": "CA", "on_hand": 9},
    {"variant_id": "P0001-BLK-REG-L", "node": "DC-CA", "region": "CA", "on_hand": 9},
]


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def schema() -> Iterator[tuple[str, str]]:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    name = f"be04_guardtw_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield url, name
    finally:
        admin.execute(f'DROP SCHEMA "{name}" CASCADE')
        admin.close()


def connector(url: str, name: str) -> Any:
    return lambda: psycopg.connect(url, options=f"-csearch_path={name}")


def stocked(schema: tuple[str, str]) -> tuple[Any, PostgresInventory, Clock]:
    connect = connector(*schema)
    with connect() as conn:
        apply_schema(conn)
        load_stock(conn, ROWS)
    clock = Clock()
    return connect, PostgresInventory(connect, clock), clock


def reserve(store: PostgresInventory, key: str, variant: str, units: int = 1) -> None:
    done = store.reserve(
        idempotency_key=key,
        order_id="O900201",
        region="CA",
        postal_code="M5V 2T6",
        lines=[Line(variant, units)],
    )
    assert getattr(done, "value", None) is not None, done


# ---- count_live ------------------------------------------------------------------------------


def test_an_empty_store_has_zero_live_holds(schema: tuple[str, str]) -> None:
    connect, _, clock = stocked(schema)
    assert count_live(connect, clock.now) == 0


def test_one_reservation_over_two_lines_counts_each_hold(schema: tuple[str, str]) -> None:
    connect, store, clock = stocked(schema)
    done = store.reserve(
        idempotency_key="tw-guard-multi",
        order_id="O900202",
        region="CA",
        postal_code="M5V 2T6",
        lines=[Line("P0001-BLK-REG-M", 1), Line("P0001-BLK-REG-L", 1)],
    )
    assert getattr(done, "value", None) is not None, done
    assert count_live(connect, clock.now) == 2  # one per hold, as the SQL counts holds


def test_a_hold_is_live_one_second_before_expiry_and_not_at_or_after_it(
    schema: tuple[str, str],
) -> None:
    connect, store, clock = stocked(schema)
    reserve(store, "tw-guard-1", "P0001-BLK-REG-M")
    expiry = START + HOLD
    assert count_live(connect, expiry - timedelta(seconds=1)) == 1
    assert count_live(connect, expiry) == 0
    assert count_live(connect, expiry + timedelta(seconds=1)) == 0
    assert count_live(connect, clock.now) == 1


def test_a_release_removes_only_its_own_hold(schema: tuple[str, str]) -> None:
    connect, store, clock = stocked(schema)
    reserve(store, "tw-guard-2", "P0001-BLK-REG-M")
    reserve(store, "tw-guard-3", "P0001-BLK-REG-L")
    store.release(idempotency_key="tw-guard-2")
    assert count_live(connect, clock.now) == 1
    store.commit(idempotency_key="tw-guard-3")
    assert count_live(connect, clock.now) == 0


def test_the_guard_agrees_with_the_stores_available_figure(schema: tuple[str, str]) -> None:
    connect, store, clock = stocked(schema)
    reserve(store, "tw-guard-4", "P0001-BLK-REG-M", 4)
    assert getattr(store.available("P0001-BLK-REG-M", "DC-CA"), "value", None) == 5
    assert count_live(connect, clock.now) == 1
    clock.now = START + HOLD
    assert getattr(store.available("P0001-BLK-REG-M", "DC-CA"), "value", None) == 9
    assert count_live(connect, clock.now) == 0


def test_a_non_utc_aware_time_is_accepted(schema: tuple[str, str]) -> None:
    connect, store, clock = stocked(schema)
    reserve(store, "tw-guard-5", "P0001-BLK-REG-M")
    plus_five = timezone(timedelta(hours=5))
    assert count_live(connect, clock.now.astimezone(plus_five)) == 1
    assert count_live(connect, (START + HOLD).astimezone(plus_five)) == 0


def test_a_naive_time_is_refused_even_with_tables_present(schema: tuple[str, str]) -> None:
    connect, _, _ = stocked(schema)
    with pytest.raises(ValueError):
        count_live(connect, datetime(2026, 11, 26, 12, 0))  # noqa: DTZ001


def test_the_guard_never_changes_a_hold(schema: tuple[str, str]) -> None:
    connect, store, clock = stocked(schema)
    reserve(store, "tw-guard-6", "P0001-BLK-REG-M")
    for _ in range(3):
        assert count_live(connect, clock.now) == 1
    assert count_live(connect, START + HOLD) == 0  # reading past expiry sweeps nothing
    assert count_live(connect, clock.now) == 1


# ---- main() exit codes -----------------------------------------------------------------------


def test_main_exits_0_when_none_are_live(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(live_holds, "count_live", lambda connect, now: 0)
    assert main() == 0
    assert "0" in capsys.readouterr().out


def test_main_exits_3_not_0_when_the_tables_do_not_exist(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Step 0's security review L5: absent tables are reported apart from "none live", with the
    database named, so a wrong DATABASE_URL can't pass as a clean check."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/elsewhere")
    monkeypatch.setattr(live_holds, "count_live", lambda connect, now: None)
    assert main() == 3
    assert "'elsewhere'" in capsys.readouterr().out


def test_main_exits_2_on_a_malformed_url_and_prints_none_of_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Re-review N1: a URL that doesn't parse is "not checked", and no part of it is printed."""
    fragment = "not-a-real-pw-fragment"  # synthetic: proves no part of the URL is printed
    monkeypatch.setenv("DATABASE_URL", f"postgresql://u:{fragment}%zz@localhost:5432/northwind")
    assert main() == 2
    out = capsys.readouterr()
    assert fragment not in out.out + out.err


def test_main_exits_2_on_any_database_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(connect: Any, now: datetime) -> int:
        raise psycopg.errors.InsufficientPrivilege("denied")

    monkeypatch.setattr(live_holds, "count_live", denied)
    assert main() == 2


@pytest.mark.parametrize("live", [1, 7])
def test_main_exits_1_when_any_hold_is_live(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], live: int
) -> None:
    monkeypatch.setattr(live_holds, "count_live", lambda connect, now: live)
    assert main() == 1
    assert str(live) in capsys.readouterr().out


def test_main_passes_an_aware_time_to_count_live(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[datetime] = []

    def fake(connect: Any, now: datetime) -> int:
        seen.append(now)
        return 0

    monkeypatch.setattr(live_holds, "count_live", fake)
    assert main() == 0
    assert seen[0].tzinfo is not None
    assert abs((datetime.now(UTC) - seen[0]).total_seconds()) < 60


def test_main_exits_2_when_the_database_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none?connect_timeout=2")
    assert main() == 2
    assert capsys.readouterr().out.strip() == ""  # nothing claimed as a count


def test_main_exits_2_not_0_when_the_connection_fails_mid_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise psycopg.OperationalError("down")

    monkeypatch.setattr(psycopg, "connect", boom)
    assert main() == 2


def test_main_against_the_real_database_reads_live_holds(
    monkeypatch: pytest.MonkeyPatch, schema: tuple[str, str]
) -> None:
    """The real path end to end: DATABASE_URL's connection, with the schema selected by the
    connection's own search_path (so only the throwaway schema is read)."""
    url, name = schema
    real = psycopg.connect

    def routed(conninfo: str, **kwargs: Any) -> Any:
        kwargs.setdefault("options", f"-csearch_path={name}")
        return real(conninfo, **kwargs)

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(psycopg, "connect", routed)
    assert main() == 3  # tables absent in the throwaway schema
    _, store, clock = stocked(schema)
    clock.now = datetime.now(UTC)  # main() reads this process's clock
    assert main() == 0  # tables present, nothing held
    reserve(store, "tw-guard-7", "P0001-BLK-REG-M")
    assert main() == 1
    store.release(idempotency_key="tw-guard-7")
    assert main() == 0
