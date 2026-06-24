"""Registers the BTC price-threshold market with the derivative-market registry."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.markets._shared.registry import register
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
    ) -> list[MarketRef]:
        return await source.discover(
            clients["deribit"], topic, targets=settings.btc_target_list, limit=limit
        )


register(BtcPriceMarket())
