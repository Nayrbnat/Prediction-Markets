"""respx-mocked tests for the Kalshi discovery client."""

from __future__ import annotations

from decimal import Decimal

import pytest
import respx
from httpx import Response

from app.core.errors import SchemaDriftError
from app.core.http import make_client
from app.sources import kalshi

BASE = "https://kalshi.test"


async def test_discover_converts_cents_to_probability() -> None:
    payload = {
        "markets": [
            {
                "ticker": "KX-1",
                "event_ticker": "KXFED",
                "title": "Fed rate decision in March",
                "yes_bid": 60,
                "yes_ask": 64,
                "no_bid": 36,
                "no_ask": 40,
                "volume": 5000,
                "status": "active",
            }
        ]
    }
    async with respx.mock:
        respx.get(f"{BASE}/markets").mock(return_value=Response(200, json=payload))
        async with make_client(base_url=BASE) as client:
            refs = await kalshi.discover(client, "fed rate")
    assert len(refs) == 1
    ref = refs[0]
    assert ref.market_key == "KX-1"
    assert ref.quoted_prices == [Decimal("0.62"), Decimal("0.38")]  # (60+64)/2/100, (36+40)/2/100
    assert ref.volume == Decimal("5000")
    assert ref.venue == "kalshi"


async def test_title_filter_drops_non_matches() -> None:
    payload = {
        "markets": [
            {"ticker": "A", "title": "Weather in NYC", "yes_bid": 50, "yes_ask": 52,
             "status": "active"},
        ]
    }
    async with respx.mock:
        respx.get(f"{BASE}/markets").mock(return_value=Response(200, json=payload))
        async with make_client(base_url=BASE) as client:
            refs = await kalshi.discover(client, "fed rate")
    assert refs == []


async def test_schema_drift_raises() -> None:
    async with respx.mock:
        respx.get(f"{BASE}/markets").mock(return_value=Response(200, json={"nope": []}))
        async with make_client(base_url=BASE) as client:
            with pytest.raises(SchemaDriftError):
                await kalshi.discover(client, "fed")
