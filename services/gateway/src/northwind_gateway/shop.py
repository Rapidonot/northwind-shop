"""The shop pages' reads (SA-01 PR 1; hard rules 2, 7, 10).

- `GET /api/departments`: the departments that have public products, for the top nav.
- `GET /api/products?department=&q=`: the public products in a department, or whose name holds `q`,
  each with its live lowest price. Products with a photo come first, otherwise in catalog order.
- `GET /api/product?id=`: one public product's record, with each variant's live price.

SA-01 PR 2 (only the products on screen are priced, hard rule 2):
- `GET /api/departments` also gives each department's count and the total, in catalog order (the
  order departments first appear in the catalog). It reads no price.
- `GET /api/products` takes `offset`/`limit` (a page of at most 48) and `ids` (up to 48 product IDs,
  for the home hero and the bag). With either, the gateway lists every match unpriced
  (`priced: false`), orders it (photos first, or in the order of `ids`), takes the page and prices
  only that page (`product_ids`). Without either it is PR 1's full listing, which the portal no
  longer asks for.
- `GET /api/reviews?id=&limit=`: a public product's public reviews, newest first, and the total,
  through the portal-only `list_reviews`. Shopper text, shown by the page as plain text (hard rule
  6); every field is type-checked here.

The gateway reads mcp-catalog itself, as its own agent, `shopper-portal`, never through a model. It
uses `get_product` and two portal-only tools, `browse_products` and `get_prices`
(`tool_scopes.PORTAL_CATALOG_TOOLS`). The region comes from the server: `shopper_caller`, from the
signed session cookie, never from a query parameter. Prices are read from the live store on every
request (hard rule 2), and the product facts come from the catalog record (`data/fixtures`).

Failures (hard rule 10):
- no key, mcp-catalog down, a refused token or an unreadable result: one fixed `transient`
  envelope, status 503. The portal then shows "unavailable", never an empty list;
- an unknown or internal product: the same fixed `not_found`, status 404 (hard rule 7);
- a malformed ID or query: the fixed validation envelope, status 422;
- card data in any query name or value, in any repeat: the chat's fixed card refusal, before any
  lookup or app log (`no_card_data`, a dependency of every route here). uvicorn's access log is
  outside the app, so `access_log` drops every query string from it (security review F1).

Nothing from a failure reaches the response or a log beyond its type name.

Photos are files named `<product id>.webp` in `PORTAL_PHOTO_DIR` (default
`apps/portal/public/products/800`), listed on each request, so photos copied in while the server
runs show at once.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import httpx2
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import PORTAL_AGENT
from northwind_assistant.rag_client import quiet_client_logs
from northwind_common import catalog_tool
from northwind_common.cards import contains_card_data
from northwind_common.localenv import mcp_catalog_url
from northwind_common.mcp_token import CATALOG_SERVER, TokenKeyError, bearer, mint, signing_key
from northwind_common.tool_scopes import BROWSE_PRODUCTS, GET_PRICES, LIST_REVIEWS
from northwind_gateway.chat import CardNumberRefused
from northwind_gateway.settings import shopper_caller

log = logging.getLogger(__name__)

GET_PRODUCT = "get_product"
PRODUCT_ID = re.compile(r"P\d{4}", re.ASCII)
# mcp-catalog's own variant pattern: an ID the browser puts in a URL is checked first (review F2).
VARIANT_ID = re.compile(r"P\d{4}(?:-[A-Z0-9]{1,16}){1,4}", re.ASCII)
PHOTO_NAME = re.compile(r"(P\d{4})\.webp", re.ASCII)
MAX_QUERY_CHARS = 64
MAX_PAGE = 48  # browse_products' own cap on `product_ids`
MAX_REVIEWS = 50
REVIEW_ID = re.compile(r"R\d{5}", re.ASCII)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
NO_STORE = {"Cache-Control": "no-store"}

UNAVAILABLE = "The shop isn't available right now."
NOT_FOUND = "No product with that ID."
INVALID = "That request could not be accepted."

DEFAULT_PHOTO_DIR = Path(__file__).resolve().parents[4] / "apps/portal/public/products/800"


def no_card_data(request: Request) -> None:
    """Refuses a request whose query holds card data in any name or value, in any repeat, or split
    across them (test-writer, SA-01 PR 1: FastAPI's `Query()` sees only the last value of a named
    parameter; security review F1: names too)."""
    texts = [text for pair in request.query_params.multi_items() for text in pair]
    if any(contains_card_data(text) for text in texts) or contains_card_data("\n".join(texts)):
        raise CardNumberRefused


router = APIRouter(dependencies=[Depends(no_card_data)])


class Unreadable(Exception):
    """A result that is not what the tool returns; holds no text on purpose."""


class NotFound(Exception):
    """The tool's `not_found`: an unknown or internal product, alike (hard rule 7)."""


