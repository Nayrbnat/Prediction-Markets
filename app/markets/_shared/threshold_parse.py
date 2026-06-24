"""Shared parsing for crypto price-threshold markets.

Turns a ``MarketObservation`` into a ``ThresholdPoint`` (the 'above strike' side) for
both our own exact ``deribit`` rows and best-effort Polymarket/Kalshi rows. The only
per-market inputs are the underlying symbol and its name aliases, so each concrete
crypto market (BTC, ETH, ...) is just ``make_parser(underlying=..., aliases=...)`` — no
duplicated regex/keyword logic.

NOTE (constitution §13): the deribit side is exact (we emit those rows); the
prediction-market side is BEST-EFFORT string parsing and must be verified against live
market phrasings before relying on it.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.markets._shared.threshold_compare import ThresholdPoint
from app.models.domain import MarketObservation

_DEFAULT_DERIVATIVE_VENUE = "deribit"
_DEFAULT_PREDICTION_VENUES = ("polymarket", "kalshi")

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


def parse_strike(text: str) -> Decimal | None:
    """Extract a dollar strike from free text (``$150,000``, ``150k``, ``$2.5M``)."""
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


def is_above(text: str) -> bool:
    """Whether a label denotes the 'above strike' side."""
    low = text.lower()
    return any(word in low for word in _ABOVE_WORDS)


def make_parser(
    *,
    underlying: str,
    aliases: tuple[str, ...],
    derivative_venue: str = _DEFAULT_DERIVATIVE_VENUE,
    prediction_venues: tuple[str, ...] = _DEFAULT_PREDICTION_VENUES,
):
    """Build a ``parse(obs) -> ThresholdPoint | None`` for one underlying."""

    def _deribit_point(obs: MarketObservation) -> ThresholdPoint | None:
        # Exact parse of our own row (market_key like 'BTC-26DEC26-150000').
        if obs.close_date is None or "≥" not in obs.outcome:
            return None  # only the "above" outcome carries P(above)
        parts = obs.market_key.split("-")
        if len(parts) < 3 or parts[0] != underlying:
            return None
        strike = _to_decimal(parts[2])
        if strike is None:
            return None
        return ThresholdPoint(
            venue=derivative_venue, underlying=underlying, strike=strike,
            year=obs.close_date.year, month=obs.close_date.month,
            prob_above=obs.probability, close_date=obs.close_date,
        )

    def _prediction_point(obs: MarketObservation) -> ThresholdPoint | None:
        # Best-effort parse of a Polymarket/Kalshi threshold row (the 'above' side).
        if obs.close_date is None:
            return None
        if not any(alias in obs.event_title.lower() for alias in aliases):
            return None
        if not is_above(obs.outcome):
            return None
        strike = parse_strike(obs.outcome) or parse_strike(obs.event_title)
        if strike is None:
            return None
        return ThresholdPoint(
            venue=obs.venue, underlying=underlying, strike=strike,
            year=obs.close_date.year, month=obs.close_date.month,
            prob_above=obs.probability, close_date=obs.close_date,
        )

    def parse(obs: MarketObservation) -> ThresholdPoint | None:
        if obs.venue == derivative_venue:
            return _deribit_point(obs)
        if obs.venue in prediction_venues:
            return _prediction_point(obs)
        return None

    return parse
