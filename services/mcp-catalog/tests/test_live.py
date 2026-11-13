"""The live store (CC-10, row #10b; Q4, HR2): it seeds only from a verified dataset, takes writes
through the two setters, and reads them back at once."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from northwind_data.dataset import FIXTURES_DIR
from northwind_data.ingest_guard import DatasetMismatchError
from northwind_mcp_catalog import app as catalog_app
from northwind_mcp_catalog.live import FixtureCatalog

pytestmark = pytest.mark.req("CC-10")

VID = "P0001-BLK-REG-L"


@pytest.fixture
def tampered(tmp_path: Path) -> Path:
    """A copy with one byte of prices.jsonl changed. The manifest is NOT re-hashed."""
    target = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_DIR, target)
    path = target / "prices.jsonl"
    data = bytearray(path.read_bytes())
    index = data.index(b'"amount":"') + len(b'"amount":"')
    data[index] = ord("9") if data[index] != ord("9") else ord("8")
    path.write_bytes(bytes(data))
    return target


def test_a_tampered_copy_is_refused(tampered: Path) -> None:
    with pytest.raises(DatasetMismatchError, match=r"prices\.jsonl"):
        FixtureCatalog(tampered)


def test_the_app_refuses_to_start_on_a_tampered_dataset(
    tampered: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NORTHWIND_MCP_TOKEN_KEY", "k" * 40)
    monkeypatch.setattr(catalog_app, "FixtureCatalog", lambda: FixtureCatalog(tampered))
    with pytest.raises(DatasetMismatchError):
        catalog_app.build()


def test_an_untouched_copy_seeds_the_store(tmp_path: Path) -> None:
    target = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_DIR, target)
    catalog = FixtureCatalog(target)
    assert catalog.product("P0001") is not None
    assert catalog.variant(VID) is not None


def test_the_setters_are_not_exposed_as_tools() -> None:
    from northwind_mcp_catalog.tools import TOOLS

    assert not {"set_price", "set_stock"} & set(TOOLS)


def test_reads_reflect_the_setters_immediately() -> None:
    catalog = FixtureCatalog()
    price = catalog.price(VID, "UK")
    assert price is not None
    catalog.set_price(VID, "UK", "12.30", "GBP")
    after = catalog.price(VID, "UK")
    assert after is not None
    assert (after.amount, after.currency) == ("12.30", "GBP")
    catalog.set_price(VID, "UK", "7", "GBP")
    again = catalog.price(VID, "UK")
    assert again is not None and again.amount == "7.00"
    catalog.set_stock(VID, "UK", 3)
    assert catalog.stock(VID, "UK") == 3
    catalog.set_stock(VID, "UK", 0)
    assert catalog.stock(VID, "UK") == 0
    # a second store is independent
    assert FixtureCatalog().price(VID, "UK") == price


def test_an_unknown_price_or_stock_row_is_none() -> None:
    catalog = FixtureCatalog()
    assert catalog.price("P9999-BLK-REG-M", "UK") is None
    assert catalog.stock("P9999-BLK-REG-M", "UK") is None
    assert catalog.product("P9999") is None
    assert catalog.variant("P9999-BLK-REG-M") is None


@pytest.mark.parametrize("setter", ["price", "stock"])
def test_a_setter_refuses_an_unknown_variant(setter: str) -> None:
    catalog = FixtureCatalog()
    with pytest.raises(KeyError):
        if setter == "price":
            catalog.set_price("P9999-BLK-REG-M", "UK", "1.00", "GBP")
        else:
            catalog.set_stock("P9999-BLK-REG-M", "UK", 1)


@pytest.mark.parametrize(
    ("amount", "region", "currency"),
    [
        ("abc", "UK", "GBP"),
        ("", "UK", "GBP"),
        ("0", "UK", "GBP"),
        ("-1.00", "UK", "GBP"),
        ("1.005", "UK", "GBP"),
        ("NaN", "UK", "GBP"),
        ("Infinity", "UK", "GBP"),
        ("1.00", "uk", "GBP"),
        ("1.00", "UK", "gbp"),
        ("1.00", "UK", "GBPX"),
    ],
)
def test_set_price_refuses_bad_input_and_changes_nothing(
    amount: str, region: str, currency: str
) -> None:
    catalog = FixtureCatalog()
    before = catalog.price(VID, "UK")
    with pytest.raises(ValueError):
        catalog.set_price(VID, region, amount, currency)
    assert catalog.price(VID, "UK") == before


@pytest.mark.parametrize("on_hand", [-1, True, 1.5, "3", None])
def test_set_stock_refuses_bad_input_and_changes_nothing(on_hand: object) -> None:
    catalog = FixtureCatalog()
    before = catalog.stock(VID, "UK")
    with pytest.raises(ValueError):
        catalog.set_stock(VID, "UK", on_hand)  # type: ignore[arg-type]
    assert catalog.stock(VID, "UK") == before


def test_set_stock_refuses_a_bad_region() -> None:
    with pytest.raises(ValueError):
        FixtureCatalog().set_stock(VID, "u", 1)


# ---- re-review N3 and N5 (#10b) -----------------------------------------------------------------


def _patched_line(data: bytes, needle: bytes, old: bytes, new: bytes) -> bytes:
    lines = data.split(b"\n")
    i = next(n for n, line in enumerate(lines) if needle in line and old in line)
    lines[i] = lines[i].replace(old, new, 1)
    return b"\n".join(lines)


def test_the_store_parses_the_verified_bytes_not_a_second_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L2 (re-review N3, pass-3 P2): every file the store parses is what `load_verified` returned.
    A distinct value is patched into each of the four, and a re-read of any one of them from disk
    would show the fixture's value instead."""
    from northwind_data.ingest_guard import VerifiedDataset, load_verified
    from northwind_mcp_catalog import live

    real = load_verified(FIXTURES_DIR)
    files = dict(real.files)
    files["products.jsonl"] = _patched_line(
        files["products.jsonl"], b'"product_id":"P0001"', b'"name":"', b'"name":"PATCHED '
    )
    files["variants.jsonl"] = _patched_line(
        files["variants.jsonl"], VID.encode(), b'"sku":"', b'"sku":"PATCHED-'
    )
    lines = files["prices.jsonl"].split(b"\n")
    i = next(n for n, line in enumerate(lines) if VID.encode() in line and b'"UK"' in line)
    lines[i] = b'{"amount":"1.23","currency":"GBP","product_id":"P0001","region":"UK",' + (
        b'"variant_id":"' + VID.encode() + b'"}'
    )
    files["prices.jsonl"] = b"\n".join(lines)
    stock = [
        b'{"node":"DC-UK","on_hand":777,"product_id":"P0001","region":"UK","variant_id":"'
        + VID.encode()
        + b'"}'
    ]
    files["stock.jsonl"] = b"\n".join(
        [line for line in files["stock.jsonl"].split(b"\n") if VID.encode() + b'"' not in line]
        + stock
    )
    patched = VerifiedDataset(real.dataset_sha256, files)
    monkeypatch.setattr(live, "load_verified", lambda _dir: patched)
    catalog = FixtureCatalog(FIXTURES_DIR)
    price = catalog.price(VID, "UK")
    product = catalog.product("P0001")
    variant = catalog.variant(VID)
    assert price is not None and price.amount == "1.23"
    assert product is not None and product.name.startswith("PATCHED ")
    assert variant is not None and variant.sku.startswith("PATCHED-")
    assert catalog.stock(VID, "UK") == 777


def test_a_line_separator_inside_a_value_does_not_split_a_record() -> None:
    """N5: records end at `\\n` only, as the writer writes them."""
    from northwind_mcp_catalog.live import _jsonl

    data = '{"a":"x\u2028y\u2029z\u0085w"}\n{"a":"b"}\n'.encode()
    assert _jsonl(data) == [{"a": "x\u2028y\u2029z\u0085w"}, {"a": "b"}]
