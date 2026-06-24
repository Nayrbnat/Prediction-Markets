"""Models for the company-bet scan (discovery/listing only).

A lightweight enumeration of specific-company prediction markets available on Kalshi /
Polymarket — NOT a relative-value comparison and NOT persisted. Refreshed on its own
(5-day) cron so the user can see what company bets are tradeable.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# Coarse classification of what a company bet is about.
BetKind = Literal["price", "kpi-or-event"]


class CompanyBet(BaseModel):
    """One discovered company-specific market (just enough to identify it)."""

    venue: str  # "kalshi" | "polymarket"
    source_key: str  # Kalshi series ticker / Polymarket market_key
    title: str
    kind: BetKind = "kpi-or-event"
    close_date: datetime | None = None


class CompanyScan(BaseModel):
    """The result of one company-bet scan run."""

    generated_for: date
    bets: list[CompanyBet] = Field(default_factory=list)
    count: int = 0
    kalshi_count: int = 0
    polymarket_count: int = 0
    truncated: bool = False  # True if the safety cap was hit
