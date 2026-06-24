"""Hand-checked tests for the Breeden-Litzenberger / digital helper (_shared/density)."""

from __future__ import annotations

from decimal import Decimal

from app.markets._shared.density import prob_above

# Call-price curve (strike, call_usd) for one expiry. Monotonically decreasing in strike.
CURVE = [
    (Decimal(100_000), Decimal(8_000)),
    (Decimal(110_000), Decimal(3_000)),
    (Decimal(120_000), Decimal(800)),
]


def test_prob_above_at_listed_strike() -> None:
    # Bracket [100k,110k]: slope = (3000-8000)/10000 = -0.5 -> P = 0.5.
    assert prob_above(CURVE, Decimal(110_000)) == Decimal("0.5")


def test_prob_above_between_strikes() -> None:
    # 115k -> bracket [110k,120k]: slope = (800-3000)/10000 = -0.22 -> P = 0.22.
    assert prob_above(CURVE, Decimal(115_000)) == Decimal("0.22")


def test_prob_above_uses_lower_bracket_for_interior_point() -> None:
    # 105k falls in [100k,110k] -> same slope -> 0.5.
    assert prob_above(CURVE, Decimal(105_000)) == Decimal("0.5")


def test_target_below_range_returns_none() -> None:
    assert prob_above(CURVE, Decimal(90_000)) is None


def test_target_above_range_returns_none() -> None:
    assert prob_above(CURVE, Decimal(130_000)) is None


def test_fewer_than_two_strikes_returns_none() -> None:
    assert prob_above([(Decimal(100_000), Decimal(5_000))], Decimal(100_000)) is None


def test_non_monotonic_noise_clamps_to_zero() -> None:
    # Call price rising with strike (bid/ask noise) -> positive slope -> P<0 -> clamp 0.
    noisy = [(Decimal(100_000), Decimal(1_000)), (Decimal(110_000), Decimal(1_500))]
    assert prob_above(noisy, Decimal(105_000)) == Decimal("0")
