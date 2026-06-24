"""BTC relative value: Polymarket/Kalshi P(BTC ≥ strike) vs the Deribit options-implied
P(BTC ≥ strike) for the same strike + expiry.

Owns the BTC-specific parsing of each venue's threshold observations into the shared
``ThresholdPoint``; the matching + gap mechanism is the venue-agnostic
``app/markets/_shared/threshold_compare``.

NOTE (constitution §13): the Deribit side is exact (we emit those rows ourselves). The
Polymarket/Kalshi side is BEST-EFFORT string parsing of market titles/outcomes — the exact
phrasings must be verified against live BTC markets before relying on the comparison.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.markets._shared import threshold_compare
from app.markets._shared.threshold_compare import ThresholdPoint
from app.models.digest import ThresholdDivergence
from app.models.domain import MarketObservation

_DERIVATIVE_VENUE = "deribit"
_PREDICTION_VENUES = ("polymarket", "kalshi")
_UNDERLYING = "BTC"
_ALIASES = ("btc", "bitcoin")

# An outcome/label denotes the "above strike" side if it contains one of these.
_ABOVE_WORDS = (
    "≥", ">=", ">", "above", "over", "reach", "hit", "exceed",
    "at least", "more than", "or more", "yes",
)
# A strike must be $-anchored or carry a k/M suffix — avoids matching bare years (2026).
_DOLLAR = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM]?)")
_SUFFIX = re.compile(r"\b([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM])\b")


def _to_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _parse_strike(text: str) -> Decimal | None:
    for rx in (_DOLLAR, _SUFFIX):
        m = rx.search(text)
        if not m:
            continue
        num = _to_decimal(m.group(1).replace(",", ""))
        if num is None:
            continue
        suffix = (m.group(2) or "").lower()
        if suffix == "k":
            num *= Decimal(1000)
        elif suffix == "m":
            num *= Decimal(1_000_000)
        return num
    return None


def _is_above(text: str) -> bool:
    low = text.lower()
    return any(word in low for word in _ABOVE_WORDS)


def _deribit_point(obs: MarketObservation) -> ThresholdPoint | None:
    """Exact parse of our own deribit row (market_key 'BTC-26DEC26-150000')."""
    if obs.close_date is None or "≥" not in obs.outcome:
        return None  # only the "above" outcome carries P(above)
    parts = obs.market_key.split("-")
    if len(parts) < 3 or parts[0] != _UNDERLYING:
        return None
    strike = _to_decimal(parts[2])
    if strike is None:
        return None
    return ThresholdPoint(
        venue=_DERIVATIVE_VENUE, underlying=_UNDERLYING, strike=strike,
        year=obs.close_date.year, month=obs.close_date.month,
        prob_above=obs.probability, close_date=obs.close_date,
    )


def _prediction_point(obs: MarketObservation) -> ThresholdPoint | None:
    """Best-effort parse of a Polymarket/Kalshi BTC threshold row (the 'above' side)."""
    if obs.close_date is None:
        return None
    if not any(alias in obs.event_title.lower() for alias in _ALIASES):
        return None
    if not _is_above(obs.outcome):
        return None
    strike = _parse_strike(obs.outcome) or _parse_strike(obs.event_title)
    if strike is None:
        return None
    return ThresholdPoint(
        venue=obs.venue, underlying=_UNDERLYING, strike=strike,
        year=obs.close_date.year, month=obs.close_date.month,
        prob_above=obs.probability, close_date=obs.close_date,
    )


def parse(obs: MarketObservation) -> ThresholdPoint | None:
    if obs.venue == _DERIVATIVE_VENUE:
        return _deribit_point(obs)
    if obs.venue in _PREDICTION_VENUES:
        return _prediction_point(obs)
    return None


def compare(
    observations: list[MarketObservation], *, gap_threshold: Decimal
) -> list[ThresholdDivergence]:
    """Emit signed market−derivative P(above) gaps per (BTC, strike, expiry)."""
    return threshold_compare.compare(
        observations,
        parser=parse,
        derivative_venue=_DERIVATIVE_VENUE,
        gap_threshold=gap_threshold,
    )
