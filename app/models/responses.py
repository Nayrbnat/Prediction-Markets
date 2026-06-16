"""Outbound response models. These are what OpenAPI renders at /docs."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field

from app import __version__
from app.models.domain import EventDistribution, MarketRef
from app.models.provenance import DISCLAIMER, Venue


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HealthStatus(BaseModel):
    status: str
    database: bool
    version: str = __version__


class RefreshResult(BaseModel):
    """Result of one ingestion run (the cron target)."""

    status: str = "ok"
    markets: int
    changes: int
    purged: int


class VenueAvailability(BaseModel):
    """Which signals a venue contributed for this topic (degrade-gracefully record)."""

    venue: Venue
    matched: bool
    signals: list[str] = Field(
        default_factory=list,
        description="e.g. ['price', 'volume', 'depth']; smart-money tilt is v2 / Polymarket-only.",
    )
    note: str | None = None


class MarketDetail(BaseModel):
    """One market's current state for GET /markets/{venue}/{id}."""

    market: MarketRef
    distribution: EventDistribution | None = None
    stale: bool = False


class HistoryPoint(BaseModel):
    """One row of the change-log time series."""

    observed_at: datetime
    probability: Decimal
    previous_probability: Decimal | None = None
    delta: Decimal | None = None


class TopicAnalysis(BaseModel):
    """The headline POST /analyze response."""

    topic: str
    generated_at: datetime = Field(default_factory=_now)
    stale: bool = Field(
        default=False, description="True if served from store without a live top-up."
    )
    markets: list[MarketRef] = Field(default_factory=list)
    distributions: list[EventDistribution] = Field(default_factory=list)
    venue_availability: list[VenueAvailability] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="Degradation / caveat notes.")
    llm_synthesis: None = Field(
        default=None, description="Typed LLM synthesis — deferred to v2; always null in v1."
    )
    disclaimer: str = DISCLAIMER
