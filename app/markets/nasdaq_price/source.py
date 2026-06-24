"""CBOE NDX options -> options-implied P(Nasdaq-100 > strike). Thin binding over the
shared CBOE source (``app/markets/_shared/cboe.py``); all I/O + math live there.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from app.markets._shared import cboe
from app.models.domain import MarketRef

VENUE = cboe.VENUE  # "cboe"


async def discover(
    client: httpx.AsyncClient,
    topic: str,
    *,
    targets: list[tuple[Decimal, str]],
    limit: int = 50,
) -> list[MarketRef]:
    """Emit one MarketRef per (strike, expiry token) NDX target with the implied P(above)."""
    return await cboe.discover_thresholds(
        client, topic, symbol="_NDX", underlying="NDX", targets=targets, limit=limit
    )
