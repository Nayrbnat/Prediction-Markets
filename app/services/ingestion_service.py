"""The cron/CLI ingestion run, as named steps:
discover -> flag -> analyse/normalise -> upsert -> log material moves -> purge.
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
    gateway: Gateway, repo: MarketRepository, settings: Settings, topic: str
) -> tuple[list[MarketObservation], list[MarketObservation]]:
    tracked = topic in settings.high_priority
    priority = "high" if tracked else "normal"
    category = settings.categories.get(topic)

    refs = await gateway.discover(topic, venues=None, limit=settings.per_topic_limit)
    observations: list[MarketObservation] = []
    changes: list[MarketObservation] = []

    for ref in refs:
        dist = await pricing.build_distribution(gateway, ref, settings)
        if dist is None:
            continue
        rows = pricing.observations_from_distribution(
            dist, ref, priority=priority, tracked=tracked, category=category
        )
        observations.extend(rows)

        if tracked:
            existing = {o.outcome: o for o in await repo.read_market(ref.venue, ref.market_key)}
            for row in rows:
                prev = existing.get(row.outcome)
                if prev is None:
                    continue
                change = probability_change(
                    prev.probability, row.probability,
                    material_threshold=settings.material_change,
                )
                if change.material:
                    changes.append(
                        row.model_copy(
                            update={
                                "previous_probability": change.previous,
                                "probability_delta": change.delta,
                            }
                        )
                    )
    return observations, changes


async def run_ingestion(
    *, repo: MarketRepository, gateway: Gateway, settings: Settings
) -> RefreshResult:
    topics = settings.topics
    logger.info("ingestion.start", extra={"topics": len(topics)})

    results = await asyncio.gather(
        *(_ingest_topic(gateway, repo, settings, t) for t in topics),
        return_exceptions=True,
    )

    all_obs: list[MarketObservation] = []
    all_changes: list[MarketObservation] = []
    for topic, result in zip(topics, results, strict=False):
        if isinstance(result, BaseException):
            logger.warning("ingestion.topic_failed", extra={"topic": topic, "error": str(result)})
            continue
        obs, changes = result
        all_obs.extend(obs)
        all_changes.extend(changes)

    await repo.upsert_observations(all_obs)
    await repo.append_changes(all_changes)
    purged = await repo.purge_stale(settings.retention_days)

    logger.info(
        "ingestion.done",
        extra={"markets": len(all_obs), "changes": len(all_changes), "purged": purged},
    )
    return RefreshResult(markets=len(all_obs), changes=len(all_changes), purged=purged)
