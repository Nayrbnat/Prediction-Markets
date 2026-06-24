"""respx-mocked tests for the Deribit ETH threshold discovery source."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import respx
from httpx import Response

from app.core.http import make_client
from app.markets.eth_price import source

DERIBIT = "https://deribit.test"

_EXPIRY_MS = int(datetime(2026, 12, 26, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)

# Three call strikes for 26DEC26 (underlying 4000) -> curve [(4000,800),(5000,300),(6000,80)].
_INSTRUMENTS = {
    "result": [
        {"instrument_name": "ETH-26DEC26-4000-C", "strike": 4000,
         "option_type": "call", "expiration_timestamp": _EXPIRY_MS},
        {"instrument_name": "ETH-26DEC26-5000-C", "strike": 5000,
         "option_type": "call", "expiration_timestamp": _EXPIRY_MS},
        {"instrument_name": "ETH-26DEC26-6000-C", "strike": 6000,
         "option_type": "call", "expiration_timestamp": _EXPIRY_MS},
        {"instrument_name": "ETH-26DEC26-5000-P", "strike": 5000,
         "option_type": "put", "expiration_timestamp": _EXPIRY_MS},  # ignored
        {"instrument_name": "ETH-31JUL26-5000-C", "strike": 5000,
         "option_type": "call", "expiration_timestamp": _EXPIRY_MS},  # other expiry, ignored
    ]
}
def _sum(name: str, mark: float) -> dict:
    return {"instrument_name": name, "mark_price": mark, "underlying_price": 4000}


_SUMMARY = {
    "result": [
        _sum("ETH-26DEC26-4000-C", 0.20),
        _sum("ETH-26DEC26-5000-C", 0.075),
        _sum("ETH-26DEC26-6000-C", 0.02),
        _sum("ETH-26DEC26-5000-P", 0.05),
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
                client, "eth price", targets=[(Decimal(5_000), "26DEC26")]
            )

    assert len(refs) == 1
    ref = refs[0]
    assert ref.venue == "deribit"
    assert ref.market_key == "ETH-26DEC26-5000"
    assert ref.event_title == "ETH ≥ $5,000 by 26DEC26"
    assert ref.enable_order_book is False
    assert ref.outcomes == ["ETH ≥ $5,000", "ETH < $5,000"]
    # slope (300-800)/1000 = -0.5 -> P(above) 0.5.
    assert ref.quoted_prices == [Decimal("0.5"), Decimal("0.5")]
    assert ref.close_date is not None
    assert (ref.close_date.year, ref.close_date.month) == (2026, 12)


async def test_unbracketable_target_is_skipped() -> None:
    # 9000 is above the listed strike range -> cannot bracket -> skipped (clean).
    async with respx.mock:
        _mock_ok()
        async with make_client(base_url=DERIBIT) as client:
            refs = await source.discover(
                client, "eth price", targets=[(Decimal(9_000), "26DEC26")]
            )
    assert refs == []


async def test_no_targets_returns_empty() -> None:
    async with respx.mock:
        async with make_client(base_url=DERIBIT) as client:
            refs = await source.discover(client, "eth price", targets=[])
    assert refs == []


async def test_fetch_failure_degrades_to_empty() -> None:
    async with respx.mock:
        respx.get(f"{DERIBIT}/api/v2/public/get_instruments").mock(return_value=Response(500))
        respx.get(f"{DERIBIT}/api/v2/public/get_book_summary_by_currency").mock(
            return_value=Response(200, json=_SUMMARY)
        )
        async with make_client(base_url=DERIBIT) as client:
            refs = await source.discover(
                client, "eth price", targets=[(Decimal(5_000), "26DEC26")]
            )
    assert refs == []
