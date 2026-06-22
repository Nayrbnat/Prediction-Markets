"""Digest output models — MoverItem, TrackedMarket, MarketDigest.

All probabilities/deltas are ``Decimal``; dates are UTC-aware.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class MoverItem(BaseModel):
    """One outcome that moved ≥ threshold day-over-day (tracked markets only)."""

    venue: str
    event_title: str
    market_key: str
    outcome: str
    previous: Decimal = Field(ge=0, le=1)
    current: Decimal = Field(ge=0, le=1)
    delta: Decimal  # signed; negative means the probability fell
    close_date: datetime | None = None


class OutcomeProb(BaseModel):
    """Current normalised probability for one outcome."""

    outcome: str
    probability: Decimal = Field(ge=0, le=1)


class TrackedMarket(BaseModel):
    """Current probability snapshot for a tracked market (all outcomes)."""

    venue: str
    event_title: str
    market_key: str
    outcomes: list[OutcomeProb]


class MarketDigest(BaseModel):
    """The full daily digest payload — movers + current state of tracked markets."""

    generated_for: date
    mover_threshold: Decimal
    movers: list[MoverItem] = Field(default_factory=list)
    tracked: list[TrackedMarket] = Field(default_factory=list)
    mover_count: int = 0
    tracked_count: int = 0
