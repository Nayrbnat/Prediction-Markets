"""Normalise sibling outcomes within one event to sum to 1.0.

Raw market probabilities rarely sum to exactly 1 (spread/fees). We keep the raw
values, the normalised values, and the factor (= raw_sum) so the transformation is
fully auditable: normalised = raw / raw_sum.
"""

from __future__ import annotations

from decimal import Decimal

from app.analysis.probability import q6
from app.models.domain import EventDistribution, OutcomeProbability
from app.models.provenance import Venue


def normalise_distribution(
    *,
    venue: Venue,
    event_title: str,
    market_key: str,
    outcomes: list[OutcomeProbability],
) -> EventDistribution:
    raw_sum = sum((o.probability for o in outcomes), Decimal(0))

    if raw_sum == 0:
        # No fabricated normalisation; surface the degenerate case honestly.
        return EventDistribution(
            venue=venue,
            event_title=event_title,
            market_key=market_key,
            outcomes=outcomes,
            raw_sum=raw_sum,
            factor=Decimal(0),
            normalised=False,
        )

    normalised: list[OutcomeProbability] = []
    for o in outcomes:
        prov = o.provenance.model_copy(update={"normalisation_factor": raw_sum})
        normalised.append(
            o.model_copy(update={"probability": q6(o.probability / raw_sum), "provenance": prov})
        )

    return EventDistribution(
        venue=venue,
        event_title=event_title,
        market_key=market_key,
        outcomes=normalised,
        raw_sum=raw_sum,
        factor=raw_sum,
        normalised=True,
    )
