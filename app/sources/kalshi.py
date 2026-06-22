"""Kalshi — discovery + prices via the official Trade API (public reads, no auth).

Verified live 2026-06-22 against official docs:
- Prices are DOLLAR-denominated strings already in 0..1 (``yes_bid_dollars`` etc.,
  NOT cents). Volumes are fixed-point strings (``volume_24h_fp``/``volume_fp``).
- The ``title`` field is deprecated; outcome labels live in ``yes_sub_title``.
- There is NO keyword search endpoint. Discovery is navigational:
  series (``/series?category=``) -> events (``/events?series_ticker=&with_nested_markets``)
  -> markets. We resolve a topic to series two ways: an explicit topic->series map,
  else a category scan keyword-matching series titles.
- Events expose ``mutually_exclusive``; an exclusive event's nested markets are the
  outcomes of one distribution. NO wallet logic — Kalshi has no chain.
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
_TERMINAL_STATUS = {"closed", "settled", "determined", "finalized", "inactive"}
_MAX_AUTO_SERIES = 5


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


def _yes_price(market: dict) -> Decimal | None:
    yes = _mid(_money(market.get("yes_bid_dollars")), _money(market.get("yes_ask_dollars")))
    if yes is None:
        yes = _money(market.get("last_price_dollars"))
    return yes


def _is_active(market: dict) -> bool:
    return str(market.get("status", "")) not in _TERMINAL_STATUS


def _event_volume(markets: list[dict]) -> Decimal | None:
    vols = [
        v
        for v in (_money(m.get("volume_24h_fp")) or _money(m.get("volume_fp")) for m in markets)
        if v is not None
    ]
    return sum(vols, Decimal(0)) if vols else None


def _event_to_refs(event: dict, *, topic: str) -> list[MarketRef]:
    markets = [m for m in event.get("markets", []) or [] if isinstance(m, dict)]
    active = [m for m in markets if _is_active(m)]
    if not active:
        return []
    event_ticker = str(event.get("event_ticker", ""))
    event_title = str(event.get("title") or event.get("sub_title") or topic)

    if bool(event.get("mutually_exclusive", False)) and len(active) > 1:
        outcomes: list[str] = []
        prices: list[Decimal] = []
        for m in active:
            yes = _yes_price(m)
            if yes is None:
                continue
            outcomes.append(str(m.get("yes_sub_title") or m.get("ticker")))
            prices.append(yes)
        if not outcomes:
            return []
        return [
            MarketRef(
                venue=VENUE,
                event_id=event_ticker,
                market_key=event_ticker,
                event_title=event_title,
                outcomes=outcomes,
                resolved=False,
                volume=_event_volume(active),
                topic=topic,
                quoted_prices=prices,
            )
        ]

    # Non-exclusive (or single) markets: one binary Yes/No ref each.
    refs: list[MarketRef] = []
    for m in active:
        ticker = m.get("ticker")
        if not ticker:
            raise SchemaDriftError("kalshi market missing 'ticker'")
        yes = _yes_price(m)
        if yes is None:
            continue
        label = str(m.get("yes_sub_title") or "Yes")
        refs.append(
            MarketRef(
                venue=VENUE,
                event_id=event_ticker or str(ticker),
                market_key=str(ticker),
                event_title=event_title,
                outcomes=[label, "No"],
                resolved=False,
                volume=_money(m.get("volume_24h_fp")) or _money(m.get("volume_fp")),
                topic=topic,
                quoted_prices=[yes, Decimal(1) - yes],
            )
        )
    return refs


async def _series_for_topic(
    client: httpx.AsyncClient, topic: str, category: str
) -> list[str]:
    """Keyword-match the topic against series titles within a category."""
    payload = await fetch_json(
        client, "/series", venue=VENUE, limiter=_limiter, params={"category": category}
    )
    if not isinstance(payload, dict) or "series" not in payload:
        raise SchemaDriftError("kalshi /series missing 'series'")
    words = [w for w in topic.lower().split() if len(w) > 2]
    matched: list[str] = []
    for s in payload["series"]:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title", "")).lower()
        if any(w in title for w in words):
            ticker = s.get("ticker")
            if ticker:
                matched.append(str(ticker))
    logger.info(
        "kalshi.series_match",
        extra={"topic": topic, "category": category, "matched": len(matched)},
    )
    return matched[:_MAX_AUTO_SERIES]


async def _events_for_series(
    client: httpx.AsyncClient, series_ticker: str, *, topic: str, limit: int
) -> list[MarketRef]:
    payload = await fetch_json(
        client,
        "/events",
        venue=VENUE,
        limiter=_limiter,
        params={
            "series_ticker": series_ticker,
            "with_nested_markets": "true",
            "status": "open",
            "limit": str(min(limit, 200)),
        },
    )
    if not isinstance(payload, dict) or "events" not in payload:
        raise SchemaDriftError("kalshi /events missing 'events'")
    refs: list[MarketRef] = []
    for event in payload["events"]:
        if isinstance(event, dict):
            refs.extend(_event_to_refs(event, topic=topic))
    return refs


async def discover(
    client: httpx.AsyncClient,
    topic: str,
    *,
    limit: int = 50,
    series_tickers: list[str] | None = None,
    category: str | None = None,
) -> list[MarketRef]:
    """Discover open Kalshi markets for ``topic`` via series -> events -> markets.

    Resolution: explicit ``series_tickers`` first; else keyword-match series titles
    within ``category``; else warn and return nothing (clean degradation).
    """
    series = list(series_tickers or [])
    if not series and category:
        series = await _series_for_topic(client, topic, category)

    if not series:
        logger.warning(
            "kalshi.no_series_no_match",
            extra={"topic": topic, "hint": "set KALSHI_SERIES_MAP or KALSHI_CATEGORY_MAP"},
        )
        return []

    refs: list[MarketRef] = []
    for s in series:
        refs.extend(await _events_for_series(client, s, topic=topic, limit=limit))
    logger.info(
        "kalshi.discover",
        extra={"topic": topic, "markets": len(refs), "series": series},
    )
    return refs
