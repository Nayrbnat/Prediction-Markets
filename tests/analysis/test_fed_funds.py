"""Hand-checked tests for the ZQ -> FOMC-probability math (FedWatch calculation).

All expected values are computed by hand in the comments so the arithmetic is auditable.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.analysis.fed_funds import (
    distribution_from_delta,
    fed_funds_distribution,
    implied_average_rate,
)

# Output order is fixed: [50+ cut, 25 cut, No change, 25 hike, 50+ hike].
CUT50, CUT25, HOLD, HIKE25, HIKE50 = 0, 1, 2, 3, 4


def _probs(result) -> dict[str, Decimal]:
    return dict(zip(result.outcomes, result.probabilities, strict=True))


def test_implied_average_rate() -> None:
    # 100 - 96.365 = 3.635 (the live July-2026 ZQ example)
    assert implied_average_rate(Decimal("96.365")) == Decimal("3.635")


def test_exact_25bps_hike_single_contract() -> None:
    # Meeting June 15 (30-day month): n_before=15, n_after=15.
    # r_start=4.33, r_end=4.58 (a clean +25bps). implied_avg = 0.5*4.33 + 0.5*4.58 = 4.455.
    # price = 100 - 4.455 = 95.545.
    r = fed_funds_distribution(
        meeting_date=date(2026, 6, 15),
        price_meeting_month=Decimal("95.545"),
        r_start=Decimal("4.33"),
    )
    assert r.method == "single_contract"
    assert r.r_end == Decimal("4.58")
    assert r.delta_bps == Decimal("25")
    p = _probs(r)
    assert p["25 bps hike"] == Decimal("1")
    assert p["No change"] == Decimal("0")


def test_partial_hike_probability_single_contract() -> None:
    # Want k=0.32 -> r_end = 4.33 + 0.32*0.25 = 4.41. implied_avg = 0.5*4.33+0.5*4.41 = 4.37.
    # price = 95.63.  Expect No change 0.68, +25bps 0.32.
    r = fed_funds_distribution(
        meeting_date=date(2026, 6, 15),
        price_meeting_month=Decimal("95.63"),
        r_start=Decimal("4.33"),
    )
    assert r.r_end == Decimal("4.41")
    assert r.delta_bps == Decimal("8")
    p = _probs(r)
    assert p["No change"] == Decimal("0.68")
    assert p["25 bps hike"] == Decimal("0.32")
    assert sum(r.probabilities) == Decimal("1")


def test_partial_cut_probability_single_contract() -> None:
    # k=-0.4 -> r_end = 4.33 - 0.10 = 4.23. implied_avg = 0.5*4.33+0.5*4.23 = 4.28. price=95.72.
    # Expect No change 0.60, 25 bps cut 0.40.
    r = fed_funds_distribution(
        meeting_date=date(2026, 6, 15),
        price_meeting_month=Decimal("95.72"),
        r_start=Decimal("4.33"),
    )
    assert r.r_end == Decimal("4.23")
    p = _probs(r)
    assert p["No change"] == Decimal("0.6")
    assert p["25 bps cut"] == Decimal("0.4")


def test_two_contract_path_for_month_end_meeting() -> None:
    # July 29 (31-day month): n_after=2 < min_days_after -> two-contract.
    # August has no meeting; August implied avg = post-meeting rate = 4.50 -> price 95.50.
    # r_start=4.33 -> delta=0.17 -> k=0.68 -> No change 0.32, +25bps 0.68.
    r = fed_funds_distribution(
        meeting_date=date(2026, 7, 29),
        price_meeting_month=Decimal("95.99"),  # ignored on the two-contract path
        r_start=Decimal("4.33"),
        price_next_month=Decimal("95.50"),
        next_month_has_meeting=False,
    )
    assert r.method == "two_contract"
    assert r.r_end == Decimal("4.5")
    p = _probs(r)
    assert p["No change"] == Decimal("0.32")
    assert p["25 bps hike"] == Decimal("0.68")


def test_month_end_meeting_falls_back_to_single_when_next_month_has_meeting() -> None:
    # Same month-end meeting, but next month DOES have a meeting -> cannot use it; single-contract.
    r = fed_funds_distribution(
        meeting_date=date(2026, 7, 29),
        price_meeting_month=Decimal("95.99"),
        r_start=Decimal("4.33"),
        price_next_month=Decimal("95.50"),
        next_month_has_meeting=True,
    )
    assert r.method == "single_contract"


def test_distribution_from_delta_clamps_large_move() -> None:
    # +60bps implied -> k=2.4, clamped to 2 -> all weight on "50+ bps hike".
    pairs = dict(distribution_from_delta(Decimal("4.00"), Decimal("4.60")))
    assert pairs["50+ bps hike"] == Decimal("1")
    assert pairs["No change"] == Decimal("0")


def test_distribution_always_sums_to_one() -> None:
    for delta in ("0.03", "-0.03", "0.12", "0.40", "-0.55"):
        pairs = distribution_from_delta(Decimal("4.00"), Decimal("4.00") + Decimal(delta))
        total = sum((p for _, p in pairs), Decimal(0))
        assert abs(total - Decimal("1")) < Decimal("0.000001"), delta
