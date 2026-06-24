"""CME 30-Day Fed Funds futures (ZQ) -> futures-implied FOMC meeting probabilities.

I/O only. This source fetches:
  - ZQ contract settlement/last prices from Yahoo Finance (free, no key), per
    contract month (symbol ``ZQ{monthcode}{YY}.CBT``, e.g. July 2026 = ``ZQN26.CBT``);
  - the current effective fed funds rate (EFFR) from the NY Fed rates API.

It binds the Fed-specific bits (ZQ symbol scheme, EFFR source, labels) and delegates the
meeting orchestration + chaining to the shared ``app/markets/_shared/rate_futures``, which
uses the pure rate-step math. One ``MarketRef`` per upcoming FOMC meeting is emitted, shaped
like a prediction-market venue (``quoted_prices`` = computed bucket probabilities), flowing
through the existing pricing -> snapshot -> digest pipeline as the ``cme`` venue.

There is no order book and no per-trader data — CME contributes a futures-implied
distribution only. Degrades gracefully: a failed fetch for one meeting is skipped.
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from app.core.http import fetch_json
from app.core.rate_limit import AsyncRateLimiter
from app.markets._shared import rate_futures
from app.models.domain import MarketRef

VENUE = "cme"
_limiter = AsyncRateLimiter(rate_per_sec=5.0)

# CME futures month codes (calendar month -> code).
_MONTH_CODE = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}


def zq_symbol(year: int, month: int) -> str:
    """Yahoo symbol for the ZQ contract of a given month, e.g. (2026, 7) -> 'ZQN26.CBT'."""
    return f"ZQ{_MONTH_CODE[month]}{year % 100:02d}.CBT"


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


async def _zq_price(client: httpx.AsyncClient, symbol: str) -> Decimal | None:
    """Fetch the latest ZQ price for ``symbol`` from Yahoo's chart endpoint."""
    payload = await fetch_json(
        client,
        f"/v8/finance/chart/{symbol}",
        venue=VENUE,
        limiter=_limiter,
        params={"interval": "1d", "range": "5d"},
    )
    if not isinstance(payload, dict):
        return None
    try:
        meta = payload["chart"]["result"][0]["meta"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(meta, dict):
        return None
    price = _to_decimal(meta.get("regularMarketPrice"))
    return price if price is not None else _to_decimal(meta.get("chartPreviousClose"))


async def _current_effr(client: httpx.AsyncClient) -> Decimal | None:
    """Fetch the latest EFFR (%) from the NY Fed rates API."""
    payload = await fetch_json(
        client,
        "/api/rates/unsecured/effr/last/1.json",
        venue=VENUE,
        limiter=_limiter,
    )
    if not isinstance(payload, dict):
        return None
    rates = payload.get("refRates")
    if not isinstance(rates, list) or not rates or not isinstance(rates[0], dict):
        return None
    return _to_decimal(rates[0].get("percentRate"))


def _label(meeting: date) -> str:
    return f"Fed decision in {calendar.month_name[meeting.month]} {meeting.year}"


def _key(meeting: date) -> str:
    return f"FOMC-{meeting.isoformat()}"


async def discover(
    yahoo_client: httpx.AsyncClient,
    nyfed_client: httpx.AsyncClient,
    topic: str,
    *,
    meetings: list[date],
    horizon: int = 2,
    limit: int = 50,
) -> list[MarketRef]:
    """Emit one MarketRef per upcoming FOMC meeting with futures-implied probabilities.

    Returns ``[]`` (clean degradation) when no meetings are configured/upcoming or the
    EFFR read fails — never fabricates a rate.
    """

    async def current_rate() -> Decimal | None:
        return await _current_effr(nyfed_client)

    async def price_for(year: int, month: int) -> Decimal | None:
        return await _zq_price(yahoo_client, zq_symbol(year, month))

    return await rate_futures.discover_meetings(
        venue=VENUE,
        topic=topic,
        meetings=meetings,
        current_rate=current_rate,
        price_for=price_for,
        label_fn=_label,
        key_fn=_key,
        horizon=horizon,
        limit=limit,
    )
