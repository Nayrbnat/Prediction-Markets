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


async def test_discover_reads_dollar_fields() -> None:
    # Live shape (verified 2026-06-16): dollar-denominated string prices (0..1).
    payload = {
        "cursor": "abc",
        "markets": [
            {
                "ticker": "KX-1",
                "event_ticker": "KXFED",
                "title": "Fed rate decision in March",
                "yes_bid_dollars": "0.60",
                "yes_ask_dollars": "0.64",
                "no_bid_dollars": "0.36",
                "no_ask_dollars": "0.40",
                "last_price_dollars": "0.62",
                "volume_24h_fp": "5000",
                "volume_fp": "120000",
                "status": "active",
            }
        ],
    }
    async with respx.mock:
        respx.get(f"{BASE}/markets").mock(return_value=Response(200, json=payload))
        async with make_client(base_url=BASE) as client:
            refs = await kalshi.discover(client, "fed rate")
    assert len(refs) == 1
    ref = refs[0]
    assert ref.market_key == "KX-1"
    assert ref.quoted_prices == [Decimal("0.62"), Decimal("0.38")]  # (0.60+0.64)/2, (0.36+0.40)/2
    assert ref.volume == Decimal("5000")  # prefers 24h volume
    assert ref.venue == "kalshi"


async def test_falls_back_to_last_price_when_no_quotes() -> None:
    payload = {
        "markets": [
            {
                "ticker": "KX-2",
                "title": "fed rate fallback",
                "yes_bid_dollars": None,
                "yes_ask_dollars": None,
                "last_price_dollars": "0.55",
                "volume_24h_fp": "10",
                "status": "active",
            }
        ]
    }
    async with respx.mock:
        respx.get(f"{BASE}/markets").mock(return_value=Response(200, json=payload))
        async with make_client(base_url=BASE) as client:
            refs = await kalshi.discover(client, "fed rate")
    assert refs[0].quoted_prices == [Decimal("0.55"), Decimal("0.45")]


async def test_title_filter_drops_non_matches() -> None:
    payload = {
        "markets": [
            {"ticker": "A", "title": "Weather in NYC", "yes_bid_dollars": "0.50",
             "yes_ask_dollars": "0.52", "status": "active"},
        ]
    }
    async with respx.mock:
        respx.get(f"{BASE}/markets").mock(return_value=Response(200, json=payload))
        async with make_client(base_url=BASE) as client:
            refs = await kalshi.discover(client, "fed rate")
    assert refs == []


async def test_no_series_no_match_warns(caplog: pytest.LogCaptureFixture) -> None:
    payload = {
        "markets": [
            {"ticker": "Z", "title": "Weather in NYC", "yes_bid_dollars": "0.50",
             "yes_ask_dollars": "0.52", "status": "active"},
        ]
    }
    async with respx.mock:
        respx.get(f"{BASE}/markets").mock(return_value=Response(200, json=payload))
        async with make_client(base_url=BASE) as client:
            with caplog.at_level("WARNING"):
                refs = await kalshi.discover(client, "fed rate")  # no series_ticker
    assert refs == []
    assert any("kalshi.no_series_no_match" in r.message for r in caplog.records)


async def test_schema_drift_raises() -> None:
    async with respx.mock:
        respx.get(f"{BASE}/markets").mock(return_value=Response(200, json={"nope": []}))
        async with make_client(base_url=BASE) as client:
            with pytest.raises(SchemaDriftError):
                await kalshi.discover(client, "fed")
