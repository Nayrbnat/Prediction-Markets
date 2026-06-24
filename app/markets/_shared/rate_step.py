"""Pure math: an average-overnight-rate futures price -> a central-bank meeting
distribution over 25bps buckets. This is the "FedWatch" calculation, generalised.

A futures contract for calendar month *M* on an overnight rate (EFFR for CME ZQ,
SOFR for SR1, €STR for the ECB equivalent) settles to ``100 - average_daily_rate(M)``,
so ``implied_avg(M) = 100 - price(M)``. A policy meeting splits its month into days at
the current rate and days at the post-meeting rate; we solve for the post-meeting rate
and convert the implied change into a distribution over 25bps buckets.

Everything here is a deterministic pure function — no I/O. A market's source layer
(e.g. ``app/markets/fed_rates/source.py``) fetches the prices and current rate and
feeds them in.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.analysis.probability import q6

_STEP = Decimal("0.25")  # assumed uniform policy move size (25bps)
_PAR = Decimal("100")

# Integer move level (in 25bps steps; +hike / -cut) -> human-readable bucket label.
# Levels are clamped to [-2, +2]; anything larger collapses into the "50+ bps" bucket.
_LEVEL_LABELS: dict[int, str] = {
    -2: "50+ bps cut",
    -1: "25 bps cut",
    0: "No change",
    1: "25 bps hike",
    2: "50+ bps hike",
}
# Canonical output order (used for the emitted distribution).
_ORDER: list[int] = [-2, -1, 0, 1, 2]


@dataclass(frozen=True)
class RateStepResult:
    """The futures-implied meeting outcome distribution + the inputs that produced it."""

    outcomes: list[str]
    probabilities: list[Decimal]  # aligned to outcomes, sum to ~1
    r_start: Decimal  # current rate used, %
    r_end: Decimal  # implied post-meeting rate, %
    delta_bps: Decimal  # (r_end - r_start) in basis points, signed
    method: str  # "single_contract" | "two_contract"


def implied_average_rate(price: Decimal) -> Decimal:
    """Futures price -> implied average daily overnight rate for the month (= 100 - price)."""
    return _PAR - price


def _post_meeting_rate_single(
    *, implied_avg: Decimal, r_start: Decimal, n_before: int, n_after: int
) -> Decimal:
    """Solve the within-month blend for the post-meeting rate.

    implied_avg = (n_before/D)*r_start + (n_after/D)*r_end, with D = n_before + n_after.
    => r_end = (implied_avg*D - r_start*n_before) / n_after
    """
    if n_after <= 0:
        raise ValueError("n_after must be > 0 for the single-contract method")
    days = Decimal(n_before + n_after)
    return (implied_avg * days - r_start * Decimal(n_before)) / Decimal(n_after)


def distribution_from_delta(r_start: Decimal, r_end: Decimal) -> list[tuple[str, Decimal]]:
    """Convert an implied rate change into a 25bps-bucket distribution.

    Uses the standard two-point interpolation: the implied number of 25bps moves
    ``k = (r_end - r_start)/0.25`` is split between the two bracketing integer levels
    (e.g. k=0.32 -> No change 0.68, +25bps 0.32). k is clamped to [-2, +2].
    Returns (label, probability) pairs in canonical order, sum ~1.
    """
    k = (r_end - r_start) / _STEP
    # Clamp so extreme moves land in the 50+ buckets rather than overflow the labels.
    if k < Decimal(-2):
        k = Decimal(-2)
    elif k > Decimal(2):
        k = Decimal(2)

    lo = math.floor(k)
    hi = math.ceil(k)
    weights: dict[int, Decimal] = {lvl: Decimal(0) for lvl in _ORDER}
    if lo == hi:
        weights[lo] = Decimal(1)
    else:
        weights[hi] = q6(k - Decimal(lo))
        weights[lo] = q6(Decimal(hi) - k)

    return [(_LEVEL_LABELS[lvl], weights[lvl]) for lvl in _ORDER]


def rate_step_distribution(
    *,
    meeting_date: date,
    price_meeting_month: Decimal,
    r_start: Decimal,
    price_next_month: Decimal | None = None,
    next_month_has_meeting: bool = False,
    min_days_after: int = 5,
) -> RateStepResult:
    """Compute the futures-implied distribution for one policy meeting.

    Day split: the decision is announced on ``meeting_date`` and the new rate takes
    effect the next day, so days 1..meeting_date.day sit at ``r_start`` (n_before) and
    the remainder of the month at the post-meeting rate (n_after).

    Method selection:
    - **single_contract** (default): solve the within-month blend from the meeting
      month's futures price.
    - **two_contract**: when the meeting is near month-end (``n_after < min_days_after``)
      the blend is noisy; if the *next* month has no meeting, its futures price's implied
      average IS the post-meeting rate, so use it directly. Requires ``price_next_month``.
    """
    days_in_month = calendar.monthrange(meeting_date.year, meeting_date.month)[1]
    n_before = meeting_date.day
    n_after = days_in_month - n_before

    implied_avg_m = implied_average_rate(price_meeting_month)

    use_two_contract = (
        n_after < min_days_after
        and price_next_month is not None
        and not next_month_has_meeting
    )

    if use_two_contract:
        assert price_next_month is not None  # narrowed by use_two_contract
        r_end = implied_average_rate(price_next_month)
        method = "two_contract"
    else:
        if n_after <= 0:
            raise ValueError(
                "meeting falls on the last day of the month; a next-month contract "
                "(price_next_month, no next-month meeting) is required"
            )
        r_end = _post_meeting_rate_single(
            implied_avg=implied_avg_m, r_start=r_start, n_before=n_before, n_after=n_after
        )
        method = "single_contract"

    pairs = distribution_from_delta(r_start, r_end)
    outcomes = [label for label, _ in pairs]
    probabilities = [p for _, p in pairs]
    delta_bps = q6((r_end - r_start) * Decimal(100))

    return RateStepResult(
        outcomes=outcomes,
        probabilities=probabilities,
        r_start=q6(r_start),
        r_end=q6(r_end),
        delta_bps=delta_bps,
        method=method,
    )
