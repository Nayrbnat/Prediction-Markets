"""Relative value: prediction-market probability vs Fed-funds-futures-implied probability.

Pure functions. Given the tracked observations (which include the ``cme`` futures-implied
distribution alongside the prediction-market venues), align outcomes onto a canonical Fed
outcome set, match markets to the same FOMC meeting (by close-date month), and emit the
signed per-outcome gap ``market − futures``.

This conflates genuine mispricing with the (small, at the front end) rate risk premium —
it is decision-support, NOT arbitrage. The caller labels it accordingly.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from app.analysis.changes import probability_change
from app.models.digest import DivergenceItem
from app.models.domain import MarketObservation

_FUTURES_VENUE = "cme"

# Timezone-aware sentinel so markets with no close_date sort last without mixing
# naive/aware datetimes during comparison.
_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)

# Canonical Fed outcome buckets (also the CME source's own labels).
# Map each venue's raw outcome label (normalised) onto one of these; unmapped -> dropped.
_CANONICAL: dict[str, str] = {
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


def canonical_outcome(label: str) -> str | None:
    """Map a venue's raw outcome label onto the canonical bucket, or None if unknown."""
    return _CANONICAL.get(" ".join(label.lower().split()))


@dataclass
class _Market:
    venue: str
    close_date: datetime | None = None
    probs: dict[str, Decimal] = field(default_factory=dict)  # canonical_outcome -> probability


def _meeting_key(close_date: datetime | None) -> tuple[int, int] | None:
    """Group markets onto the same FOMC meeting by close-date (year, month)."""
    return (close_date.year, close_date.month) if close_date is not None else None


def _group_markets(observations: list[MarketObservation]) -> dict[tuple[str, str], _Market]:
    """Collapse observations into one _Market per (venue, market_key) with canonical probs."""
    markets: dict[tuple[str, str], _Market] = {}
    for obs in observations:
        canon = canonical_outcome(obs.outcome)
        if canon is None:
            continue
        key = (obs.venue, obs.market_key)
        m = markets.get(key)
        if m is None:
            m = _Market(venue=obs.venue, close_date=obs.close_date)
            markets[key] = m
        m.probs[canon] = obs.probability
    return markets


def compare(
    observations: list[MarketObservation], *, gap_threshold: Decimal
) -> list[DivergenceItem]:
    """Emit signed market−futures gaps per (meeting, prediction-venue, canonical outcome).

    Only meetings that have BOTH a futures (``cme``) distribution and at least one
    prediction-market distribution produce items. Sorted by meeting, then |gap| desc.
    """
    markets = _group_markets(observations)

    # Bucket markets by FOMC meeting (year, month).
    by_meeting: dict[tuple[int, int], list[_Market]] = defaultdict(list)
    for m in markets.values():
        mk = _meeting_key(m.close_date)
        if mk is not None:
            by_meeting[mk].append(m)

    items: list[DivergenceItem] = []
    for (year, month), mkts in by_meeting.items():
        futures = next((m for m in mkts if m.venue == _FUTURES_VENUE), None)
        if futures is None:
            continue  # no futures-implied distribution for this meeting -> nothing to compare
        meeting_label = f"{calendar.month_name[month]} {year}"
        for m in mkts:
            if m.venue == _FUTURES_VENUE:
                continue
            for canon, market_prob in m.probs.items():
                fut_prob = futures.probs.get(canon)
                if fut_prob is None:
                    continue
                change = probability_change(
                    fut_prob, market_prob, material_threshold=gap_threshold
                )
                items.append(
                    DivergenceItem(
                        meeting=meeting_label,
                        market_venue=m.venue,
                        outcome=canon,
                        market_prob=market_prob,
                        futures_prob=fut_prob,
                        gap=change.delta,
                        material=change.material,
                        close_date=m.close_date,
                    )
                )

    # Meeting ascending, then largest |gap| first (biggest signal per meeting on top).
    items.sort(key=lambda it: (it.close_date or _FAR_FUTURE, -abs(it.gap)))
    return items
