"""Nasdaq-100 relative value: Kalshi P(NDX ≥ strike) vs the CBOE options-implied
P(NDX ≥ strike) for the same strike + expiry.

Just the NDX identity (underlying + aliases + derivative venue); the parsing, matching, and
gap mechanism are the shared ``_shared/threshold_parse`` + ``_shared/threshold_compare``.
"""

from __future__ import annotations

from decimal import Decimal

from app.markets._shared import threshold_compare
from app.markets._shared.threshold_parse import make_parser, parse_strike
from app.models.digest import ThresholdDivergence
from app.models.domain import MarketObservation

_DERIVATIVE_VENUE = "cboe"
# Nasdaq-100 (~27,000) trades in coarse strike steps; snap to a 25-pt grid for matching.
_STRIKE_STEP = Decimal("25")
_ALIASES = ("nasdaq", "nasdaq-100", "nasdaq100", "ndx")

parse = make_parser(underlying="NDX", aliases=_ALIASES, derivative_venue=_DERIVATIVE_VENUE)
_parse_strike = parse_strike  # re-exported for tests


def compare(
    observations: list[MarketObservation], *, gap_threshold: Decimal
) -> list[ThresholdDivergence]:
    """Emit signed market−derivative P(above) gaps per (NDX, strike, expiry)."""
    return threshold_compare.compare(
        observations,
        parser=parse,
        derivative_venue=_DERIVATIVE_VENUE,
        gap_threshold=gap_threshold,
        strike_step=_STRIKE_STEP,
    )
