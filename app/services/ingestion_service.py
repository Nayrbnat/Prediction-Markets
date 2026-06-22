"""The cron/CLI ingestion run, as named steps:
start_run → discover → build observations → write_snapshots →
upsert_market_topics → refresh_latest → finish_run.

Append-only: each daily run inserts one snapshot per series.
Same-day re-runs are idempotent (ON CONFLICT DO UPDATE).

Also exposes ``run_daily`` which wraps ``run_ingestion`` and optionally sends
a digest email when ``settings.digest_enabled`` is true.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import Settings
from app.core.logging import get_logger
from app.models.domain import MarketObservation
from app.models.responses import RefreshResult
from app.notifications.email import EmailSender
from app.persistence.repository import MarketRepository
from app.services import pricing
from app.services.digest_render import render_digest
from app.services.digest_service import build_digest
from app.services.gateway import Gateway

logger = get_logger(__name__)


async def _ingest_topic(
    gateway: Gateway, settings: Settings, topic: str
) -> list[MarketObservation]:
    """Discover markets for a topic and build observation rows — no DB access."""
    tracked = topic in settings.high_priority
    priority = "high" if tracked else "normal"
    map_category = settings.categories.get(topic)

    refs = await gateway.discover(topic, venues=None, limit=settings.per_topic_limit)
    observations: list[MarketObservation] = []
    for ref in refs:
        dist = await pricing.build_distribution(gateway, ref, settings)
        if dist is None:
            continue
        # Curated CATEGORY_MAP is the controlled vocabulary and wins; the venue's
        # own category (clean only for Kalshi's event.category) is the fallback.
        category = map_category or ref.category
        rows = pricing.observations_from_distribution(
            dist, ref, priority=priority, tracked=tracked, category=category
        )
        observations.extend(rows)
    return observations


async def run_ingestion(
    *, repo: MarketRepository, gateway: Gateway, settings: Settings
) -> RefreshResult:
    topics = settings.topics
    snapshot_date = datetime.now(timezone.utc).date()
    logger.info(
        "ingestion.start",
        extra={"topics": len(topics), "snapshot_date": str(snapshot_date)},
    )

    run_id = await repo.start_run(snapshot_date)

    try:
        # Fan-out discovery across topics — no DB reads inside _ingest_topic.
        results = await asyncio.gather(
            *(_ingest_topic(gateway, settings, t) for t in topics),
            return_exceptions=True,
        )

        all_obs: list[MarketObservation] = []
        for topic, result in zip(topics, results, strict=False):
            if isinstance(result, BaseException):
                logger.warning(
                    "ingestion.topic_failed",
                    extra={"topic": topic, "error": str(result)},
                )
                continue
            all_obs.extend(result)

        # Bulk write snapshots (idempotent same-day).
        rows = await repo.write_snapshots(all_obs, snapshot_date, run_id)

        # Build and upsert topic-mapping pairs.
        # One pair per unique (venue, market_key, topic) — sourced from observations.
        topic_pairs: dict[tuple[str, str, str], dict] = {}
        for obs in all_obs:
            if obs.topic is None:
                continue
            key = (obs.venue, obs.market_key, obs.topic)
            existing = topic_pairs.get(key)
            if existing is None:
                topic_pairs[key] = {
                    "venue": obs.venue,
                    "market_key": obs.market_key,
                    "topic": obs.topic,
                    "category": obs.category,
                    "priority": obs.priority,
                    "tracked": obs.tracked,
                    "event_title": obs.event_title,
                }
            else:
                # Escalate flags if same pair appears twice (duplicate topics in obs).
                if obs.priority == "high" or existing["priority"] == "high":
                    existing["priority"] = "high"
                existing["tracked"] = existing["tracked"] or obs.tracked

        await repo.upsert_market_topics(list(topic_pairs.values()))
        await repo.refresh_latest()
        await repo.finish_run(run_id, "ok", len(topics), rows)

    except Exception as exc:
        logger.error("ingestion.failed", extra={"error": str(exc), "run_id": run_id})
        try:
            await repo.finish_run(run_id, "failed", len(topics), 0)
        except Exception:  # noqa: BLE001
            pass
        raise

    logger.info("ingestion.done", extra={"markets": len(all_obs), "rows_written": rows})
    return RefreshResult(markets=rows, changes=0, purged=0)


async def run_daily(
    *,
    repo: MarketRepository,
    gateway: Gateway,
    sender: EmailSender,
    settings: Settings,
) -> RefreshResult:
    """Ingest, then (if digest_enabled) build and send the daily digest email.

    Steps: ingest → build_digest → render_digest → send.
    Returns the RefreshResult from the ingestion run.
    """
    # Step 1: ingest
    result = await run_ingestion(repo=repo, gateway=gateway, settings=settings)

    # Step 2: digest (only when enabled)
    if not settings.digest_enabled:
        logger.info("daily.digest_skipped", extra={"reason": "digest_enabled=false"})
        return result

    digest = await build_digest(repo, settings)
    subject, html, text = render_digest(digest)

    recipients = settings.digest_recipients
    if not recipients:
        logger.warning(
            "daily.digest_no_recipients",
            extra={"reason": "digest_to is empty; skipping send"},
        )
        return result

    await sender.send(subject=subject, html=html, text=text, to=recipients)
    logger.info(
        "daily.digest_sent",
        extra={"mover_count": digest.mover_count, "recipients": len(recipients)},
    )
    return result
