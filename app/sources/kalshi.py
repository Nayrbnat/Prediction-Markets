"""Kalshi — discovery + prices via the official Trade API (public reads, no auth).

Kalshi quotes in cents (0-100); we convert to 0..1 probability units here so the
analysis layer stays venue-agnostic. NO wallet logic — Kalshi has no chain.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import httpx

from app.core.errors import SchemaDriftError
from app.core.http import fetch_json
from app.core.logging import get_logger
from app.core.rate_limit import AsyncRateLimiter
from app.models.domain import MarketRef

logger = get_logger(__name__)

VENUE = "kalshi"
_limiter = AsyncRateLimiter(rate_per_sec=8.0)
_CENTS = Decimal(100)


def _cents(value: object) -> Decimal | None:
    try:
        c = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return c / _CENTS


def _mid(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return bid if bid is not None else ask


def _market_to_ref(market: dict, *, topic: str) -> MarketRef | None:
    ticker = market.get("ticker")
    if not ticker:
        raise SchemaDriftError("kalshi market missing 'ticker'")
    yes = _mid(_cents(market.get("yes_bid")), _cents(market.get("yes_ask")))
    no = _mid(_cents(market.get("no_bid")), _cents(market.get("no_ask")))
    if yes is None:
        yes = _cents(market.get("last_price"))
    if yes is not None and no is None:
        no = Decimal(1) - yes
    if yes is None:
        return None  # no usable price; drop rather than fabricate
    volume = None
    try:
        if market.get("volume") is not None:
            volume = Decimal(str(market["volume"]))
    except (InvalidOperation, ValueError):
        volume = None
    return MarketRef(
        venue=VENUE,
        event_id=str(market.get("event_ticker", ticker)),
        market_key=str(ticker),
        event_title=str(market.get("title", topic)),
        outcomes=["Yes", "No"],
        resolved=str(market.get("status", "")) in {"closed", "settled", "determined"},
        volume=volume,
        topic=topic,
        quoted_prices=[yes, no if no is not None else Decimal(1) - yes],
    )


async def discover(
    client: httpx.AsyncClient,
    topic: str,
    *,
    limit: int = 50,
    series_ticker: str | None = None,
) -> list[MarketRef]:
    """Discover open Kalshi markets for ``topic``. Prefer ``series_ticker`` when the
    topic maps to a known series; otherwise scan open markets and title-filter."""
    params = {"limit": str(min(limit, 1000)), "status": "open"}
    if series_ticker:
        params["series_ticker"] = series_ticker

    payload = await fetch_json(client, "/markets", venue=VENUE, limiter=_limiter, params=params)
    if not isinstance(payload, dict) or "markets" not in payload:
        raise SchemaDriftError("kalshi /markets missing 'markets'")

    refs: list[MarketRef] = []
    needle = topic.lower()
    for market in payload["markets"]:
        if not isinstance(market, dict):
            continue
        if series_ticker is None and needle not in str(market.get("title", "")).lower():
            continue
        ref = _market_to_ref(market, topic=topic)
        if ref is not None and not ref.resolved:
            refs.append(ref)
    logger.info("kalshi.discover", extra={"topic": topic, "markets": len(refs)})
    return refs
