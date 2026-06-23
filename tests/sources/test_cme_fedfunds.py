"""respx-mocked tests for the CME Fed Funds (ZQ) discovery source."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import respx
from httpx import Response

from app.core.http import make_client
from app.sources import cme_fedfunds

YAHOO = "https://yahoo.test"
NYFED = "https://nyfed.test"

# A future meeting (relative to the 2026 test clock) on the 15th of a 30-day month:
# n_before=15, n_after=15 -> single-contract path. Sep 2026 -> ZQ code 'U'.
MEETING = date(2026, 9, 15)
_EFFR = {"refRates": [{"effectiveDate": "2026-06-22", "type": "EFFR", "percentRate": 4.33}]}


def _chart(price: float) -> dict:
    return {"chart": {"result": [{"meta": {"regularMarketPrice": price}}]}}


async def test_discover_single_contract_distribution() -> None:
    # price 95.63 -> implied_avg 4.37; with r_start 4.33 and a 15/15 split -> r_end 4.41
    # -> +8bps -> k=0.32 -> No change 0.68, +25bps 0.32.
    async with respx.mock:
        respx.get(f"{NYFED}/api/rates/unsecured/effr/last/1.json").mock(
            return_value=Response(200, json=_EFFR)
        )
        respx.get(f"{YAHOO}/v8/finance/chart/ZQU26.CBT").mock(
            return_value=Response(200, json=_chart(95.63))
        )
        respx.get(f"{YAHOO}/v8/finance/chart/ZQV26.CBT").mock(  # next month (Oct), unused here
            return_value=Response(200, json=_chart(95.50))
        )
        async with make_client(base_url=YAHOO) as yc, make_client(base_url=NYFED) as nc:
            refs = await cme_fedfunds.discover(
                yc, nc, "fed rate decision", meetings=[MEETING], horizon=2
            )

    assert len(refs) == 1
    ref = refs[0]
    assert ref.venue == "cme"
    assert ref.market_key == "FOMC-2026-09-15"
    assert ref.event_title == "Fed decision in September 2026"
    assert ref.enable_order_book is False
    probs = dict(zip(ref.outcomes, ref.quoted_prices or [], strict=True))
    assert probs["No change"] == Decimal("0.68")
    assert probs["25 bps hike"] == Decimal("0.32")


async def test_no_upcoming_meetings_returns_empty() -> None:
    async with respx.mock:
        # Past meeting only -> nothing upcoming -> no fetches needed, clean [].
        async with make_client(base_url=YAHOO) as yc, make_client(base_url=NYFED) as nc:
            refs = await cme_fedfunds.discover(
                yc, nc, "fed rate decision", meetings=[date(2020, 1, 1)], horizon=2
            )
    assert refs == []


async def test_effr_failure_degrades_to_empty() -> None:
    async with respx.mock:
        respx.get(f"{NYFED}/api/rates/unsecured/effr/last/1.json").mock(
            return_value=Response(500)
        )
        async with make_client(base_url=YAHOO) as yc, make_client(base_url=NYFED) as nc:
            refs = await cme_fedfunds.discover(
                yc, nc, "fed rate decision", meetings=[MEETING], horizon=2
            )
    assert refs == []


async def test_second_meeting_chains_from_first_meeting_rate() -> None:
    # Two meetings. EFFR=4.00.
    # Sep (95.875 -> implied 4.125, 15/15 split) -> r_end 4.25 => +25bps hike (100%).
    # Nov priced 95.75 -> implied avg 4.25. With chaining (r_start=4.25) -> r_end 4.25 => HOLD.
    # Without chaining (r_start=4.00) Nov would imply +50bps. The HOLD proves chaining.
    effr = {"refRates": [{"percentRate": 4.00}]}
    async with respx.mock:
        respx.get(f"{NYFED}/api/rates/unsecured/effr/last/1.json").mock(
            return_value=Response(200, json=effr)
        )
        respx.get(f"{YAHOO}/v8/finance/chart/ZQU26.CBT").mock(  # Sep
            return_value=Response(200, json=_chart(95.875))
        )
        respx.get(f"{YAHOO}/v8/finance/chart/ZQV26.CBT").mock(  # Oct (next of Sep, unused)
            return_value=Response(200, json=_chart(95.80))
        )
        respx.get(f"{YAHOO}/v8/finance/chart/ZQX26.CBT").mock(  # Nov
            return_value=Response(200, json=_chart(95.75))
        )
        respx.get(f"{YAHOO}/v8/finance/chart/ZQZ26.CBT").mock(  # Dec (next of Nov, unused)
            return_value=Response(200, json=_chart(95.70))
        )
        async with make_client(base_url=YAHOO) as yc, make_client(base_url=NYFED) as nc:
            refs = await cme_fedfunds.discover(
                yc, nc, "fed rate decision",
                meetings=[date(2026, 9, 15), date(2026, 11, 15)], horizon=2,
            )

    assert len(refs) == 2
    sep = dict(zip(refs[0].outcomes, refs[0].quoted_prices or [], strict=True))
    nov = dict(zip(refs[1].outcomes, refs[1].quoted_prices or [], strict=True))
    assert sep["25 bps hike"] == Decimal("1")
    assert nov["No change"] == Decimal("1")  # chained: no further move implied


async def test_meeting_priced_failure_is_skipped() -> None:
    async with respx.mock:
        respx.get(f"{NYFED}/api/rates/unsecured/effr/last/1.json").mock(
            return_value=Response(200, json=_EFFR)
        )
        respx.get(f"{YAHOO}/v8/finance/chart/ZQU26.CBT").mock(return_value=Response(404))
        async with make_client(base_url=YAHOO) as yc, make_client(base_url=NYFED) as nc:
            refs = await cme_fedfunds.discover(
                yc, nc, "fed rate decision", meetings=[MEETING], horizon=2
            )
    assert refs == []  # the one meeting failed its price fetch and was skipped
