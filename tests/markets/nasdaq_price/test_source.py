"""respx-mocked tests for the CBOE NDX threshold discovery source."""

from __future__ import annotations

from decimal import Decimal

import respx
from httpx import Response

from app.core.http import make_client
from app.markets.nasdaq_price import source

CBOE = "https://cboe.test"


def _call(strike: int, delta: float, token: str = "260717") -> dict:
    # OSI: NDX + YYMMDD + C + 8-digit strike (thousandths). 27000 -> 27000000.
    return {"option": f"NDX{token}C{strike * 1000:08d}", "delta": delta}


_PUT = {"option": "NDX260717P27000000", "delta": -0.5}  # ignored (put)
_OTHER_EXP = {"option": "NDX260814C27000000", "delta": 0.9}  # ignored (other expiry)
_OPTIONS = {
    "data": {
        "options": [
            _call(26000, 0.70),  # call delta decreases with strike
            _call(27000, 0.50),
            _call(28000, 0.20),
            _PUT,
            _OTHER_EXP,
        ]
    }
}


async def test_discover_emits_threshold_probability() -> None:
    async with respx.mock:
        respx.get(f"{CBOE}/api/global/delayed_quotes/options/_NDX.json").mock(
            return_value=Response(200, json=_OPTIONS)
        )
        async with make_client(base_url=CBOE) as client:
            refs = await source.discover(
                client, "nasdaq price", targets=[(Decimal(27000), "260717")]
            )

    assert len(refs) == 1
    ref = refs[0]
    assert ref.venue == "cboe"
    assert ref.market_key == "NDX-260717-27000"
    assert ref.outcomes == ["NDX ≥ $27,000", "NDX < $27,000"]
    # call delta at strike 27000 is 0.50 -> P(above) = 0.50.
    assert ref.quoted_prices == [Decimal("0.5"), Decimal("0.5")]
    assert ref.close_date is not None
    assert (ref.close_date.year, ref.close_date.month, ref.close_date.day) == (2026, 7, 17)


async def test_unbracketable_and_empty_degrade() -> None:
    async with respx.mock:
        respx.get(f"{CBOE}/api/global/delayed_quotes/options/_NDX.json").mock(
            return_value=Response(200, json=_OPTIONS)
        )
        async with make_client(base_url=CBOE) as client:
            # 40000 is above the listed strike range -> cannot bracket -> skipped.
            refs = await source.discover(
                client, "nasdaq price", targets=[(Decimal(40000), "260717")]
            )
    assert refs == []


async def test_fetch_failure_degrades_to_empty() -> None:
    async with respx.mock:
        respx.get(f"{CBOE}/api/global/delayed_quotes/options/_NDX.json").mock(
            return_value=Response(500)
        )
        async with make_client(base_url=CBOE) as client:
            refs = await source.discover(
                client, "nasdaq price", targets=[(Decimal(27000), "260717")]
            )
    assert refs == []
