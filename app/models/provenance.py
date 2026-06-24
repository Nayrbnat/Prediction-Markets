"""Provenance and confidence — attached to every probability so "why 62%?" is
always answerable, and so thin/stale markets are never presented as deep ones.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Venue = Literal["polymarket", "kalshi", "cme", "deribit"]
ConfidenceLevel = Literal["ok", "thin", "stale"]

DISCLAIMER = (
    "Decision-support data only. Market-implied probabilities are derived from live "
    "prices and may be thin, stale, or divergent across venues. Not financial advice."
)


class Provenance(BaseModel):
    """Where a single number came from."""

    venue: Venue
    endpoint: str = Field(description="The source endpoint the raw value was read from.")
    raw_value: str = Field(description="The raw price/string exactly as the source returned it.")
    observed_at: datetime
    normalisation_factor: Decimal | None = Field(
        default=None,
        description="Factor applied to normalise sibling outcomes to sum to 1, if any.",
    )


class ConfidenceFlag(BaseModel):
    """The low-confidence signal for thin/stale/illiquid markets."""

    level: ConfidenceLevel = "ok"
    reasons: list[str] = Field(default_factory=list)
