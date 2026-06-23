"""Probability deltas and materiality — drives change-log writes."""

from __future__ import annotations

from decimal import Decimal

from app.models.domain import ProbabilityChange


def probability_change(
    previous: Decimal | None,
    current: Decimal,
    *,
    material_threshold: Decimal,
) -> ProbabilityChange:
    """Delta from previous to current; ``material`` when |delta| >= threshold.

    A first observation (no previous) has a zero delta and is never material.
    """
    if previous is None:
        return ProbabilityChange(previous=None, current=current, delta=Decimal(0), material=False)
    delta = current - previous
    return ProbabilityChange(
        previous=previous,
        current=current,
        delta=delta,
        material=abs(delta) >= material_threshold,
    )
