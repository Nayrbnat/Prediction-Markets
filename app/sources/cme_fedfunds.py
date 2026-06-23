"""CME 30-Day Fed Funds futures (ZQ) -> futures-implied FOMC meeting probabilities.

I/O only. This source fetches:
  - ZQ contract settlement/last prices from Yahoo Finance (free, no key), per
    contract month (symbol ``ZQ{monthcode}{YY}.CBT``, e.g. July 2026 = ``ZQN26.CBT``);
  - the current effective fed funds rate (EFFR) from the NY Fed rates API.

It hands those raw numbers to the pure ``app/analysis/fed_funds.py`` math and emits one
``MarketRef`` per upcoming FOMC meeting, shaped exactly like a prediction-market venue
(``quoted_prices`` = the computed bucket probabilities), so it flows through the existing
pricing -> snapshot -> digest pipeline as the ``cme`` venue.

There is no order book and no per-trader data — CME contributes a futures-implied
distribution only. Degrades gracefully: a failed fetch for one meeting is skipped.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from app.analysis.fed_funds import fed_funds_distribution
from app.core.errors import RateLimitError, SourceError
from app.core.http import fetch_json
from app.core.logging import get_logger
from app.core.rate_limit import AsyncRateLimiter
from app.models.domain import MarketRef

logger = get_logger(__name__)

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


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


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


def _upcoming_meetings(meetings: list[date], *, today: date, horizon: int) -> list[date]:
    return sorted(m for m in meetings if m >= today)[:horizon]


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
    today = datetime.now(timezone.utc).date()
    upcoming = _upcoming_meetings(meetings, today=today, horizon=min(horizon, limit))
    if not upcoming:
        logger.warning("cme.no_upcoming_meetings", extra={"topic": topic})
        return []

    try:
        r_start = await _current_effr(nyfed_client)
    except (SourceError, RateLimitError) as exc:
        logger.warning("cme.effr_failed", extra={"topic": topic, "error": str(exc)})
        return []
    if r_start is None:
        logger.warning("cme.effr_unavailable", extra={"topic": topic})
        return []

    meeting_months = {(m.year, m.month) for m in meetings}
    refs: list[MarketRef] = []
    # Carry the expected rate forward across meetings (chronological): meeting k's
    # starting rate is the implied post-meeting rate of meeting k-1, not the current
    # EFFR — otherwise the 2nd meeting double-counts the cumulative move.
    current_rate = r_start
    for meeting in upcoming:  # _upcoming_meetings returns them sorted ascending
        try:
            price_m = await _zq_price(yahoo_client, zq_symbol(meeting.year, meeting.month))
            ny, nm = _next_month(meeting.year, meeting.month)
            price_next = await _zq_price(yahoo_client, zq_symbol(ny, nm))
        except (SourceError, RateLimitError) as exc:
            logger.warning(
                "cme.meeting_failed", extra={"meeting": meeting.isoformat(), "error": str(exc)}
            )
            continue
        if price_m is None:
            logger.warning("cme.price_unavailable", extra={"meeting": meeting.isoformat()})
            continue
        next_has_meeting = (ny, nm) in meeting_months

        result = fed_funds_distribution(
            meeting_date=meeting,
            price_meeting_month=price_m,
            r_start=current_rate,
            price_next_month=price_next,
            next_month_has_meeting=next_has_meeting,
        )
        current_rate = result.r_end  # chain forward for the next meeting

        label = f"Fed decision in {calendar.month_name[meeting.month]} {meeting.year}"
        refs.append(
            MarketRef(
                venue=VENUE,
                event_id=f"FOMC-{meeting.isoformat()}",
                market_key=f"FOMC-{meeting.isoformat()}",
                event_title=label,
                outcomes=result.outcomes,
                resolved=False,
                enable_order_book=False,
                topic=topic,
                quoted_prices=result.probabilities,
                close_date=datetime(meeting.year, meeting.month, meeting.day, tzinfo=timezone.utc),
            )
        )
        logger.info(
            "cme.meeting_priced",
            extra={
                "meeting": meeting.isoformat(),
                "method": result.method,
                "delta_bps": str(result.delta_bps),
            },
        )

    logger.info("cme.discover", extra={"topic": topic, "meetings": len(refs)})
    return refs