def _envelope(status: int, category: str, retryable: bool, message: str) -> JSONResponse:
    body = {
        "isError": True,
        "errorCategory": category,
        "isRetryable": retryable,
        "message": message,
    }
    return JSONResponse(status_code=status, content=body, headers=NO_STORE)


def unavailable() -> JSONResponse:
    return _envelope(503, "transient", True, UNAVAILABLE)


def not_found() -> JSONResponse:
    return _envelope(404, "validation", False, NOT_FOUND)


def invalid() -> JSONResponse:
    return _envelope(422, "validation", False, INVALID)


def photo_dir() -> Path:
    configured = os.environ.get("PORTAL_PHOTO_DIR")
    return Path(configured) if configured else DEFAULT_PHOTO_DIR


def photo_ids(directory: Path | None = None) -> frozenset[str]:
    """The product IDs with a photo. Only regular files named exactly like `P0001.webp` count (a
    directory or a dangling link of that name does not); a missing directory means no photos, never
    an error."""
    found: set[str] = set()
    try:
        with os.scandir(directory or photo_dir()) as entries:
            for entry in entries:
                match = PHOTO_NAME.fullmatch(entry.name)
                if match is not None and entry.is_file():
                    found.add(match.group(1))
    except OSError:
        return frozenset()
    return frozenset(found)


@dataclass(frozen=True)
class ShopReader:
    """The gateway's catalog reads for the shop pages, as `shopper-portal`, with the caller's
    region (and session, when there is one) in the token."""

    url: str
    key: bytes = field(repr=False)

    async def call(self, caller: Caller, tool: str, args: dict[str, Any]) -> Any:
        token, _ = mint(
            audience="shopper",
            region=caller.region,
            agent=PORTAL_AGENT,
            key=self.key,
            server=CATALOG_SERVER,
            session=caller.session,
        )
        quiet_client_logs()
        # Closed here: `streamable_http_client` leaves a client it was given open (review F4).
        async with (
            httpx2.AsyncClient(
                headers=bearer(token),
                timeout=httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
                trust_env=False,  # an HTTP_PROXY in the environment must never carry the token
            ) as http,
            Client(streamable_http_client(self.url, http_client=http)) as client,
        ):
            result = await client.call_tool(tool, args)
        if len(result.content) != 1:
            raise Unreadable
        text = getattr(result.content[0], "text", "")
        if result.is_error:
            if _error_code(text) == "not_found":
                raise NotFound
            raise Unreadable
        if tool == GET_PRODUCT:
            labelled = catalog_tool.unlabel(text)
            if labelled is None or labelled[0] != GET_PRODUCT:
                raise Unreadable
            return labelled[1]
        try:
            return json.loads(text)
        except ValueError:
            raise Unreadable from None


def _error_code(text: str) -> str | None:
    try:
        body = json.loads(text)
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


