"""Build distributions from market refs (live) and from stored observations, and
convert distributions into persistable observations. The bridge between the
sources/analysis layers and the persistence/response layers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.analysis.distribution import normalise_distribution
from app.analysis.probability import implied_probability, probability_from_price
from app.config import Settings
from app.models.domain import (
    EventDistribution,
    MarketObservation,
    MarketRef,
    OutcomeProbability,
)
from app.models.provenance import ConfidenceFlag, Provenance
from app.services.gateway import Gateway


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def build_distribution(
    gateway: Gateway, ref: MarketRef, settings: Settings
) -> EventDistribution | None:
    """Live: derive each outcome's probability (CLOB book where available, else the
    discovery quick-read) and normalise the event. Returns None if no price."""
    probs: list[OutcomeProbability] = []
    for i, outcome in enumerate(ref.outcomes):
        op: OutcomeProbability | None = None
        if ref.venue == "polymarket" and ref.enable_order_book and i < len(ref.token_ids):
            book = await gateway.order_book(ref.token_ids[i])
            if book is not None and book.mid is not None:
                op = implied_probability(
                    outcome=outcome,
                    book=book,
                    provenance=Provenance(
                        venue=ref.venue, endpoint="/book",
                        raw_value=str(book.mid), observed_at=book.observed_at,
                    ),
                    thin_spread=settings.thin_spread,
                    thin_volume=settings.thin_volume,
                    volume=ref.volume,
                )
        if op is None:
            price = (
                ref.quoted_prices[i]
                if ref.quoted_prices and i < len(ref.quoted_prices)
                else None
            )
            if price is None:
                continue
            op = probability_from_price(
                outcome=outcome,
                price=price,
                provenance=Provenance(
                    venue=ref.venue, endpoint="discovery",
                    raw_value=str(price), observed_at=_now(),
                ),
                thin_spread=settings.thin_spread,
                thin_volume=settings.thin_volume,
                volume=ref.volume,
            )
        probs.append(op)

    if not probs:
        return None
    return normalise_distribution(
        venue=ref.venue,
        event_title=ref.event_title,
        market_key=ref.market_key,
        outcomes=probs,
    )


def observations_from_distribution(
    dist: EventDistribution,
    ref: MarketRef,
    *,
    priority: str,
    tracked: bool,
    category: str | None,
) -> list[MarketObservation]:
    return [
        MarketObservation(
            venue=dist.venue,
            market_key=dist.market_key,
            outcome=op.outcome,
            event_title=dist.event_title,
            topic=ref.topic,
            category=category,
            probability=op.probability,
            raw_price=op.raw_price,
            volume=ref.volume,
            liquidity=ref.liquidity,
            confidence=op.confidence.level,
            priority=priority,
            tracked=tracked,
        )
        for op in dist.outcomes
    ]


def distribution_from_observations(obs: list[MarketObservation]) -> EventDistribution:
    """Reconstruct a distribution from stored (already-normalised) observations."""
    probs = [
        OutcomeProbability(
            outcome=o.outcome,
            probability=o.probability,
            raw_price=o.raw_price,
            provenance=Provenance(
                venue=o.venue, endpoint="store",
                raw_value=str(o.raw_price), observed_at=o.updated_at or _now(),
            ),
            confidence=ConfidenceFlag(level=o.confidence),
        )
        for o in obs
    ]
    raw_sum = sum((p.probability for p in probs), Decimal(0))
    return EventDistribution(
        venue=obs[0].venue,
        event_title=obs[0].event_title,
        market_key=obs[0].market_key,
        outcomes=probs,
        raw_sum=raw_sum,
        factor=raw_sum,
        normalised=True,
    )


def ref_from_observations(obs: list[MarketObservation]) -> MarketRef:
    first = obs[0]
    return MarketRef(
        venue=first.venue,
        event_id=first.market_key,
        market_key=first.market_key,
        event_title=first.event_title,
        outcomes=[o.outcome for o in obs],
        topic=first.topic,
        category=first.category,
        volume=first.volume,
        liquidity=first.liquidity,
    )
