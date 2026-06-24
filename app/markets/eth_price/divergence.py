"""ETH relative value: Polymarket/Kalshi P(ETH ≥ strike) vs the Deribit options-implied
P(ETH ≥ strike) for the same strike + expiry.

Just the ETH identity (underlying + name aliases); the parsing, matching, and gap
mechanism are the shared ``_shared/threshold_parse`` + ``_shared/threshold_compare``.
"""

from __future__ import annotations

from decimal import Decimal

from app.markets._shared import threshold_compare
from app.markets._shared.threshold_parse import make_parser, parse_strike
from app.models.digest import ThresholdDivergence
from app.models.domain import MarketObservation

_DERIVATIVE_VENUE = "deribit"
# Strike-grid for matching market↔options strikes. ETH trades ~2 orders of magnitude below
# BTC, so a $1k grid would collapse every strike together — snap to a $50 grid instead.
_STRIKE_STEP = Decimal("50")

parse = make_parser(underlying="ETH", aliases=("eth", "ether", "ethereum"))
_parse_strike = parse_strike  # re-exported for tests


def compare(
    observations: list[MarketObservation], *, gap_threshold: Decimal
) -> list[ThresholdDivergence]:
    """Emit signed market−derivative P(above) gaps per (ETH, strike, expiry)."""
    return threshold_compare.compare(
        observations,
        parser=parse,
        derivative_venue=_DERIVATIVE_VENUE,
        gap_threshold=gap_threshold,
        strike_step=_STRIKE_STEP,
    )
