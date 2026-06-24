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
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.markets._shared.threshold_compare import ThresholdPoint
from app.models.domain import MarketObservation, MarketRef

_DEFAULT_DERIVATIVE_VENUE = "deribit"
_DEFAULT_PREDICTION_VENUES = ("polymarket", "kalshi")

# Deribit expiry-token month abbreviations (verified live 2026-06-24): day has NO leading
# zero, month is 3-letter uppercase, year is 2 digits, e.g. 2026-06-26 -> "26JUN26".
_MONTHS = (
    "", "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)


def deribit_token(when: datetime) -> str:
    """A UTC datetime -> Deribit expiry token (e.g. 2026-06-26 -> '26JUN26')."""
    return f"{when.day}{_MONTHS[when.month]}{when.year % 100:02d}"

# Deribit gives TERMINAL P(S_T > K at expiry). We only compare against prediction markets
# that are likewise terminal "above strike" thresholds. A terminal-above market carries one
# of these markers ...
_ABOVE_WORDS = ("above", "≥", ">=", "or above", "at least", "greater than")
# ... and NONE of these, which signal a BARRIER/touch/running-max or a DOWNSIDE market
# (e.g. Polymarket "reach $X"/"dip to $X", Kalshi "how high this year"). Those are not
# terminal P(S_T>K), so we skip them rather than compare apples to oranges (§13).
_SKIP_WORDS = (
    "reach", "hit", "touch", "dip", "max", "fall", "drop",
    "below", "under", "less than", "how high", "or below", "↑", "↓",
)
# A strike must be $-anchored or carry a k/M suffix — avoids matching bare years (2026).
_DOLLAR = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM]?)")
_SUFFIX = re.compile(r"\b([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM])\b")
# Bare comma-grouped or >=4-digit number (e.g. Kalshi index "27,300 or above" — no $).
# Only used on short OUTCOME labels (strike-only), never on titles that contain a year.
_BARE = re.compile(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})(?:\.[0-9]+)?")


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


def parse_outcome_strike(text: str) -> Decimal | None:
    """Strike from a short OUTCOME label: $-anchored/suffixed first, else a bare number.

    Outcome labels are strike-only (e.g. "$50,000 or above", "27,300 or above"), so a bare
    number is safe here — unlike titles, which contain a year. Falls through to None.
    """
    dollar = parse_strike(text)
    if dollar is not None:
        return dollar
    m = _BARE.search(text)
    return _to_decimal(m.group(1).replace(",", "")) if m else None


def is_terminal_above(text: str) -> bool:
    """Whether free text denotes a TERMINAL 'above strike' threshold (not a barrier/touch/
    running-max/downside market). Used to keep the comparison apples-to-apples with the
    options-implied terminal P(S_T > K)."""
    low = text.lower()
    if any(word in low for word in _SKIP_WORDS):
        return False
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
        # Best-effort parse of a Polymarket/Kalshi TERMINAL above-threshold row.
        # Polymarket carries strike + direction in the (market) question now used as the
        # event_title ("...above $60,000 on June 24?"), outcome "Yes"/"No"; Kalshi carries
        # them in the outcome label ("$57,000 or above"). Direction is read from the text,
        # NOT the Yes/No outcome, so downside markets aren't misread as 'above'.
        if obs.close_date is None:
            return None
        text = f"{obs.event_title} {obs.outcome}"
        if not any(alias in text.lower() for alias in aliases):
            return None
        if obs.outcome.strip().lower() == "no":
            return None  # affirmative side only — the 'No' row is the complement
        if not is_terminal_above(text):
            return None  # skip barrier/touch/max/downside markets (§13)
        strike = parse_outcome_strike(obs.outcome) or parse_strike(obs.event_title)
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


def targets_from_refs(
    refs: list[MarketRef],
    *,
    underlying: str,
    aliases: tuple[str, ...],
    token_fn: Callable[[datetime], str] = deribit_token,
    prediction_venues: tuple[str, ...] = _DEFAULT_PREDICTION_VENUES,
) -> list[tuple[Decimal, str]]:
    """Derive ``(strike, expiry_token)`` targets from live prediction-market refs.

    The dynamic-targeting counterpart to static config targets: scan the Polymarket/Kalshi
    markets discovered this run, keep the TERMINAL above-thresholds for ``underlying`` (same
    terminal/barrier/strike rules as the comparator), and turn each into the (strike, expiry
    token) the derivative source queries. ``token_fn`` formats the close date into that
    source's token (Deribit ``26JUN26`` for crypto, CBOE ``YYMMDD`` for equities). Deduped +
    sorted. A target the derivative venue doesn't list yields no curve downstream and is
    skipped — never fabricated.
    """
    targets: set[tuple[Decimal, str]] = set()
    for ref in refs:
        if ref.venue not in prediction_venues or ref.close_date is None:
            continue
        for outcome in ref.outcomes:
            if outcome.strip().lower() == "no":
                continue  # affirmative side only
            text = f"{ref.event_title} {outcome}"
            if not any(alias in text.lower() for alias in aliases):
                continue
            if not is_terminal_above(text):
                continue
            strike = parse_outcome_strike(outcome) or parse_strike(ref.event_title)
            if strike is None:
                continue
            targets.add((strike, token_fn(ref.close_date)))
    return sorted(targets)