def shop_reader() -> ShopReader | None:
    """The reader, with the key read from the environment on each request (hard rule 4). None
    without a key: the routes answer the fixed 503, never a 500."""
    try:
        return ShopReader(mcp_catalog_url(), signing_key())
    except TokenKeyError:
        return None


Reader = Annotated[ShopReader | None, Depends(shop_reader)]
Shopper = Annotated[Caller, Depends(shopper_caller)]


def _money(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"amount", "currency"}:
        raise Unreadable
    if not all(isinstance(v, str) for v in value.values()):
        raise Unreadable
    return {"amount": value["amount"], "currency": value["currency"]}


def _tile(product: Any, photos: frozenset[str]) -> dict[str, Any]:
    if not isinstance(product, dict) or not isinstance(product.get("product_id"), str):
        raise Unreadable
    if not PRODUCT_ID.fullmatch(product["product_id"]):
        raise Unreadable  # it goes into an image URL and a link (review F2)
    return {
        "product_id": product["product_id"],
        "name": str(product.get("name", "")),
        "brand": str(product.get("brand", "")),
        "department": str(product.get("department", "")),
        "from_price": _money(product.get("from_price")),
        "to_price": _money(product.get("to_price")),
        "photo": product["product_id"] in photos,
    }


def _summary_id(product: Any) -> str:
    product_id = product.get("product_id") if isinstance(product, dict) else None
    if not isinstance(product_id, str) or not PRODUCT_ID.fullmatch(product_id):
        raise Unreadable  # it goes into a priced read, an image URL and a link (review F2)
    return product_id


async def _browse(reader: ShopReader, caller: Caller, args: dict[str, Any]) -> list[Any]:
    body = await reader.call(caller, BROWSE_PRODUCTS, args)
    if not isinstance(body, dict) or body.get("region") != caller.region:
        raise Unreadable
    products = body.get("products")
    if not isinstance(products, list):
        raise Unreadable
    return products


def _query_value(value: str | None) -> str | None:
    """A query value, stripped; None when absent or blank. Card data is refused before anything
    else reads it; too long is invalid."""
    if value is None:
        return None
    if contains_card_data(value):
        raise CardNumberRefused
    value = value.strip()
    if len(value) > MAX_QUERY_CHARS:
        raise ValueError("too long")
    return value or None


@router.get("/api/departments")
async def departments(caller: Shopper, reader: Reader) -> JSONResponse:
    if reader is None:
        log.error("shop read failed: no key")
        return unavailable()
    try:
        # SA-01 PR 2: counted unpriced; no price is read for the nav.
        products = await _browse(reader, caller, {"priced": False})
        counts: dict[str, int] = {}
        for product in products:
            _summary_id(product)
            name = str(product.get("department", ""))
            if name:
                counts[name] = counts.get(name, 0) + 1
    except Exception as error:  # noqa: BLE001 - its text must not reach the shopper or a log
        log.error("shop read failed: %s", type(error).__name__)
        return unavailable()
    # Catalog order, the order departments first appear in the catalog (the user's look round 1).
    names = list(counts)
    body = {"departments": names, "counts": counts, "total": sum(counts.values())}
    return JSONResponse(content=body, headers=NO_STORE)


def _int_value(value: str | None, low: int, high: int) -> int | None:
    if value is None:
        return None
    if contains_card_data(value):
        raise CardNumberRefused
    if not value.isascii() or not value.isdigit() or len(value) > 4:
        raise ValueError("not a small number")
    number = int(value)
    if not low <= number <= high:
        raise ValueError("out of range")
    return number


def _ids_value(value: str | None) -> list[str] | None:
    text = _query_value(value)
    if text is None:
        return None
    ids = [part.strip() for part in text.split(",")]
    if not 1 <= len(ids) <= MAX_PAGE or not all(PRODUCT_ID.fullmatch(i) for i in ids):
        raise ValueError("bad ids")
    return list(dict.fromkeys(ids))  # once each, first order kept


