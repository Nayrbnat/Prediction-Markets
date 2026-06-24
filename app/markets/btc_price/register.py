"""Registers the BTC price-threshold market with the derivative-market registry."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.markets._shared.registry import register
from app.markets._shared.threshold_parse import targets_from_refs
from app.markets.btc_price import source
from app.models.domain import MarketRef
from app.models.provenance import Venue


class BtcPriceMarket:
    """Descriptor for the BTC price-threshold (Deribit options) derivative market."""

    name = "btc_price"
    venue: Venue = "deribit"
    signals = ["options-implied"]  # risk-neutral threshold probability — no per-trader data

    def base_urls(self, settings: Settings) -> dict[str, str]:
        return {"deribit": settings.deribit_base_url}

    def enabled(self, settings: Settings) -> bool:
        return settings.btc_enabled

    def serves_topic(self, topic: str, settings: Settings) -> bool:
        return topic in settings.btc_topic_set

    async def discover(
        self,
        clients: dict[str, httpx.AsyncClient],
        settings: Settings,
        topic: str,
        *,
        limit: int,
        prediction_refs: list[MarketRef],
    ) -> list[MarketRef]:
        # Dynamic targeting: derive (strike, expiry) from the live Polymarket/Kalshi BTC
        # markets discovered this run; any configured BTC_TARGETS supplement them.
        targets = sorted({
            *targets_from_refs(prediction_refs, underlying="BTC", aliases=("btc", "bitcoin")),
            *settings.btc_target_list,
        })
        return await source.discover(clients["deribit"], topic, targets=targets, limit=limit)


register(BtcPriceMarket())
