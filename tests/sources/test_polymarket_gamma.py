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
            "openInterest": "838095",
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
                    "bestBid": "0.61",
                    "bestAsk": "0.63",
                    "lastTradePrice": "0.62",
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


async def test_binary_ref_uses_child_question_as_title() -> None:
    # Multi-strike price events template the event title ("Bitcoin above ___ on June 24?")
    # but carry the strike in each child's `question` — which must become the ref title so
    # downstream relative-value matching can read the strike.
    payload = {"events": [{"id": "E", "title": "Bitcoin above ___ on June 24?",
                           "negRisk": False, "markets": [
        {"conditionId": "0xq", "outcomes": '["Yes","No"]', "outcomePrices": '["0.4","0.6"]',
         "question": "Will the price of Bitcoin be above $60,000 on June 24?", "closed": False}
    ]}]}
    async with respx.mock:
        respx.get(f"{BASE}/public-search").mock(return_value=Response(200, json=payload))
        async with make_client(base_url=BASE) as client:
            refs = await gamma.discover(client, "btc price")
    assert len(refs) == 1
    assert refs[0].event_title == "Will the price of Bitcoin be above $60,000 on June 24?"


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


# ---------------------------------------------------------------------------
# Binary complement + event OI fixes
# ---------------------------------------------------------------------------

async def test_binary_ref_no_side_bid_ask_last_are_complements() -> None:
    """No-side bid/ask/last must be the complement of Yes-side ask/bid/last."""
    async with respx.mock:
        respx.get(f"{BASE}/public-search").mock(return_value=Response(200, json=_BINARY_EVENT))
        async with make_client(base_url=BASE) as client:
            refs = await gamma.discover(client, "fed")
    ref = refs[0]
    # Yes side
    assert ref.best_bids[0] == Decimal("0.61")
    assert ref.best_asks[0] == Decimal("0.63")
    assert ref.last_trades[0] == Decimal("0.62")
    # No side: complement identity (no_bid = 1−yes_ask, no_ask = 1−yes_bid, no_last = 1−yes_last)
    assert ref.best_bids[1] == Decimal("0.370000")   # 1 − 0.63
    assert ref.best_asks[1] == Decimal("0.390000")   # 1 − 0.61
    assert ref.last_trades[1] == Decimal("0.380000")  # 1 − 0.62


async def test_binary_ref_open_interest_threaded_from_event() -> None:
    """Event-level openInterest must be broadcast to all outcomes of a binary ref."""
    async with respx.mock:
        respx.get(f"{BASE}/public-search").mock(return_value=Response(200, json=_BINARY_EVENT))
        async with make_client(base_url=BASE) as client:
            refs = await gamma.discover(client, "fed")
    ref = refs[0]
    assert ref.open_interests == [Decimal("838095"), Decimal("838095")]


async def test_binary_ref_no_side_null_when_yes_bid_is_none() -> None:
    """If Yes bid is absent, No ask must remain None — no fabrication."""
    event = {
        "events": [{
            "id": "E2", "title": "t", "negRisk": False, "openInterest": "100",
            "markets": [{
                "conditionId": "0xdef",
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.5","0.5"]',
                "clobTokenIds": '["1","2"]',
                "enableOrderBook": True,
                "closed": False,
                # bestBid absent; bestAsk present
                "bestAsk": "0.52",
                "lastTradePrice": "0.51",
            }],
        }]
    }
    async with respx.mock:
        respx.get(f"{BASE}/public-search").mock(return_value=Response(200, json=event))
        async with make_client(base_url=BASE) as client:
            refs = await gamma.discover(client, "t")
    ref = refs[0]
    assert ref.best_bids[0] is None        # Yes bid absent
    assert ref.best_asks[1] is None        # No ask = 1−yes_bid — but yes_bid is None → None
    assert ref.best_bids[1] == Decimal("0.480000")   # No bid = 1−yes_ask = 1−0.52


async def test_multi_outcome_ref_not_complemented() -> None:
    """Multi-outcome (negRisk) candidates are independent; bid/ask must NOT be complemented."""
    async with respx.mock:
        respx.get(f"{BASE}/public-search").mock(return_value=Response(200, json=_MULTI_EVENT))
        async with make_client(base_url=BASE) as client:
            refs = await gamma.discover(client, "world cup")
    ref = refs[0]
    # All bid/ask slots are None (no bestBid/bestAsk in _MULTI_EVENT fixture)
    # and must NOT be replaced by complements of each other.
    assert all(b is None for b in ref.best_bids)
    assert all(a is None for a in ref.best_asks)
