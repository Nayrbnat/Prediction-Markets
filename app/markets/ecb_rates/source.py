"""€STR futures -> futures-implied ECB Governing Council meeting probabilities.

I/O only. Fetches €STR future prices from Yahoo (``ESR{monthcode}{YY}.CME``) + the current
€STR from the ECB SDMX data API, then delegates meeting orchestration to the shared
``app/markets/_shared/rate_futures`` (pure rate-step math). Emits one ``MarketRef`` per
upcoming ECB meeting under the ``estr`` venue.

⚠️ DEFERRED — no free data supports a correct ECB signal (verified 2026-06-24, §13):
``ESR*.CME`` is CME's **THREE-MONTH** €STR future (compounded €STR over a quarterly IMM
period, 3rd Wed -> 3rd Wed +3M), but the shared rate-step math needs a **ONE-MONTH**
average-rate contract (ZQ/SR1 convention). Two fixes were investigated and BOTH need PAID
data:
  (a) a 1-month-average €STR future (ICE One-Month €STR) — exists but no free feed
      (Yahoo/Stooq don't carry it);
  (b) 3-month-compounded strip math — needs the OVERLAPPING serial (monthly) €STR contracts
      to isolate a single meeting, but the free Yahoo feed reliably carries only the
      QUARTERLY contracts (serials 404), and a quarterly window spans ~2 ECB meetings
      (1 equation, 2 unknowns) → a single meeting's probability is under-determined.
So ECB stays disabled (ECB_ENABLED=false) until a paid €STR feed (ICE 1-month / serial strip
/ ICE ECB-dated €STR) is wired. The €STR rate read (ECB SDMX) and ECB markets on Polymarket
both work, so only the futures leg is blocked.

Degrades gracefully: a failed fetch for one meeting is skipped.
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

VENUE = "estr"
_limiter = AsyncRateLimiter(rate_per_sec=5.0)

# CME futures month codes (calendar month -> code).
_MONTH_CODE = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}


def esr_symbol(year: int, month: int) -> str:
    """Yahoo symbol for the ESR contract of a given month, e.g. (2026, 9) -> 'ESRU26.CME'."""
    return f"ESR{_MONTH_CODE[month]}{year % 100:02d}.CME"


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


async def _esr_price(client: httpx.AsyncClient, symbol: str) -> Decimal | None:
    """Fetch the latest ESR price for ``symbol`` from Yahoo's chart endpoint."""
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


async def _current_estr(client: httpx.AsyncClient) -> Decimal | None:
    """Fetch the latest €STR (%) from the ECB SDMX data API.

    Parsed defensively: dataSets[0].series -> first series -> observations -> first
    observation (a list) -> element [0] is the rate. The series dimension key is not
    hardcoded — we take the first series whatever its key is.
    """
    payload = await fetch_json(
        client,
        "/service/data/EST/B.EU000A2X2A25.WT?lastNObservations=1&format=jsondata",
        venue=VENUE,
        limiter=_limiter,
    )
    if not isinstance(payload, dict):
        return None
    try:
        series = payload["dataSets"][0]["series"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(series, dict) or not series:
        return None
    first_series = next(iter(series.values()))
    if not isinstance(first_series, dict):
        return None
    observations = first_series.get("observations")
    if not isinstance(observations, dict) or not observations:
        return None
    first_obs = next(iter(observations.values()))
    if not isinstance(first_obs, list) or not first_obs:
        return None
    return _to_decimal(first_obs[0])


def _label(meeting: date) -> str:
    return f"ECB decision in {calendar.month_name[meeting.month]} {meeting.year}"


def _key(meeting: date) -> str:
    return f"ECB-{meeting.isoformat()}"


async def discover(
    yahoo_client: httpx.AsyncClient,
    ecb_client: httpx.AsyncClient,
    topic: str,
    *,
    meetings: list[date],
    horizon: int = 2,
    limit: int = 50,
) -> list[MarketRef]:
    """Emit one MarketRef per upcoming ECB meeting with futures-implied probabilities.

    Returns ``[]`` (clean degradation) when no meetings are configured/upcoming or the
    €STR read fails — never fabricates a rate.
    """

    async def current_rate() -> Decimal | None:
        return await _current_estr(ecb_client)

    async def price_for(year: int, month: int) -> Decimal | None:
        return await _esr_price(yahoo_client, esr_symbol(year, month))

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
