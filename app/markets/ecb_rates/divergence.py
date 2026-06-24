"""ECB relative value: Polymarket/Kalshi probability vs the €STR-futures-implied
probability for the same ECB Governing Council meeting.

Owns the ECB label->bucket table; the comparison mechanism is the shared, venue-agnostic
``app/markets/_shared/rate_compare``. Public API (``canonical_outcome``, ``cut_hold_raise``,
``compare``) is preserved for the digest service and tests.

§13: the ECB prediction-market outcome phrasings below are best-effort — confirm the exact
Polymarket/Kalshi ECB outcome strings against live data and extend the table as needed.
"""

from __future__ import annotations

from decimal import Decimal

from app.markets._shared import rate_compare
from app.models.digest import DivergenceItem
from app.models.domain import MarketObservation

_DERIVATIVE_VENUE = "estr"

# Each venue's raw outcome label (whitespace/case-normalised) -> canonical 25bps bucket.
_TABLE: dict[str, str] = {
    # €STR futures (our own source) — generic rate-step labels.
    "50+ bps cut": "50+ bps cut",
    "25 bps cut": "25 bps cut",
    "no change": "No change",
    "25 bps hike": "25 bps hike",
    "50+ bps hike": "50+ bps hike",
    # ECB prediction-market phrasings (best-effort — see §13 note).
    "ecb maintains rate": "No change",
    "ecb holds": "No change",
    "ecb cuts 25bps": "25 bps cut",
    "ecb cut 25 bps": "25 bps cut",
    "ecb hikes 25bps": "25 bps hike",
    "ecb hike 25 bps": "25 bps hike",
    "25 bps decrease": "25 bps cut",
    "25 bps increase": "25 bps hike",
    "50+ bps decrease": "50+ bps cut",
    "50+ bps increase": "50+ bps hike",
}

canonical_outcome = rate_compare.make_canonical(_TABLE)


def cut_hold_raise(
    pairs: list[tuple[str, Decimal]],
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Collapse an ECB market's (outcome, probability) pairs into (cut, hold, raise)."""
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
