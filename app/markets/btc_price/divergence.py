"""BTC relative value: Polymarket/Kalshi P(BTC ≥ strike) vs the Deribit options-implied
P(BTC ≥ strike) for the same strike + expiry.

Just the BTC identity (underlying + name aliases); the parsing, matching, and gap
mechanism are the shared ``_shared/threshold_parse`` + ``_shared/threshold_compare``.
"""

from __future__ import annotations

from decimal import Decimal

from app.markets._shared import threshold_compare
from app.markets._shared.threshold_parse import make_parser, parse_strike
from app.models.digest import ThresholdDivergence
from app.models.domain import MarketObservation

_DERIVATIVE_VENUE = "deribit"
# Strike-grid for matching market↔options strikes. BTC trades in ~$1k strike steps near
# spot, so snap to a $1,000 grid (e.g. $59,950 and $60,000 are the same threshold).
_STRIKE_STEP = Decimal("1000")

parse = make_parser(underlying="BTC", aliases=("btc", "bitcoin"))
_parse_strike = parse_strike  # re-exported for tests


def compare(
    observations: list[MarketObservation], *, gap_threshold: Decimal
) -> list[ThresholdDivergence]:
    """Emit signed market−derivative P(above) gaps per (BTC, strike, expiry)."""
    return threshold_compare.compare(
        observations,
        parser=parse,
        derivative_venue=_DERIVATIVE_VENUE,
        gap_threshold=gap_threshold,
        strike_step=_STRIKE_STEP,
    )
