"""Deribit BTC options -> options-implied P(BTC > strike). Thin currency binding over the
shared Deribit source (``app/markets/_shared/deribit.py``); all I/O + math live there.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from app.markets._shared import deribit
from app.models.domain import MarketRef

VENUE = deribit.VENUE  # "deribit"


async def discover(
    client: httpx.AsyncClient,
    topic: str,
    *,
    targets: list[tuple[Decimal, str]],
    limit: int = 50,
) -> list[MarketRef]:
    """Emit one MarketRef per (strike, expiry token) BTC target with the implied P(above)."""
    return await deribit.discover_thresholds(
        client, topic, currency="BTC", underlying="BTC", targets=targets, limit=limit
    )
