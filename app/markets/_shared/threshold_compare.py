"""Relative value for price-threshold markets: prediction-market P(above) vs the
options-implied (risk-neutral) P(above) for the SAME underlying / strike / expiry.

Pure, venue-agnostic. A concrete threshold market (BTC, ETH, ...) supplies a ``parser``
that turns one ``MarketObservation`` into a ``ThresholdPoint`` (or ``None`` to drop it) —
this is where each venue's outcome labels are interpreted. The matching + gap mechanism
here is shared.

Like the rate comparator, this conflates genuine mispricing with the risk premium (and
crypto's is not small): decision-support, NOT arbitrage. Callers label it accordingly.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.analysis.changes import probability_change
from app.models.digest import ThresholdDivergence
from app.models.domain import MarketObservation

_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ThresholdPoint:
    """One venue's P(above strike) for an underlying/strike/expiry, parsed from an obs."""

    venue: str
    underlying: str
    strike: Decimal
    year: int
    month: int
    prob_above: Decimal
    close_date: datetime | None


Parser = Callable[[MarketObservation], ThresholdPoint | None]


def _round_strike(strike: Decimal, step: Decimal) -> Decimal:
    """Snap a strike to a grid so e.g. 149,950 (PM) and 150,000 (options) match."""
    if step <= 0:
        return strike
    return (strike / step).quantize(Decimal(1)) * step


def compare(
    observations: list[MarketObservation],
    *,
    parser: Parser,
    derivative_venue: str,
    gap_threshold: Decimal,
    strike_step: Decimal = Decimal("1000"),
) -> list[ThresholdDivergence]:
    """Emit signed market−derivative P(above) gaps per (underlying, strike, expiry).

    Only keys that have BOTH a derivative point and a prediction-market point produce
    items. Sorted by expiry, then |gap| desc.
    """
    points = [p for obs in observations if (p := parser(obs)) is not None]

    by_key: dict[tuple[str, Decimal, int, int], list[ThresholdPoint]] = defaultdict(list)
    for p in points:
        key = (p.underlying, _round_strike(p.strike, strike_step), p.year, p.month)
        by_key[key].append(p)

    items: list[ThresholdDivergence] = []
    for pts in by_key.values():
        deriv = next((p for p in pts if p.venue == derivative_venue), None)
        if deriv is None:
            continue
        expiry_label = f"{calendar.month_abbr[deriv.month]} {deriv.year}"
        for p in pts:
            if p.venue == derivative_venue:
                continue
            change = probability_change(
                deriv.prob_above, p.prob_above, material_threshold=gap_threshold
            )
            items.append(
                ThresholdDivergence(
                    underlying=p.underlying,
                    strike=deriv.strike,
                    expiry=expiry_label,
                    market_venue=p.venue,
                    market_prob=p.prob_above,
                    derivative_prob=deriv.prob_above,
                    gap=change.delta,
                    material=change.material,
                    close_date=p.close_date,
                )
            )

    items.sort(key=lambda it: (it.close_date or _FAR_FUTURE, -abs(it.gap)))
    return items
