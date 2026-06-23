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


class DivergenceItem(BaseModel):
    """Relative value: a prediction-market probability vs the Fed-funds-futures-implied
    probability for the SAME FOMC meeting + outcome. ``gap`` is signed (market − futures);
    it is a signal to investigate, NOT arbitrage (futures are risk-neutral)."""

    meeting: str  # e.g. "September 2026"
    market_venue: str  # polymarket | kalshi
    outcome: str  # canonical bucket label
    market_prob: Decimal = Field(ge=0, le=1)
    futures_prob: Decimal = Field(ge=0, le=1)
    gap: Decimal  # signed: market_prob − futures_prob
    material: bool = False
    close_date: datetime | None = None


class MarketDigest(BaseModel):
    """The full daily digest payload — movers + current state of tracked markets."""

    generated_for: date
    mover_threshold: Decimal
    movers: list[MoverItem] = Field(default_factory=list)
    tracked: list[TrackedMarket] = Field(default_factory=list)
    divergences: list[DivergenceItem] = Field(default_factory=list)
    mover_count: int = 0
    tracked_count: int = 0
    divergence_count: int = 0  # count of MATERIAL divergences (|gap| >= threshold)
