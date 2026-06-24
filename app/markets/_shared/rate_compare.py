"""Relative value for rate-step markets: prediction-market probability vs the
futures-implied probability for the SAME central-bank meeting.

Pure, venue-agnostic. A concrete rate market (Fed, ECB, ...) supplies its own
label->bucket table (via ``make_canonical``) and its derivative venue string; the
comparison mechanism here is shared. Outcome buckets are the standard 25bps schema
emitted by ``rate_step`` (``50+ bps cut`` ... ``50+ bps hike``).

This conflates genuine mispricing with the (small, at the front end) rate risk
premium — it is decision-support, NOT arbitrage. Callers label it accordingly.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from app.analysis.changes import probability_change
from app.models.digest import DivergenceItem
from app.models.domain import MarketObservation

# Canonical 25bps buckets (also the rate-step source's own labels).
CUT_BUCKETS = frozenset({"50+ bps cut", "25 bps cut"})
HIKE_BUCKETS = frozenset({"25 bps hike", "50+ bps hike"})
HOLD = "No change"

# Timezone-aware sentinel so markets with no close_date sort last without mixing
# naive/aware datetimes during comparison.
_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)

Canonical = Callable[[str], str | None]


def make_canonical(table: dict[str, str]) -> Canonical:
    """Build a label->canonical-bucket mapper from a (normalised) lookup table.

    Lookups are whitespace- and case-normalised; unknown labels map to None (dropped).
    """

    def canonical(label: str) -> str | None:
        return table.get(" ".join(label.lower().split()))

    return canonical


def cut_hold_raise(
    pairs: list[tuple[str, Decimal]], *, canonical: Canonical
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Collapse a market's (outcome, probability) pairs into (cut, hold, raise) sums.

    Cut = all cut sizes; Hold = no change; Raise = all hike sizes. Returns None when
    none of the outcomes map to the rate schema (e.g. a 'number of dissents' market).
    """
    cut = hold = hike = Decimal(0)
    mapped = False
    for outcome, prob in pairs:
        canon = canonical(outcome)
        if canon is None:
            continue
        mapped = True
        if canon in CUT_BUCKETS:
            cut += prob
        elif canon in HIKE_BUCKETS:
            hike += prob
        else:  # HOLD
            hold += prob
    return (cut, hold, hike) if mapped else None


@dataclass
class _Market:
    venue: str
    close_date: datetime | None = None
    probs: dict[str, Decimal] = field(default_factory=dict)  # canonical_outcome -> probability


def _meeting_key(close_date: datetime | None) -> tuple[int, int] | None:
    """Group markets onto the same meeting by close-date (year, month)."""
    return (close_date.year, close_date.month) if close_date is not None else None


def _group_markets(
    observations: list[MarketObservation], canonical: Canonical
) -> dict[tuple[str, str], _Market]:
    """Collapse observations into one _Market per (venue, market_key) with canonical probs."""
    markets: dict[tuple[str, str], _Market] = {}
    for obs in observations:
        canon = canonical(obs.outcome)
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
    observations: list[MarketObservation],
    *,
    canonical: Canonical,
    derivative_venue: str,
    gap_threshold: Decimal,
) -> list[DivergenceItem]:
    """Emit signed market−futures gaps per (meeting, prediction-venue, canonical outcome).

    Only meetings that have BOTH a derivative (``derivative_venue``) distribution and at
    least one prediction-market distribution produce items. Sorted by meeting, then
    |gap| desc.
    """
    markets = _group_markets(observations, canonical)

    by_meeting: dict[tuple[int, int], list[_Market]] = defaultdict(list)
    for m in markets.values():
        mk = _meeting_key(m.close_date)
        if mk is not None:
            by_meeting[mk].append(m)

    items: list[DivergenceItem] = []
    for (year, month), mkts in by_meeting.items():
        futures = next((m for m in mkts if m.venue == derivative_venue), None)
        if futures is None:
            continue  # no derivative distribution for this meeting -> nothing to compare
        meeting_label = f"{calendar.month_name[month]} {year}"
        for m in mkts:
            if m.venue == derivative_venue:
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

    items.sort(key=lambda it: (it.close_date or _FAR_FUTURE, -abs(it.gap)))
    return items
