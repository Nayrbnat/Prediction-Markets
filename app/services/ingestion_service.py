"""The cron/CLI ingestion run, as named steps:
discover -> build observations -> bulk upsert -> derive changes from returned rows -> purge.

No N+1 reads: upsert_observations returns the upserted rows with previous_probability
and probability_delta already computed by the DB SET clause. The service derives
material change-log entries in Python from those returned rows.
"""

from __future__ import annotations

import asyncio

from app.analysis.changes import probability_change
from app.config import Settings
from app.core.logging import get_logger
from app.models.domain import MarketObservation
from app.models.responses import RefreshResult
from app.persistence.repository import MarketRepository
from app.services import pricing
from app.services.gateway import Gateway

logger = get_logger(__name__)


async def _ingest_topic(
    gateway: Gateway, settings: Settings, topic: str
) -> list[MarketObservation]:
    """Discover markets for a topic and build observation rows — no DB access."""
    tracked = topic in settings.high_priority
    priority = "high" if tracked else "normal"
    category = settings.categories.get(topic)

    refs = await gateway.discover(topic, venues=None, limit=settings.per_topic_limit)
    observations: list[MarketObservation] = []
    for ref in refs:
        dist = await pricing.build_distribution(gateway, ref, settings)
        if dist is None:
            continue
        rows = pricing.observations_from_distribution(
            dist, ref, priority=priority, tracked=tracked, category=category
        )
        observations.extend(rows)
    return observations


def _material_changes(
    upserted: list[MarketObservation],
    *,
    settings: Settings,
) -> list[MarketObservation]:
    """Pure: filter upserted rows to those with material probability moves.

    The DB RETURNING clause gives back previous_probability and probability_delta on
    each upserted row. Apply the same analysis/changes.probability_change threshold
    so materiality logic stays exclusively in analysis/.
    """
    result: list[MarketObservation] = []
    for row in upserted:
        if not row.tracked:
            continue
        if row.previous_probability is None:
            continue  # first observation — no move to log
        change = probability_change(
            row.previous_probability,
            row.probability,
            material_threshold=settings.material_change,
        )
        if change.material:
            result.append(row)
    return result


async def run_ingestion(
    *, repo: MarketRepository, gateway: Gateway, settings: Settings
) -> RefreshResult:
    topics = settings.topics
    logger.info("ingestion.start", extra={"topics": len(topics)})

    # Fan-out discovery across topics — no DB reads inside _ingest_topic.
    results = await asyncio.gather(
        *(_ingest_topic(gateway, settings, t) for t in topics),
        return_exceptions=True,
    )

    all_obs: list[MarketObservation] = []
    for topic, result in zip(topics, results, strict=False):
        if isinstance(result, BaseException):
            logger.warning("ingestion.topic_failed", extra={"topic": topic, "error": str(result)})
            continue
        all_obs.extend(result)

    # Single bulk upsert — returns rows with previous_probability/delta from the DB.
    upserted = await repo.upsert_observations(all_obs)

    # Derive material changes in Python — no second round-trip.
    material = _material_changes(upserted, settings=settings)

    await repo.append_changes(material)
    purged = await repo.purge_stale(settings.retention_days)

    logger.info(
        "ingestion.done",
        extra={"markets": len(all_obs), "changes": len(material), "purged": purged},
    )
    return RefreshResult(markets=len(all_obs), changes=len(material), purged=purged)
