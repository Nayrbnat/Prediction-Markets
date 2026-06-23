"""respx-mocked tests for the Kalshi discovery client (event-based, two-tier)."""

from __future__ import annotations

from decimal import Decimal

import pytest
import respx
from httpx import Response

from app.core.errors import SchemaDriftError
from app.core.http import make_client
from app.sources import kalshi

BASE = "https://kalshi.test"

_FED_EVENTS = {
    "events": [
        {
            "event_ticker": "KXFEDDECISION-28JAN",
            "title": "Fed decision in Jan 2028?",
            "mutually_exclusive": True,
            "markets": [
                {"ticker": "H0", "yes_sub_title": "Fed maintains rate",
                 "yes_bid_dollars": "0.57", "yes_ask_dollars": "0.59",
                 "volume_24h_fp": "1000", "status": "active"},
                {"ticker": "H25", "yes_sub_title": "Hike 25bps",
                 "yes_bid_dollars": "0.02", "yes_ask_dollars": "0.04",
                 "volume_24h_fp": "500", "status": "active"},
                {"ticker": "C25", "yes_sub_title": "Cut 25bps",
                 "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.34",
                 "volume_24h_fp": "300", "status": "active"},
                {"ticker": "OLD", "yes_sub_title": "settled", "last_price_dollars": "0",
                 "status": "settled"},
            ],
        }
    ]
}


async def test_explicit_series_event_grouped_with_subtitles() -> None:
    async with respx.mock:
        respx.get(f"{BASE}/events").mock(return_value=Response(200, json=_FED_EVENTS))
        async with make_client(base_url=BASE) as client:
            refs = await kalshi.discover(client, "fed", series_tickers=["KXFEDDECISION"])
    assert len(refs) == 1
    ref = refs[0]
    assert ref.market_key == "KXFEDDECISION-28JAN"
    assert ref.outcomes == ["Fed maintains rate", "Hike 25bps", "Cut 25bps"]  # settled dropped
    # mids: (0.57+0.59)/2=0.58, (0.02+0.04)/2=0.03, (0.30+0.34)/2=0.32
    assert ref.quoted_prices == [Decimal("0.58"), Decimal("0.03"), Decimal("0.32")]
    assert ref.volume == Decimal("1800")  # summed across active markets
    assert ref.token_ids == []  # Kalshi has no CLOB tokens


async def test_category_autodiscovery_matches_series_title() -> None:
    series_payload = {"series": [
        {"ticker": "KXFEDDECISION", "title": "Fed interest rate decision", "category": "Economics"},
        {"ticker": "KXGAS", "title": "Gas prices", "category": "Economics"},
    ]}
    async with respx.mock:
        respx.get(f"{BASE}/series").mock(return_value=Response(200, json=series_payload))
        respx.get(f"{BASE}/events").mock(return_value=Response(200, json=_FED_EVENTS))
        async with make_client(base_url=BASE) as client:
            refs = await kalshi.discover(client, "fed decision", category="Economics")
    # "fed"/"decision" match the KXFEDDECISION title -> its events resolve
    assert len(refs) == 1
    assert refs[0].outcomes[0] == "Fed maintains rate"


async def test_no_series_no_category_warns_and_empty(caplog: pytest.LogCaptureFixture) -> None:
    async with make_client(base_url=BASE) as client:
        with caplog.at_level("WARNING"):
            refs = await kalshi.discover(client, "fed")  # nothing to resolve from
    assert refs == []
    assert any("kalshi.no_series_no_match" in r.message for r in caplog.records)


async def test_non_exclusive_event_yields_binary_refs() -> None:
    payload = {"events": [{
        "event_ticker": "KXIND",
        "title": "Independent questions",
        "mutually_exclusive": False,
        "markets": [
            {"ticker": "A", "yes_sub_title": "Rain tomorrow",
             "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.44", "status": "active"},
            {"ticker": "B", "yes_sub_title": "Snow tomorrow",
             "yes_bid_dollars": "0.10", "yes_ask_dollars": "0.12", "status": "active"},
        ],
    }]}
    async with respx.mock:
        respx.get(f"{BASE}/events").mock(return_value=Response(200, json=payload))
        async with make_client(base_url=BASE) as client:
            refs = await kalshi.discover(client, "weather", series_tickers=["KXWEATHER"])
    assert len(refs) == 2  # one binary ref per market
    assert refs[0].market_key == "A"
    assert refs[0].outcomes == ["Rain tomorrow", "No"]
    assert refs[0].quoted_prices == [Decimal("0.42"), Decimal("0.58")]


async def test_schema_drift_raises() -> None:
    async with respx.mock:
        respx.get(f"{BASE}/events").mock(return_value=Response(200, json={"nope": []}))
        async with make_client(base_url=BASE) as client:
            with pytest.raises(SchemaDriftError):
                await kalshi.discover(client, "fed", series_tickers=["KXFEDDECISION"])
