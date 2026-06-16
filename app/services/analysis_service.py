"""The /analyze composition root: serve from the store when fresh, else do a
bounded live top-up. Owns the graceful-degradation branches.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.config import Settings
from app.core.logging import get_logger
from app.models.domain import EventDistribution, MarketObservation, MarketRef
from app.models.provenance import Venue
from app.models.requests import AnalyzeRequest
from app.models.responses import TopicAnalysis, VenueAvailability
from app.persistence.repository import MarketRepository
from app.services import discovery_service, pricing
from app.services.gateway import Gateway

logger = get_logger(__name__)


def _is_fresh(observations: list[MarketObservation], ttl_seconds: int) -> bool:
    stamps = [o.updated_at for o in observations if o.updated_at is not None]
    if not stamps:
        return False
    age = (datetime.now(timezone.utc) - max(stamps)).total_seconds()
    return age <= ttl_seconds


def _group_by_market(
    observations: list[MarketObservation],
) -> dict[tuple[str, str], list[MarketObservation]]:
    groups: dict[tuple[str, str], list[MarketObservation]] = {}
    for o in observations:
        groups.setdefault((o.venue, o.market_key), []).append(o)
    return groups


def _from_store(topic: str, observations: list[MarketObservation], *, stale: bool) -> TopicAnalysis:
    distributions: list[EventDistribution] = []
    markets: list[MarketRef] = []
    matched_venues: set[str] = set()
    for group in _group_by_market(observations).values():
        distributions.append(pricing.distribution_from_observations(group))
        markets.append(pricing.ref_from_observations(group))
        matched_venues.add(group[0].venue)

    availability = [
        VenueAvailability(
            venue=v, matched=v in matched_venues, signals=["price", "volume", "depth"]
        )
        for v in ("polymarket", "kalshi")
    ]
    notes = ["served from store without a live refresh"] if stale else []
    return TopicAnalysis(
        topic=topic,
        stale=stale,
        markets=markets,
        distributions=distributions,
        venue_availability=availability,
        notes=notes,
    )


async def _live(
    topic: str,
    *,
    gateway: Gateway,
    settings: Settings,
    venues: list[Venue] | None,
    limit: int,
) -> tuple[list[MarketRef], list[EventDistribution], list[VenueAvailability]]:
    refs, availability = await discovery_service.discover(
        gateway, topic, venues=venues, limit=limit
    )
    distributions: list[EventDistribution] = []
    for ref in refs:
        dist = await pricing.build_distribution(gateway, ref, settings)
        if dist is not None:
            distributions.append(dist)
    return refs, distributions, availability


async def analyze(
    request: AnalyzeRequest,
    *,
    repo: MarketRepository | None,
    gateway: Gateway,
    settings: Settings,
) -> TopicAnalysis:
    topic = request.topic
    limit = request.limit or settings.per_topic_limit

    stored = await repo.read_topic(topic) if repo is not None else []
    if stored and _is_fresh(stored, settings.live_ttl_seconds):
        logger.info("analyze.served_from_store", extra={"topic": topic, "rows": len(stored)})
        return _from_store(topic, stored, stale=False)

    refs, distributions, availability = await _live(
        topic, gateway=gateway, settings=settings, venues=request.venues, limit=limit
    )

    # Degradation: live found nothing but we have stored data -> serve it as stale.
    if not distributions and stored:
        logger.warning("analyze.live_empty_served_stale", extra={"topic": topic})
        return _from_store(topic, stored, stale=True)

    notes = [a.note for a in availability if a.note]
    if not distributions:
        notes.append("no live markets matched this topic")
    return TopicAnalysis(
        topic=topic,
        stale=False,
        markets=refs,
        distributions=distributions,
        venue_availability=availability,
        notes=notes,
    )
