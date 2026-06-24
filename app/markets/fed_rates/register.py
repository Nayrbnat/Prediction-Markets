"""Registers the Fed rates market with the derivative-market registry."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.markets._shared.registry import register
from app.markets.fed_rates import source
from app.models.domain import MarketRef
from app.models.provenance import Venue


class FedRatesMarket:
    """Descriptor for the Fed rates (CME ZQ) derivative market."""

    name = "fed_rates"
    venue: Venue = "cme"
    signals = ["futures-implied"]  # ZQ-derived probability only — no order book / per-trader data

    def base_urls(self, settings: Settings) -> dict[str, str]:
        return {
            "yahoo": settings.yahoo_chart_base_url,
            "nyfed": settings.nyfed_rates_base_url,
        }

    def enabled(self, settings: Settings) -> bool:
        return settings.cme_enabled

    def serves_topic(self, topic: str, settings: Settings) -> bool:
        return topic in settings.cme_topic_set

    async def discover(
        self,
        clients: dict[str, httpx.AsyncClient],
        settings: Settings,
        topic: str,
        *,
        limit: int,
        prediction_refs: list[MarketRef],  # unused (rate markets use config meeting dates)
    ) -> list[MarketRef]:
        return await source.discover(
            clients["yahoo"],
            clients["nyfed"],
            topic,
            meetings=settings.fomc_meeting_dates,
            horizon=settings.cme_meeting_horizon,
            limit=limit,
        )


register(FedRatesMarket())
