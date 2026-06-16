"""Polymarket Gamma — discovery + quick-read prices (public, no auth).

Topic -> matched events/markets via /public-search. Returns typed MarketRef with
quoted prices; precise prices come from the CLOB client. No analysis here.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

import httpx

from app.core.errors import SchemaDriftError
from app.core.http import fetch_json
from app.core.logging import get_logger
from app.core.rate_limit import AsyncRateLimiter
from app.models.domain import MarketRef

logger = get_logger(__name__)

VENUE = "polymarket"
# Gamma general limit is generous; keep well under it. Verify current limits at build time.
_limiter = AsyncRateLimiter(rate_per_sec=8.0)


def _as_list(value: object) -> list:
    """Gamma returns some array fields as JSON-encoded strings."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    if isinstance(value, list):
        return value
    return []


def _dec(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _market_to_ref(
    market: dict, *, event_id: str, event_title: str, topic: str
) -> MarketRef | None:
    try:
        outcomes = [str(o) for o in _as_list(market.get("outcomes"))]
        if not outcomes:
            return None
        prices = [
            p for p in (_dec(x) for x in _as_list(market.get("outcomePrices"))) if p is not None
        ]
        token_ids = [str(t) for t in _as_list(market.get("clobTokenIds"))]
        market_key = str(market.get("conditionId") or market.get("id") or "")
        if not market_key:
            return None
        return MarketRef(
            venue=VENUE,
            event_id=event_id,
            market_key=market_key,
            event_title=event_title,
            outcomes=outcomes,
            token_ids=token_ids,
            condition_id=market.get("conditionId"),
            resolved=bool(market.get("closed", False)),
            enable_order_book=bool(market.get("enableOrderBook", True)),
            volume=_dec(market.get("volume") or market.get("volumeNum")),
            liquidity=_dec(market.get("liquidity") or market.get("liquidityNum")),
            topic=topic,
            quoted_prices=prices or None,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise SchemaDriftError(f"gamma market shape unexpected: {exc}") from exc


async def discover(client: httpx.AsyncClient, topic: str, *, limit: int = 50) -> list[MarketRef]:
    """Discover active Polymarket markets matching ``topic``."""
    payload = await fetch_json(
        client,
        "/public-search",
        venue=VENUE,
        limiter=_limiter,
        params={"q": topic, "limit_per_type": str(limit), "events_status": "active"},
    )
    if not isinstance(payload, dict) or "events" not in payload:
        raise SchemaDriftError("gamma /public-search missing 'events'")

    refs: list[MarketRef] = []
    for event in payload["events"]:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id", ""))
        event_title = str(event.get("title", topic))
        for market in event.get("markets", []) or []:
            if not isinstance(market, dict):
                continue
            ref = _market_to_ref(market, event_id=event_id, event_title=event_title, topic=topic)
            if ref is not None and not ref.resolved:
                refs.append(ref)
    logger.info("gamma.discover", extra={"topic": topic, "markets": len(refs)})
    return refs
