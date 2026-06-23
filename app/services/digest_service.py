"""Digest assembly: read movers + tracked markets, build a MarketDigest.

Named steps (SLAP): fetch_movers → fetch_tracked → group_tracked → assemble.
Pure logic (grouping/sorting) is kept inside this module; all I/O goes through
the repository abstraction.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.analysis.divergence import compare as compare_divergences
from app.config import Settings
from app.core.logging import get_logger
from app.models.digest import MarketDigest, OutcomeProb, TrackedMarket
from app.models.domain import MarketObservation
from app.persistence.repository import MarketRepository

logger = get_logger(__name__)


def _group_tracked(observations: list[MarketObservation]) -> list[TrackedMarket]:
    """Group MarketObservation rows by (venue, market_key) into TrackedMarket objects.

    Outcome order is deterministic (sorted alphabetically) so the digest is stable.
    """
    groups: dict[tuple[str, str], TrackedMarket] = {}
    for obs in observations:
        key = (obs.venue, obs.market_key)
        if key not in groups:
            groups[key] = TrackedMarket(
                venue=obs.venue,
                event_title=obs.event_title,
                market_key=obs.market_key,
                outcomes=[],
            )
        groups[key].outcomes.append(
            OutcomeProb(outcome=obs.outcome, probability=obs.probability)
        )

    # Sort outcomes within each market alphabetically for stable output
    for tm in groups.values():
        tm.outcomes.sort(key=lambda o: o.outcome)

    # Return markets sorted by (venue, market_key) for determinism
    return sorted(groups.values(), key=lambda m: (m.venue, m.market_key))


async def build_digest(repo: MarketRepository, settings: Settings) -> MarketDigest:
    """Assemble the daily digest from repository reads.

    Steps:
    1. fetch_movers — tracked outcomes that moved >= mover_threshold day-over-day.
    2. fetch_tracked — current state of all tracked markets.
    3. group_tracked — group outcome rows by market.
    4. assemble — build and return the MarketDigest.
    """
    generated_for = datetime.now(timezone.utc).date()

    # Step 1: fetch movers
    movers = await repo.read_movers(settings.mover_threshold)
    logger.info("digest.movers_fetched", extra={"count": len(movers)})

    # Step 2: fetch tracked current state
    tracked_obs = await repo.read_tracked_current()
    logger.info("digest.tracked_fetched", extra={"rows": len(tracked_obs)})

    # Step 3: group into TrackedMarket objects
    tracked = _group_tracked(tracked_obs)

    # Step 4: relative value — market vs Fed-funds-futures-implied, same meeting.
    # Reuses the tracked observations (which include the `cme` venue) — no extra read.
    divergences = compare_divergences(tracked_obs, gap_threshold=settings.rv_gap_threshold)
    material = sum(1 for d in divergences if d.material)
    logger.info(
        "digest.divergences", extra={"total": len(divergences), "material": material}
    )

    # Step 5: assemble
    digest = MarketDigest(
        generated_for=generated_for,
        mover_threshold=settings.mover_threshold,
        movers=movers,
        tracked=tracked,
        divergences=divergences,
        mover_count=len(movers),
        tracked_count=len(tracked),
        divergence_count=material,
    )
    logger.info(
        "digest.assembled",
        extra={
            "mover_count": digest.mover_count,
            "tracked_count": digest.tracked_count,
            "divergence_count": digest.divergence_count,
            "generated_for": str(generated_for),
        },
    )
    return digest
