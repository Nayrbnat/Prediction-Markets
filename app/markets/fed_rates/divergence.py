"""Fed relative value: Polymarket/Kalshi probability vs the ZQ-implied probability
for the same FOMC meeting.

Owns the Fed label->bucket table; the comparison mechanism is the shared, venue-agnostic
``app/markets/_shared/rate_compare``. Public API (``canonical_outcome``, ``cut_hold_raise``,
``compare``) is preserved for the digest service and tests.
"""

from __future__ import annotations

from decimal import Decimal

from app.markets._shared import rate_compare
from app.models.digest import DivergenceItem
from app.models.domain import MarketObservation

_DERIVATIVE_VENUE = "cme"

# Each venue's raw outcome label (whitespace/case-normalised) -> canonical 25bps bucket.
_TABLE: dict[str, str] = {
    # CME (our own source)
    "50+ bps cut": "50+ bps cut",
    "25 bps cut": "25 bps cut",
    "no change": "No change",
    "25 bps hike": "25 bps hike",
    "50+ bps hike": "50+ bps hike",
    # Kalshi
    "fed maintains rate": "No change",
    "cut 25bps": "25 bps cut",
    "hike 25bps": "25 bps hike",
    "cut >25bps": "50+ bps cut",
    "hike >25bps": "50+ bps hike",
    # Polymarket
    "25 bps decrease": "25 bps cut",
    "25 bps increase": "25 bps hike",
    "50+ bps decrease": "50+ bps cut",
    "50+ bps increase": "50+ bps hike",
}

canonical_outcome = rate_compare.make_canonical(_TABLE)


def cut_hold_raise(
    pairs: list[tuple[str, Decimal]],
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Collapse a Fed market's (outcome, probability) pairs into (cut, hold, raise)."""
    return rate_compare.cut_hold_raise(pairs, canonical=canonical_outcome)


def compare(
    observations: list[MarketObservation], *, gap_threshold: Decimal
) -> list[DivergenceItem]:
    """Emit signed market−futures gaps per (meeting, prediction-venue, canonical outcome)."""
    return rate_compare.compare(
        observations,
        canonical=canonical_outcome,
        derivative_venue=_DERIVATIVE_VENUE,
        gap_threshold=gap_threshold,
    )
