"""The /analyze read path: serve exclusively from the ingested store.

Live external fetches only happen during the ingestion cron/CLI run
(``app/services/ingestion_service.py``). The read path never calls the gateway.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.domain import EventDistribution, MarketObservation, MarketRef
from app.models.requests import AnalyzeRequest
from app.models.responses import TopicAnalysis, VenueAvailability
from app.persistence.repository import MarketRepository
from app.services import pricing

logger = get_logger(__name__)


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


async def analyze(
    request: AnalyzeRequest,
    *,
    repo: MarketRepository | None,
) -> TopicAnalysis:
    """Serve the topic analysis from the ingested store.

    If the repo is unavailable or has no rows for this topic, return an empty
    TopicAnalysis with a clear note — never call the external gateway.
    """
    topic = request.topic

    stored = await repo.read_topic(topic) if repo is not None else []
    if not stored:
        logger.info("analyze.no_data", extra={"topic": topic})
        availability = [
            VenueAvailability(venue=v, matched=False, signals=[])
            for v in ("polymarket", "kalshi")
        ]
        return TopicAnalysis(
            topic=topic,
            stale=False,
            markets=[],
            distributions=[],
            venue_availability=availability,
            notes=["no ingested data for topic"],
        )

    logger.info("analyze.served_from_store", extra={"topic": topic, "rows": len(stored)})
    return _from_store(topic, stored, stale=True)
