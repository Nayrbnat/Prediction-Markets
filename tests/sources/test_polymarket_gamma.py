"""respx-mocked tests for the Gamma discovery client."""

from __future__ import annotations

from decimal import Decimal

import pytest
import respx
from httpx import Response

from app.core.errors import RateLimitError, SchemaDriftError
from app.core.http import make_client
from app.sources import polymarket_gamma as gamma

BASE = "https://gamma.test"

_EVENT_PAYLOAD = {
    "events": [
        {
            "id": "E1",
            "title": "Fed decision in March",
            "markets": [
                {
                    "conditionId": "0xabc",
                    "outcomes": '["Yes","No"]',
                    "outcomePrices": '["0.62","0.38"]',
                    "clobTokenIds": '["111","222"]',
                    "enableOrderBook": True,
                    "closed": False,
                    "volume": "5000",
                    "liquidity": "2000",
                }
            ],
        }
    ]
}


async def test_discover_parses_markets() -> None:
    async with respx.mock:
        respx.get(f"{BASE}/public-search").mock(return_value=Response(200, json=_EVENT_PAYLOAD))
        async with make_client(base_url=BASE) as client:
            refs = await gamma.discover(client, "fed")
    assert len(refs) == 1
    ref = refs[0]
    assert ref.market_key == "0xabc"
    assert ref.token_ids == ["111", "222"]
    assert ref.quoted_prices == [Decimal("0.62"), Decimal("0.38")]
    assert ref.venue == "polymarket"


async def test_discover_drops_resolved() -> None:
    payload = {"events": [{"id": "E", "title": "t", "markets": [
        {"conditionId": "x", "outcomes": '["Yes","No"]', "outcomePrices": '["0.5","0.5"]',
         "closed": True}
    ]}]}
    async with respx.mock:
        respx.get(f"{BASE}/public-search").mock(return_value=Response(200, json=payload))
        async with make_client(base_url=BASE) as client:
            refs = await gamma.discover(client, "x")
    assert refs == []


async def test_schema_drift_raises() -> None:
    async with respx.mock:
        respx.get(f"{BASE}/public-search").mock(return_value=Response(200, json={"wrong": []}))
        async with make_client(base_url=BASE) as client:
            with pytest.raises(SchemaDriftError):
                await gamma.discover(client, "fed")


async def test_429_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    async with respx.mock:
        route = respx.get(f"{BASE}/public-search").mock(
            side_effect=[Response(429), Response(200, json=_EVENT_PAYLOAD)]
        )
        async with make_client(base_url=BASE) as client:
            refs = await gamma.discover(client, "fed")
    assert route.call_count == 2
    assert len(refs) == 1


async def test_429_exhausts_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    async with respx.mock:
        respx.get(f"{BASE}/public-search").mock(return_value=Response(429))
        async with make_client(base_url=BASE) as client:
            with pytest.raises(RateLimitError):
                await gamma.discover(client, "fed")
