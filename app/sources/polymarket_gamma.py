"""Polymarket Gamma — discovery + quick-read prices (public, no auth).

Topic -> matched events via /public-search. Returns typed MarketRef grouped at the
EVENT level: a mutually-exclusive multi-outcome event (``negRisk``) becomes one
MarketRef whose outcomes are the candidate markets (label = ``groupItemTitle``,
price = each candidate's Yes price); a single binary market becomes one Yes/No
MarketRef; a non-exclusive multi-market event becomes one binary MarketRef per
child. Precise prices come from the CLOB client (binary events only). No analysis.
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


def _binary_ref(
    market: dict, *, event_id: str, event_title: str, topic: str
) -> MarketRef | None:
    """One Yes/No MarketRef from a single binary market (CLOB-priced)."""
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


def _multi_outcome_ref(
    event: dict, active: list[dict], *, topic: str
) -> MarketRef | None:
    """One MarketRef for a mutually-exclusive event: each active child contributes
    one outcome (label = groupItemTitle, probability = its Yes price). token_ids is
    left empty so pricing uses these quick-read prices rather than N CLOB calls."""
    outcomes: list[str] = []
    prices: list[Decimal] = []
    for m in active:
        op = _as_list(m.get("outcomePrices"))
        yes_price = _dec(op[0]) if op else None
        if yes_price is None:
            continue
        label = m.get("groupItemTitle") or m.get("question") or m.get("slug")
        outcomes.append(str(label))
        prices.append(yes_price)
    if not outcomes:
        return None
    event_id = str(event.get("id", ""))
    return MarketRef(
        venue=VENUE,
        event_id=event_id,
        market_key=event_id,
        event_title=str(event.get("title", topic)),
        outcomes=outcomes,
        token_ids=[],  # grouped: use quoted prices, skip per-candidate CLOB
        resolved=False,
        enable_order_book=False,
        volume=_dec(event.get("volume")),
        liquidity=_dec(event.get("liquidity")),
        topic=topic,
        quoted_prices=prices,
    )


def _event_to_refs(event: dict, *, topic: str) -> list[MarketRef]:
    markets = [m for m in event.get("markets", []) or [] if isinstance(m, dict)]
    active = [m for m in markets if not bool(m.get("closed", False))]
    if not active:
        return []
    event_id = str(event.get("id", ""))
    event_title = str(event.get("title", topic))

    if bool(event.get("negRisk", False)) and len(active) > 1:
        ref = _multi_outcome_ref(event, active, topic=topic)
        return [ref] if ref is not None else []

    refs: list[MarketRef] = []
    for m in active:
        ref = _binary_ref(m, event_id=event_id, event_title=event_title, topic=topic)
        if ref is not None:
            refs.append(ref)
    return refs


async def discover(client: httpx.AsyncClient, topic: str, *, limit: int = 50) -> list[MarketRef]:
    """Discover active Polymarket markets matching ``topic``, grouped by event."""
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
        if isinstance(event, dict):
            refs.extend(_event_to_refs(event, topic=topic))
    logger.info("gamma.discover", extra={"topic": topic, "markets": len(refs)})
    return refs
