"""respx-mocked tests for the Deribit BTC threshold discovery source."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import respx
from httpx import Response

from app.core.http import make_client
from app.markets.btc_price import source

DERIBIT = "https://deribit.test"

_EXPIRY_MS = int(datetime(2026, 12, 26, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)

# Three call strikes for 26DEC26 (underlying 100k) -> curve [(100k,8000),(110k,3000),(120k,800)].
_INSTRUMENTS = {
    "result": [
        {"instrument_name": "BTC-26DEC26-100000-C", "strike": 100000,
         "option_type": "call", "expiration_timestamp": _EXPIRY_MS},
        {"instrument_name": "BTC-26DEC26-110000-C", "strike": 110000,
         "option_type": "call", "expiration_timestamp": _EXPIRY_MS},
        {"instrument_name": "BTC-26DEC26-120000-C", "strike": 120000,
         "option_type": "call", "expiration_timestamp": _EXPIRY_MS},
        {"instrument_name": "BTC-26DEC26-110000-P", "strike": 110000,
         "option_type": "put", "expiration_timestamp": _EXPIRY_MS},  # ignored
        {"instrument_name": "BTC-31JUL26-110000-C", "strike": 110000,
         "option_type": "call", "expiration_timestamp": _EXPIRY_MS},  # other expiry, ignored
    ]
}
def _sum(name: str, mark: float) -> dict:
    return {"instrument_name": name, "mark_price": mark, "underlying_price": 100000}


_SUMMARY = {
    "result": [
        _sum("BTC-26DEC26-100000-C", 0.08),
        _sum("BTC-26DEC26-110000-C", 0.03),
        _sum("BTC-26DEC26-120000-C", 0.008),
        _sum("BTC-26DEC26-110000-P", 0.02),
    ]
}


def _mock_ok() -> None:
    respx.get(f"{DERIBIT}/api/v2/public/get_instruments").mock(
        return_value=Response(200, json=_INSTRUMENTS)
    )
    respx.get(f"{DERIBIT}/api/v2/public/get_book_summary_by_currency").mock(
        return_value=Response(200, json=_SUMMARY)
    )


async def test_discover_emits_threshold_probability() -> None:
    async with respx.mock:
        _mock_ok()
        async with make_client(base_url=DERIBIT) as client:
            refs = await source.discover(
                client, "btc price", targets=[(Decimal(110_000), "26DEC26")]
            )

    assert len(refs) == 1
    ref = refs[0]
    assert ref.venue == "deribit"
    assert ref.market_key == "BTC-26DEC26-110000"
    assert ref.event_title == "BTC ≥ $110,000 by 26DEC26"
    assert ref.enable_order_book is False
    assert ref.outcomes == ["BTC ≥ $110,000", "BTC < $110,000"]
    # slope (3000-8000)/10000 = -0.5 -> P(above) 0.5.
    assert ref.quoted_prices == [Decimal("0.5"), Decimal("0.5")]
    assert ref.close_date is not None
    assert (ref.close_date.year, ref.close_date.month) == (2026, 12)


async def test_unbracketable_target_is_skipped() -> None:
    # 200k is above the listed strike range -> cannot bracket -> skipped (clean).
    async with respx.mock:
        _mock_ok()
        async with make_client(base_url=DERIBIT) as client:
            refs = await source.discover(
                client, "btc price", targets=[(Decimal(200_000), "26DEC26")]
            )
    assert refs == []


async def test_no_targets_returns_empty() -> None:
    async with respx.mock:
        async with make_client(base_url=DERIBIT) as client:
            refs = await source.discover(client, "btc price", targets=[])
    assert refs == []


async def test_fetch_failure_degrades_to_empty() -> None:
    async with respx.mock:
        respx.get(f"{DERIBIT}/api/v2/public/get_instruments").mock(return_value=Response(500))
        respx.get(f"{DERIBIT}/api/v2/public/get_book_summary_by_currency").mock(
            return_value=Response(200, json=_SUMMARY)
        )
        async with make_client(base_url=DERIBIT) as client:
            refs = await source.discover(
                client, "btc price", targets=[(Decimal(110_000), "26DEC26")]
            )
    assert refs == []
