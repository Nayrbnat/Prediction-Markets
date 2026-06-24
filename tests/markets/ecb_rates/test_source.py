"""respx-mocked tests for the €STR futures (ECB) discovery source."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import respx
from httpx import Response

from app.core.http import make_client
from app.markets.ecb_rates import source as estr_source

YAHOO = "https://yahoo.test"
ECB = "https://ecb.test"

# A future meeting (relative to the 2026 test clock) on the 15th of a 30-day month:
# n_before=15, n_after=15 -> single-contract path. Sep 2026 -> ESR code 'U'.
MEETING = date(2026, 9, 15)
# ECB SDMX shape: dataSets[0].series -> first series -> observations -> first obs -> [0].
_ESTR = {"dataSets": [{"series": {"0:0:0:0": {"observations": {"0": [4.33]}}}}]}


def _chart(price: float) -> dict:
    return {"chart": {"result": [{"meta": {"regularMarketPrice": price}}]}}


async def test_discover_single_contract_distribution() -> None:
    # price 95.63 -> implied_avg 4.37; with r_start 4.33 and a 15/15 split -> r_end 4.41
    # -> +8bps -> k=0.32 -> No change 0.68, +25bps 0.32.
    async with respx.mock:
        respx.get(f"{ECB}/service/data/EST/B.EU000A2X2A25.WT").mock(
            return_value=Response(200, json=_ESTR)
        )
        respx.get(f"{YAHOO}/v8/finance/chart/ESRU26.CME").mock(
            return_value=Response(200, json=_chart(95.63))
        )
        respx.get(f"{YAHOO}/v8/finance/chart/ESRV26.CME").mock(  # next month (Oct), unused
            return_value=Response(200, json=_chart(95.50))
        )
        async with make_client(base_url=YAHOO) as yc, make_client(base_url=ECB) as ec:
            refs = await estr_source.discover(
                yc, ec, "ecb rate decision", meetings=[MEETING], horizon=2
            )

    assert len(refs) == 1
    ref = refs[0]
    assert ref.venue == "estr"
    assert ref.market_key == "ECB-2026-09-15"
    assert ref.event_title == "ECB decision in September 2026"
    assert ref.enable_order_book is False
    probs = dict(zip(ref.outcomes, ref.quoted_prices or [], strict=True))
    assert probs["No change"] == Decimal("0.68")
    assert probs["25 bps hike"] == Decimal("0.32")


async def test_no_upcoming_meetings_returns_empty() -> None:
    async with respx.mock:
        # Past meeting only -> nothing upcoming -> no fetches needed, clean [].
        async with make_client(base_url=YAHOO) as yc, make_client(base_url=ECB) as ec:
            refs = await estr_source.discover(
                yc, ec, "ecb rate decision", meetings=[date(2020, 1, 1)], horizon=2
            )
    assert refs == []


async def test_estr_failure_degrades_to_empty() -> None:
    async with respx.mock:
        respx.get(f"{ECB}/service/data/EST/B.EU000A2X2A25.WT").mock(
            return_value=Response(500)
        )
        async with make_client(base_url=YAHOO) as yc, make_client(base_url=ECB) as ec:
            refs = await estr_source.discover(
                yc, ec, "ecb rate decision", meetings=[MEETING], horizon=2
            )
    assert refs == []