async def _page(
    reader: ShopReader,
    caller: Caller,
    args: dict[str, Any],
    ids: list[str] | None,
    offset: int,
    limit: int,
) -> tuple[int, list[dict[str, Any]]]:
    """Every match listed unpriced and ordered, then only the page priced (hard rule 2)."""
    photos = photo_ids()
    listing = [
        _summary_id(p)
        for p in await _browse(reader, caller, {**args, "priced": False, **_id_args(ids)})
    ]
    if ids is not None:
        listed = set(listing)
        ordered = [i for i in ids if i in listed]
    else:
        ordered = sorted(listing, key=lambda pid: pid not in photos)  # stable: catalog order
    page = ordered[offset : offset + limit]
    if not page:
        return len(ordered), []
    priced = {
        tile["product_id"]: tile
        for tile in (_tile(p, photos) for p in await _browse(reader, caller, {"product_ids": page}))
    }
    # A product hidden between the two reads is dropped, never shown unpriced.
    return len(ordered), [priced[pid] for pid in page if pid in priced]


def _id_args(ids: list[str] | None) -> dict[str, Any]:
    return {} if ids is None else {"product_ids": ids}


@router.get("/api/products")
async def products(
    caller: Shopper,
    reader: Reader,
    department: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    ids: Annotated[str | None, Query()] = None,
    offset: Annotated[str | None, Query()] = None,
    limit: Annotated[str | None, Query()] = None,
) -> JSONResponse:
    try:
        wanted_department, text = _query_value(department), _query_value(q)
        wanted_ids = _ids_value(ids)
        start = _int_value(offset, 0, 9999)
        size = _int_value(limit, 1, MAX_PAGE)
    except ValueError:
        return invalid()
    if reader is None:
        log.error("shop read failed: no key")
        return unavailable()
    args: dict[str, Any] = {}
    if wanted_department is not None:
        args["department"] = wanted_department
    if text is not None:
        args["text"] = text
    if wanted_ids is not None or size is not None or start is not None:
        try:
            total, page = await _page(
                reader,
                caller,
                args,
                wanted_ids,
                start or 0,
                size or (len(wanted_ids) if wanted_ids else MAX_PAGE),
            )
        except Exception as error:  # noqa: BLE001
            log.error("shop read failed: %s", type(error).__name__)
            return unavailable()
        body = {"region": caller.region, "total": total, "offset": start or 0, "products": page}
        return JSONResponse(content=body, headers=NO_STORE)
    try:
        photos = photo_ids()
        tiles = [_tile(p, photos) for p in await _browse(reader, caller, args)]
    except Exception as error:  # noqa: BLE001
        log.error("shop read failed: %s", type(error).__name__)
        return unavailable()
    tiles.sort(key=lambda tile: not tile["photo"])  # stable: catalog order within each group
    body = {"region": caller.region, "total": len(tiles), "products": tiles}
    return JSONResponse(content=body, headers=NO_STORE)


@router.get("/api/product")
async def product(
    caller: Shopper, reader: Reader, id: Annotated[str | None, Query()] = None
) -> JSONResponse:
    try:
        product_id = _query_value(id)
    except ValueError:
        return invalid()
    if product_id is None or not PRODUCT_ID.fullmatch(product_id):
        return invalid()
    if reader is None:
        log.error("shop read failed: no key")
        return unavailable()
    try:
        record = await reader.call(caller, GET_PRODUCT, {"product_id": product_id})
        prices = await reader.call(caller, GET_PRICES, {"product_id": product_id})
        view = _product_view(record, prices, caller, product_id)
    except NotFound:
        return not_found()
    except Exception as error:  # noqa: BLE001
        log.error("shop read failed: %s", type(error).__name__)
        return unavailable()
    return JSONResponse(content={"product": view}, headers=NO_STORE)


