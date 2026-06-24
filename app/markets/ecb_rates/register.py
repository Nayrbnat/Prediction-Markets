"""Registers the ECB rates market with the derivative-market registry."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.markets._shared.registry import register
from app.markets.ecb_rates import source
from app.models.domain import MarketRef
from app.models.provenance import Venue


class EcbRatesMarket:
    """Descriptor for the ECB rates (€STR futures) derivative market."""

    name = "ecb_rates"
    venue: Venue = "estr"
    signals = ["futures-implied"]  # €STR-derived probability only — no order book / per-trader

    def base_urls(self, settings: Settings) -> dict[str, str]:
        return {
            "yahoo": settings.yahoo_chart_base_url,
            "ecb": settings.ecb_rates_base_url,
        }

    def enabled(self, settings: Settings) -> bool:
        return settings.ecb_enabled

    def serves_topic(self, topic: str, settings: Settings) -> bool:
        return topic in settings.ecb_topic_set

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
            clients["ecb"],
            topic,
            meetings=settings.ecb_meeting_dates,
            horizon=settings.ecb_meeting_horizon,
            limit=limit,
        )


register(EcbRatesMarket())
