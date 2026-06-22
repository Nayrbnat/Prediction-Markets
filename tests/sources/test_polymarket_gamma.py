"""respx-mocked tests for the Gamma discovery client (event-level grouping)."""

from __future__ import annotations

from decimal import Decimal

import pytest
import respx
from httpx import Response

from app.core.errors import RateLimitError, SchemaDriftError
from app.core.http import make_client
from app.sources import polymarket_gamma as gamma

BASE = "https://gamma.test"

_BINARY_EVENT = {
    "events": [
        {
            "id": "E1",
            "title": "Fed decision in March",
            "negRisk": False,
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

_MULTI_EVENT = {
    "events": [
        {
            "id": "EVT9",
            "title": "World Cup Winner",
            "negRisk": True,
            "volume": "1000000",
            "markets": [
                {"groupItemTitle": "Spain", "outcomes": '["Yes","No"]',
                 "outcomePrices": '["0.40","0.60"]', "closed": False},
                {"groupItemTitle": "France", "outcomes": '["Yes","No"]',
                 "outcomePrices": '["0.35","0.65"]', "closed": False},
                {"groupItemTitle": "Brazil", "outcomes": '["Yes","No"]',
                 "outcomePrices": '["0.25","0.75"]', "closed": False},
                {"groupItemTitle": "Old", "outcomes": '["Yes","No"]',
                 "outcomePrices": '["0","1"]', "closed": True},
            ],
        }
    ]
}


async def test_binary_event_yields_yes_no_ref() -> None:
    async with respx.mock:
        respx.get(f"{BASE}/public-search").mock(return_value=Response(200, json=_BINARY_EVENT))
        async with make_client(base_url=BASE) as client:
            refs = await gamma.discover(client, "fed")
    assert len(refs) == 1
    ref = refs[0]
    assert ref.market_key == "0xabc"
    assert ref.outcomes == ["Yes", "No"]
    assert ref.token_ids == ["111", "222"]  # CLOB-priced
    assert ref.quoted_prices == [Decimal("0.62"), Decimal("0.38")]


async def test_multi_outcome_event_grouped_with_candidate_labels() -> None:
    async with respx.mock:
        respx.get(f"{BASE}/public-search").mock(return_value=Response(200, json=_MULTI_EVENT))
        async with make_client(base_url=BASE) as client:
            refs = await gamma.discover(client, "world cup")
    assert len(refs) == 1  # one ref for the whole event
    ref = refs[0]
    assert ref.market_key == "EVT9"
    assert ref.outcomes == ["Spain", "France", "Brazil"]  # closed "Old" dropped
    assert ref.quoted_prices == [Decimal("0.40"), Decimal("0.35"), Decimal("0.25")]
    assert ref.token_ids == []  # grouped -> use quoted prices, skip per-candidate CLOB


async def test_resolved_binary_dropped() -> None:
    payload = {"events": [{"id": "E", "title": "t", "negRisk": False, "markets": [
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
            side_effect=[Response(429), Response(200, json=_BINARY_EVENT)]
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