def _product_view(record: Any, prices: Any, caller: Caller, product_id: str) -> dict[str, Any]:
    if not isinstance(record, dict) or record.get("product_id") != product_id:
        raise Unreadable
    if not isinstance(prices, dict) or prices.get("product_id") != product_id:
        raise Unreadable
    if prices.get("region") != caller.region or not isinstance(prices.get("variants"), list):
        raise Unreadable
    variants = []
    for variant in prices["variants"]:
        if not isinstance(variant, dict) or not isinstance(variant.get("variant_id"), str):
            raise Unreadable
        if not VARIANT_ID.fullmatch(variant["variant_id"]):
            raise Unreadable  # review F2
        variants.append(
            {
                "variant_id": variant["variant_id"],
                "colour": str(variant.get("colour", "")),
                "size": str(variant.get("size", "")),
                "fit": variant.get("fit") if isinstance(variant.get("fit"), str) else None,
                "price": _money(variant.get("price")),
            }
        )
    attributes = record.get("attributes")
    return {
        "product_id": product_id,
        "name": str(record.get("name", "")),
        "brand": str(record.get("brand", "")),
        "department": str(record.get("department", "")),
        # SA-01 PR 2: the specifications grid's record fields, as the record has them.
        "subcategory": _text_or_none(record.get("subcategory")),
        "gender": _text_or_none(record.get("gender")),
        "fits": [f for f in record.get("fits", []) if isinstance(f, str)],
        "description": str(record.get("description", "")),
        "attributes": attributes if isinstance(attributes, dict) else {},
        "colours": [c for c in record.get("colours", []) if isinstance(c, str)],
        "sizes": [s for s in record.get("sizes", []) if isinstance(s, str)],
        "region": caller.region,
        "variants": variants,
        "photo": product_id in photo_ids(),
    }


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _review_view(review: Any) -> dict[str, Any]:
    if not isinstance(review, dict):
        raise Unreadable
    review_id = review.get("review_id")
    if not isinstance(review_id, str) or not REVIEW_ID.fullmatch(review_id):
        raise Unreadable
    date = review.get("date")
    if not isinstance(date, str) or not DATE.fullmatch(date):
        raise Unreadable
    texts = [review.get(key) for key in ("title", "body", "reviewer")]
    if not all(isinstance(text, str) for text in texts) or not isinstance(
        review.get("verified"), bool
    ):
        raise Unreadable
    return {
        "review_id": review_id,
        "title": review["title"],
        "body": review["body"],
        "reviewer": review["reviewer"],
        "date": date,
        "verified": review["verified"],
    }


@router.get("/api/reviews")
async def reviews(
    caller: Shopper,
    reader: Reader,
    id: Annotated[str | None, Query()] = None,
    limit: Annotated[str | None, Query()] = None,
) -> JSONResponse:
    try:
        product_id = _query_value(id)
        size = _int_value(limit, 1, MAX_REVIEWS)
    except ValueError:
        return invalid()
    if product_id is None or not PRODUCT_ID.fullmatch(product_id):
        return invalid()
    if reader is None:
        log.error("shop read failed: no key")
        return unavailable()
    args: dict[str, Any] = {"product_id": product_id}
    if size is not None:
        args["limit"] = size
    try:
        body = await reader.call(caller, LIST_REVIEWS, args)
        if not isinstance(body, dict) or body.get("product_id") != product_id:
            raise Unreadable
        total, listed = body.get("total"), body.get("reviews")
        if isinstance(total, bool) or not isinstance(total, int) or not isinstance(listed, list):
            raise Unreadable
        view = {
            "product_id": product_id,
            "total": total,
            "reviews": [_review_view(r) for r in listed],
        }
    except NotFound:
        return not_found()
    except Exception as error:  # noqa: BLE001
        log.error("shop read failed: %s", type(error).__name__)
        return unavailable()
    return JSONResponse(content=view, headers=NO_STORE)
