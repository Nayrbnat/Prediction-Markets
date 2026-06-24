"""Pure options-implied probability helpers (Breeden-Litzenberger / digital).

Given European call prices ``C(K)`` across strikes for ONE expiry, the risk-neutral
probability that the underlying finishes above a strike is the (undiscounted) digital
call value::

    P(S_T > K) = -dC/dK

estimated from the vertical call spread between the two listed strikes bracketing the
target: ``-dC/dK ≈ (C(K_lo) - C(K_hi)) / (K_hi - K_lo)``.

Discounting is ignored (``r ≈ 0`` for crypto forwards; the forward already equals the
option's ``underlying_price``) — a documented v1 simplification. The result conflates
the risk-neutral measure with the real-world one: it is decision-support, NOT a
real-world probability. Callers carry that caveat.

Everything here is a deterministic pure function — no I/O.
"""

from __future__ import annotations

from decimal import Decimal

from app.analysis.probability import q6


def _clamp01(value: Decimal) -> Decimal:
    if value < 0:
        return Decimal(0)
    if value > 1:
        return Decimal(1)
    return value


def prob_above(calls: list[tuple[Decimal, Decimal]], target: Decimal) -> Decimal | None:
    """Risk-neutral ``P(S_T > target)`` from call prices via the vertical-spread digital.

    ``calls`` is ``(strike, call_price_usd)`` points for one expiry (order irrelevant).
    Returns ``None`` when the target cannot be bracketed by listed strikes (outside the
    range, or fewer than two usable strikes) — never fabricates a probability.
    """
    pts = sorted(
        ((Decimal(k), Decimal(c)) for k, c in calls), key=lambda p: p[0]
    )
    if len(pts) < 2:
        return None
    if target < pts[0][0] or target > pts[-1][0]:
        return None

    for (k1, c1), (k2, c2) in zip(pts, pts[1:], strict=False):
        if k1 <= target <= k2 and k2 > k1:
            slope = (c2 - c1) / (k2 - k1)  # dC/dK, expected to be in [-1, 0]
            return q6(_clamp01(-slope))
    return None
