"""Inbound request bodies."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.models.provenance import Venue


class AnalyzeRequest(BaseModel):
    topic: str = Field(min_length=1, description="Free-text topic to analyse across venues.")
    venues: list[Venue] | None = Field(
        default=None, description="Restrict to these venues; default = all."
    )
    limit: int | None = Field(default=None, ge=1, le=200, description="Max markets per venue.")
    as_of: date | None = Field(
        default=None,
        description="Return the point-in-time state as of this date (ISO 8601). "
                    "If omitted, returns the latest ingested state.",
    )
