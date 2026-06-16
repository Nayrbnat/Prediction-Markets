"""respx-mocked tests for the CLOB order-book client."""

from __future__ import annotations

from decimal import Decimal

import respx
from httpx import Response

from app.core.http import make_client
from app.sources import polymarket_clob as clob

BASE = "https://clob.test"


async def test_order_book_computes_mid_spread_depth() -> None:
    payload = {
        "bids": [{"price": "0.60", "size": "500"}, {"price": "0.59", "size": "100"}],
        "asks": [{"price": "0.64", "size": "300"}, {"price": "0.65", "size": "200"}],
        "tick_size": "0.01",
    }
    async with respx.mock:
        respx.get(f"{BASE}/book").mock(return_value=Response(200, json=payload))
        async with make_client(base_url=BASE) as client:
            book = await clob.order_book(client, "111")
    assert book.best_bid == Decimal("0.60")
    assert book.best_ask == Decimal("0.64")
    assert book.mid == Decimal("0.62")
    assert book.spread == Decimal("0.04")
    assert book.depth == Decimal("800")  # best bid size + best ask size


async def test_empty_book_yields_no_mid() -> None:
    async with respx.mock:
        respx.get(f"{BASE}/book").mock(return_value=Response(200, json={"bids": [], "asks": []}))
        async with make_client(base_url=BASE) as client:
            book = await clob.order_book(client, "111")
    assert book.mid is None
    assert book.spread is None
