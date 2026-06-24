"""Shared orchestration for overnight-rate futures markets (Fed ZQ, ECB €STR, ...).

Any central bank whose policy rate has a 1-month average-overnight-rate futures contract
(EFFR→ZQ, SOFR→SR1, €STR→€STR futures) prices its meetings the same way: per upcoming
meeting, read the meeting-month future (and the next month's, for the two-contract path),
solve the implied post-meeting rate via the pure ``rate_step`` math, and chain the start
rate forward across meetings. This module is that orchestration, parameterised by a
``price_for(year, month)`` fetcher + labels; concrete markets bind the symbol scheme and
current-rate source in their own ``source.py``.

I/O-adjacent (it awaits the injected fetcher) but contains no transport details itself.
Degrades gracefully: a failed/absent price for one meeting is skipped, never fabricated.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.errors import RateLimitError, SourceError
from app.core.logging import get_logger
from app.markets._shared.rate_step import rate_step_distribution
from app.models.domain import MarketRef

logger = get_logger(__name__)

PriceFetcher = Callable[[int, int], Awaitable[Decimal | None]]
RateFetcher = Callable[[], Awaitable[Decimal | None]]


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def upcoming_meetings(meetings: list[date], *, today: date, horizon: int) -> list[date]:
    """The next ``horizon`` meetings on/after ``today``, ascending."""
    return sorted(m for m in meetings if m >= today)[:horizon]


async def discover_meetings(
    *,
    venue: str,
    topic: str,
    meetings: list[date],
    current_rate: RateFetcher,
    price_for: PriceFetcher,
    label_fn: Callable[[date], str],
    key_fn: Callable[[date], str],
    horizon: int = 2,
    limit: int = 50,
) -> list[MarketRef]:
    """Emit one MarketRef per upcoming meeting with the futures-implied distribution.

    ``current_rate()`` returns the latest overnight rate (the first meeting's start rate),
    fetched lazily so a topic with no upcoming meeting makes no network call. ``price_for(
    year, month)`` returns the average-overnight-rate future's price (or None). The start
    rate is chained forward to each subsequent meeting's implied post-meeting rate. Returns
    ``[]`` (clean degradation) on no upcoming meetings or a missing/failed current rate.
    """
    today = datetime.now(timezone.utc).date()
    upcoming = upcoming_meetings(meetings, today=today, horizon=min(horizon, limit))
    if not upcoming:
        logger.warning("rate_futures.no_upcoming_meetings", extra={"venue": venue, "topic": topic})
        return []

    try:
        r_start = await current_rate()
    except (SourceError, RateLimitError) as exc:
        logger.warning("rate_futures.rate_failed", extra={"venue": venue, "error": str(exc)})
        return []
    if r_start is None:
        logger.warning("rate_futures.rate_unavailable", extra={"venue": venue, "topic": topic})
        return []

    meeting_months = {(m.year, m.month) for m in meetings}
    refs: list[MarketRef] = []
    current = r_start
    for meeting in upcoming:
        try:
            price_m = await price_for(meeting.year, meeting.month)
            ny, nm = _next_month(meeting.year, meeting.month)
            price_next = await price_for(ny, nm)
        except (SourceError, RateLimitError) as exc:
            logger.warning(
                "rate_futures.meeting_failed",
                extra={"venue": venue, "meeting": meeting.isoformat(), "error": str(exc)},
            )
            continue
        if price_m is None:
            logger.warning(
                "rate_futures.price_unavailable",
                extra={"venue": venue, "meeting": meeting.isoformat()},
            )
            continue

        result = rate_step_distribution(
            meeting_date=meeting,
            price_meeting_month=price_m,
            r_start=current,
            price_next_month=price_next,
            next_month_has_meeting=(ny, nm) in meeting_months,
        )
        current = result.r_end  # chain forward for the next meeting

        refs.append(
            MarketRef(
                venue=venue,
                event_id=key_fn(meeting),
                market_key=key_fn(meeting),
                event_title=label_fn(meeting),
                outcomes=result.outcomes,
                resolved=False,
                enable_order_book=False,
                topic=topic,
                quoted_prices=result.probabilities,
                close_date=datetime(
                    meeting.year, meeting.month, meeting.day, tzinfo=timezone.utc
                ),
            )
        )
        logger.info(
            "rate_futures.meeting_priced",
            extra={"venue": venue, "meeting": meeting.isoformat(),
                   "method": result.method, "delta_bps": str(result.delta_bps)},
        )

    logger.info(
        "rate_futures.discover",
        extra={"venue": venue, "topic": topic, "markets": len(refs)},
    )
    return refs
