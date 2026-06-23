"""Internal domain contracts shared across layers. Money/prices are ``Decimal``;
venues/sides are ``Literal``; timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.models.provenance import ConfidenceFlag, Provenance, Venue

Priority = Literal["high", "normal"]


class MarketRef(BaseModel):
    """What discovery returns: identity of a market across a venue."""

    venue: Venue
    event_id: str
    market_key: str = Field(
        description="Stable per-venue id (Polymarket conditionId / Kalshi ticker)."
    )
    event_title: str
    outcomes: list[str]
    token_ids: list[str] = Field(
        default_factory=list, description="CLOB token ids (Polymarket only)."
    )
    condition_id: str | None = None
    resolved: bool = False
    enable_order_book: bool = True
    # Aggregate market volume used for the thin-market confidence flag in
    # assess_confidence(); per-outcome volumes live in outcome_volumes_24h /
    # outcome_volumes_total.
    volume: Decimal | None = None
    liquidity: Decimal | None = None
    topic: str | None = None
    category: str | None = None
    quoted_prices: list[Decimal] | None = Field(
        default=None,
        description="Quick-read price per outcome (0..1), aligned to `outcomes`, from discovery.",
    )
    # Per-outcome parallel arrays (aligned to outcomes[]).  NULL means the venue
    # did not expose that value for the given outcome side — never fabricated.
    best_bids: list[Decimal | None] | None = None
    best_asks: list[Decimal | None] | None = None
    last_trades: list[Decimal | None] | None = None
    outcome_volumes_24h: list[Decimal | None] | None = None
    outcome_volumes_total: list[Decimal | None] | None = None
    open_interests: list[Decimal | None] | None = None
    # Resolution date (market-level, same for all outcomes in the same market).
    close_date: datetime | None = None


class OrderBookTop(BaseModel):
    """Top-of-book snapshot from a venue's order book."""

    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    mid: Decimal | None = None
    spread: Decimal | None = None
    depth: Decimal | None = None
    observed_at: datetime


class OutcomeProbability(BaseModel):
    """One outcome's market-implied probability with full provenance."""

    outcome: str
    probability: Decimal = Field(ge=0, le=1)
    raw_price: Decimal
    provenance: Provenance
    confidence: ConfidenceFlag = Field(default_factory=ConfidenceFlag)
    # Market microstructure fields — NULL when not available for this outcome/side.
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    spread: Decimal | None = None
    last_trade_price: Decimal | None = None
    volume_24h: Decimal | None = None
    volume_total: Decimal | None = None
    open_interest: Decimal | None = None


class EventDistribution(BaseModel):
    """Sibling outcomes of one event, raw and normalised to sum to 1."""

    venue: Venue
    event_title: str
    market_key: str
    outcomes: list[OutcomeProbability]
    raw_sum: Decimal
    factor: Decimal = Field(description="Normalisation factor: normalised = raw / raw_sum.")
    normalised: bool = True


class MarketObservation(BaseModel):
    """The persisted row shape (one per venue/market_key/outcome).

    Used by pricing helpers (distribution_from_observations, ref_from_observations)
    and by ingestion (observations_from_distribution).
    """

    venue: Venue
    market_key: str
    outcome: str
    event_title: str
    topic: str | None = None
    category: str | None = None
    probability: Decimal = Field(ge=0, le=1)
    previous_probability: Decimal | None = None
    probability_delta: Decimal | None = None
    raw_price: Decimal
    volume_24h: Decimal | None = None
    volume_total: Decimal | None = None
    liquidity: Decimal | None = None
    close_date: datetime | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    spread: Decimal | None = None
    last_trade_price: Decimal | None = None
    open_interest: Decimal | None = None
    confidence: str = "ok"
    priority: Priority = "normal"
    tracked: bool = False
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_changed_at: datetime | None = None
    updated_at: datetime | None = None


class ProbabilityChange(BaseModel):
    """The delta between two observations and whether it is material."""

    previous: Decimal | None
    current: Decimal
    delta: Decimal
    material: bool
