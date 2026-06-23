"""Polymarket CLOB — precise order book for a token (public, no auth).

token_id -> best bid/ask/mid/spread as OrderBookTop. No probability math here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from app.core.errors import SchemaDriftError
from app.core.http import fetch_json
from app.core.logging import get_logger
from app.core.rate_limit import AsyncRateLimiter
from app.models.domain import OrderBookTop

logger = get_logger(__name__)

VENUE = "polymarket"
_limiter = AsyncRateLimiter(rate_per_sec=8.0)


def _dec(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _best(levels: list, *, side: str) -> tuple[Decimal | None, Decimal | None]:
    """Return (price, size) of the best level. Bids: highest price; asks: lowest."""
    prices: list[tuple[Decimal, Decimal]] = []
    for lvl in levels or []:
        price = _dec(lvl.get("price")) if isinstance(lvl, dict) else None
        size = _dec(lvl.get("size")) if isinstance(lvl, dict) else None
        if price is not None:
            prices.append((price, size or Decimal(0)))
    if not prices:
        return None, None
    chosen = max(prices, key=lambda p: p[0]) if side == "bid" else min(prices, key=lambda p: p[0])
    return chosen


async def order_book(client: httpx.AsyncClient, token_id: str) -> OrderBookTop:
    payload = await fetch_json(
        client, "/book", venue=VENUE, limiter=_limiter, params={"token_id": token_id}
    )
    if not isinstance(payload, dict):
        raise SchemaDriftError("clob /book did not return an object")

    best_bid, bid_size = _best(payload.get("bids", []), side="bid")
    best_ask, ask_size = _best(payload.get("asks", []), side="ask")
    mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
    spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
    depth = (bid_size or Decimal(0)) + (ask_size or Decimal(0))

    return OrderBookTop(
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        spread=spread,
        depth=depth if depth > 0 else None,
        observed_at=datetime.now(timezone.utc),
    )
