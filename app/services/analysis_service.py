"""The /analyze read path: serve exclusively from the ingested store.

Live external fetches only happen during the ingestion cron/CLI run
(``app/services/ingestion_service.py``). The read path never calls the gateway.
"""

from __future__ import annotations

from datetime import date

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


def _from_store(
    topic: str,
    observations: list[MarketObservation],
    *,
    stale: bool,
    as_of: date | None = None,
) -> TopicAnalysis:
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
    notes: list[str] = []
    if stale:
        notes.append("served from store without a live refresh")
    if as_of is not None:
        notes.append(f"as of {as_of}")
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
    as_of: date | None = request.as_of

    stored = await repo.read_topic(topic, as_of=as_of) if repo is not None else []
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

    # as_of queries are not stale (you asked for a specific date)
    stale = as_of is None
    logger.info(
        "analyze.served_from_store",
        extra={"topic": topic, "rows": len(stored), "as_of": str(as_of)},
    )
    return _from_store(topic, stored, stale=stale, as_of=as_of)
