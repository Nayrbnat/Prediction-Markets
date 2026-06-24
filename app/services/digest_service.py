"""Digest assembly: read movers + tracked markets, build a MarketDigest.

Named steps (SLAP): fetch_movers → fetch_tracked → group_tracked → assemble.
Pure logic (grouping/sorting) is kept inside this module; all I/O goes through
the repository abstraction.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone

from app.analysis.probability import q6
from app.config import Settings
from app.core.logging import get_logger
from app.markets.btc_price.divergence import compare as compare_btc_thresholds
from app.markets.eth_price.divergence import compare as compare_eth_thresholds
from app.markets.fed_rates.divergence import compare as compare_divergences
from app.markets.fed_rates.divergence import cut_hold_raise
from app.models.digest import (
    MarketDigest,
    MeetingMatrix,
    OutcomeProb,
    SourceProbs,
    TrackedMarket,
)
from app.models.domain import MarketObservation
from app.persistence.repository import MarketRepository

logger = get_logger(__name__)

# Display label + sort order per venue for the cut/hold/raise matrix.
_SOURCE_LABEL = {"polymarket": "Polymarket", "kalshi": "Kalshi", "cme": "Futures"}
_SOURCE_ORDER = {"polymarket": 0, "kalshi": 1, "cme": 2}


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


def _build_meeting_matrices(
    observations: list[MarketObservation],
) -> tuple[list[MeetingMatrix], set[tuple[str, str]]]:
    """Group tracked obs into per-meeting cut/hold/raise rows, one row per source.

    Returns (matrices, mapped_keys). ``mapped_keys`` are the (venue, market_key) markets
    that fit the Fed cut/hold/raise schema — the caller routes the rest to the generic
    "other tracked" list. Markets with no close_date or non-Fed outcomes are not mapped.
    """
    by_market: dict[tuple[str, str], dict] = {}
    for obs in observations:
        key = (obs.venue, obs.market_key)
        entry = by_market.setdefault(
            key, {"pairs": [], "close_date": obs.close_date, "venue": obs.venue}
        )
        entry["pairs"].append((obs.outcome, obs.probability))

    meetings: dict[tuple[int, int], MeetingMatrix] = {}
    seen_source: dict[tuple[int, int], set[str]] = {}
    mapped: set[tuple[str, str]] = set()
    for (venue, market_key), entry in by_market.items():
        collapsed = cut_hold_raise(entry["pairs"])
        close_date = entry["close_date"]
        if collapsed is None or close_date is None:
            continue  # not Fed cut/hold/raise, or unplaceable -> generic tracked list
        mkey = (close_date.year, close_date.month)
        if venue in seen_source.setdefault(mkey, set()):
            continue  # one row per source per meeting
        seen_source[mkey].add(venue)
        mapped.add((venue, market_key))

        cut, hold, hike = collapsed
        matrix = meetings.get(mkey)
        if matrix is None:
            matrix = MeetingMatrix(
                meeting=f"{calendar.month_abbr[close_date.month]} {close_date.year}",
                close_date=close_date,
                rows=[],
            )
            meetings[mkey] = matrix
        matrix.rows.append(
            SourceProbs(
                source=_SOURCE_LABEL.get(venue, venue),
                venue=venue,
                cut=q6(cut),
                hold=q6(hold),
                raise_=q6(hike),
            )
        )

    ordered = sorted(meetings.values(), key=lambda m: m.close_date or datetime.max)
    for matrix in ordered:
        matrix.rows.sort(key=lambda r: _SOURCE_ORDER.get(r.venue, 99))
    return ordered, mapped


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

    # Step 3: cut/hold/raise matrix per FOMC meeting (Polymarket vs Kalshi vs Futures).
    matrices, mapped_keys = _build_meeting_matrices(tracked_obs)
    # Anything not in the Fed cut/hold/raise schema falls back to the generic list.
    tracked = _group_tracked(
        [o for o in tracked_obs if (o.venue, o.market_key) not in mapped_keys]
    )
    logger.info(
        "digest.matrices",
        extra={"meetings": len(matrices), "other_tracked": len(tracked)},
    )

    # Step 4: relative value — market vs Fed-funds-futures-implied, same meeting.
    # Reuses the tracked observations (which include the `cme` venue) — no extra read.
    divergences = compare_divergences(tracked_obs, gap_threshold=settings.rv_gap_threshold)
    material = sum(1 for d in divergences if d.material)
    logger.info(
        "digest.divergences", extra={"total": len(divergences), "material": material}
    )

    # Step 4b: threshold relative value — prediction market vs options-implied P(above).
    threshold_divs = [
        *compare_btc_thresholds(tracked_obs, gap_threshold=settings.crypto_gap_threshold),
        *compare_eth_thresholds(tracked_obs, gap_threshold=settings.crypto_gap_threshold),
    ]
    threshold_material = sum(1 for t in threshold_divs if t.material)
    logger.info(
        "digest.threshold_divergences",
        extra={"total": len(threshold_divs), "material": threshold_material},
    )

    # Step 5: assemble
    digest = MarketDigest(
        generated_for=generated_for,
        mover_threshold=settings.mover_threshold,
        movers=movers,
        meeting_matrices=matrices,
        tracked=tracked,
        divergences=divergences,
        threshold_divergences=threshold_divs,
        mover_count=len(movers),
        tracked_count=len(tracked),
        divergence_count=material,
        threshold_divergence_count=threshold_material,
    )
    logger.info(
        "digest.assembled",
        extra={
            "mover_count": digest.mover_count,
            "meeting_count": len(matrices),
            "tracked_count": digest.tracked_count,
            "divergence_count": digest.divergence_count,
            "generated_for": str(generated_for),
        },
    )
    return digest
