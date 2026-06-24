"""Registers the Nasdaq-100 price-threshold market with the derivative-market registry."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.markets._shared import cboe
from app.markets._shared.registry import register
from app.markets._shared.threshold_parse import targets_from_refs
from app.markets.nasdaq_price import source
from app.models.domain import MarketRef
from app.models.provenance import Venue

_ALIASES = ("nasdaq", "nasdaq-100", "nasdaq100", "ndx")


class NasdaqPriceMarket:
    """Descriptor for the Nasdaq-100 price-threshold (CBOE NDX options) derivative market."""

    name = "nasdaq_price"
    venue: Venue = "cboe"
    signals = ["options-implied"]  # risk-neutral threshold probability — no per-trader data

    def base_urls(self, settings: Settings) -> dict[str, str]:
        return {"cboe": settings.cboe_base_url}

    def enabled(self, settings: Settings) -> bool:
        return settings.nasdaq_enabled

    def serves_topic(self, topic: str, settings: Settings) -> bool:
        return topic in settings.nasdaq_topic_set

    async def discover(
        self,
        clients: dict[str, httpx.AsyncClient],
        settings: Settings,
        topic: str,
        *,
        limit: int,
        prediction_refs: list[MarketRef],
    ) -> list[MarketRef]:
        # Dynamic targeting: derive (strike, CBOE expiry token) from the live Kalshi/Polymarket
        # Nasdaq-100 markets discovered this run; any configured NASDAQ_TARGETS supplement them.
        derived = targets_from_refs(
            prediction_refs, underlying="NDX", aliases=_ALIASES, token_fn=cboe.cboe_token
        )
        targets = sorted({*derived, *settings.nasdaq_target_list})
        return await source.discover(clients["cboe"], topic, targets=targets, limit=limit)


register(NasdaqPriceMarket())
