"""Kalshi — discovery + prices via the official Trade API (public reads, no auth).

Verified live 2026-06-16: the API returns DOLLAR-denominated string prices
(``yes_bid_dollars`` etc., already in 0..1 probability units — NOT cents) and
fixed-point string sizes/volumes (``volume_24h_fp``, ``volume_fp``). The legacy
cent fields (``yes_bid``/``yes_ask``/``last_price``) have been removed.
NO wallet logic — Kalshi has no chain.
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


def _money(value: object) -> Decimal | None:
    """Parse a dollar-denominated string (0..1 probability units) to Decimal."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _mid(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return bid if bid is not None else ask


def _market_to_ref(market: dict, *, topic: str) -> MarketRef | None:
    ticker = market.get("ticker")
    if not ticker:
        raise SchemaDriftError("kalshi market missing 'ticker'")
    yes = _mid(_money(market.get("yes_bid_dollars")), _money(market.get("yes_ask_dollars")))
    no = _mid(_money(market.get("no_bid_dollars")), _money(market.get("no_ask_dollars")))
    if yes is None:
        yes = _money(market.get("last_price_dollars"))
    if yes is None:
        return None  # no usable price; drop rather than fabricate
    if no is None:
        no = Decimal(1) - yes
    volume = _money(market.get("volume_24h_fp"))
    if volume is None:
        volume = _money(market.get("volume_fp"))
    return MarketRef(
        venue=VENUE,
        event_id=str(market.get("event_ticker", ticker)),
        market_key=str(ticker),
        event_title=str(market.get("title", topic)),
        outcomes=["Yes", "No"],
        resolved=str(market.get("status", "")) in {"closed", "settled", "determined"},
        volume=volume,
        topic=topic,
        quoted_prices=[yes, no],
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

    if series_ticker is None and not refs:
        # Verified live: the unfiltered open-markets scan rarely matches a free-text
        # topic (top markets are multi-leg combos). Map the topic to a series ticker
        # via KALSHI_SERIES_MAP for reliable Kalshi coverage.
        logger.warning(
            "kalshi.no_series_no_match",
            extra={"topic": topic, "hint": "set KALSHI_SERIES_MAP for this topic"},
        )
    logger.info(
        "kalshi.discover",
        extra={"topic": topic, "markets": len(refs), "series_ticker": series_ticker},
    )
    return refs
