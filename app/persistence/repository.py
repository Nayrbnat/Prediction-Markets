"""Market repository: the contract for reading/writing the store, plus the asyncpg
implementation. Callers depend on the ABC, never on asyncpg.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from app.core.errors import PersistenceError
from app.core.logging import get_logger
from app.models.digest import MoverItem
from app.models.domain import MarketObservation
from app.models.responses import HistoryPoint

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg

logger = get_logger(__name__)


def _dedupe_observations(observations: list[MarketObservation]) -> list[MarketObservation]:
    """Collapse rows sharing a (venue, market_key, outcome) key into one.

    The append-only ``INSERT ... ON CONFLICT DO UPDATE`` (idempotent same-day overwrite)
    cannot affect the same target row twice in one command. The same market can surface
    under overlapping watchlist topics, so duplicates are reachable.

    Keep the later row's values (last-write-wins) but ESCALATE flags so tracked/high
    status is never lost:
      - ``tracked = existing.tracked or new.tracked``
      - ``priority = "high"`` if either row is high, else the new row's priority.
    Insertion order is preserved.
    """
    merged: dict[tuple[str, str, str], MarketObservation] = {}
    for obs in observations:
        key = (obs.venue, obs.market_key, obs.outcome)
        existing = merged.get(key)
        if existing is None:
            merged[key] = obs
            continue
        priority = "high" if "high" in (existing.priority, obs.priority) else obs.priority
        merged[key] = obs.model_copy(
            update={
                "tracked": existing.tracked or obs.tracked,
                "priority": priority,
            }
        )
    return list(merged.values())


class MarketRepository(ABC):
    """Storage contract — append-only snapshot model.

    Ingestion path:
        start_run → write_snapshots + upsert_market_topics → refresh_latest → finish_run.
    Read path: read_topic / read_market / search_markets / history.
    """

    @abstractmethod
    async def ping(self) -> bool: ...

    @abstractmethod
    async def start_run(self, snapshot_date: date) -> int:
        """Insert an ingestion_runs row and return the run_id."""
        ...

    @abstractmethod
    async def write_snapshots(
        self,
        observations: list[MarketObservation],
        snapshot_date: date,
        run_id: int,
    ) -> int:
        """Bulk-insert snapshots (idempotent same-day overwrite). Returns row count."""
        ...

    @abstractmethod
    async def upsert_market_topics(
        self,
        # pairs: list of {venue, market_key, topic, category, priority, tracked, event_title}
        pairs: list[dict],
    ) -> None:
        """Upsert M2M topic mappings, escalating priority/tracked on conflict."""
        ...

    @abstractmethod
    async def refresh_latest(self) -> None:
        """REFRESH MATERIALIZED VIEW market_latest (CONCURRENTLY if possible)."""
        ...

    @abstractmethod
    async def finish_run(
        self, run_id: int, status: str, topics: int, rows_written: int
    ) -> None:
        """Mark an ingestion run complete."""
        ...

    @abstractmethod
    async def read_topic(
        self, topic: str, as_of: date | None = None
    ) -> list[MarketObservation]:
        """All observations for a topic; point-in-time if as_of given."""
        ...

    @abstractmethod
    async def read_market(
        self, venue: str, market_key: str, as_of: date | None = None
    ) -> list[MarketObservation]:
        """All observations for a market; point-in-time if as_of given."""
        ...

    @abstractmethod
    async def search_markets(
        self, q: str, venue: str | None, limit: int
    ) -> list[MarketObservation]:
        """Full-text search over market_latest joined with market_topics."""
        ...

    @abstractmethod
    async def history(
        self, venue: str, market_key: str, outcome: str, limit: int = 100
    ) -> list[HistoryPoint]:
        """All snapshot dates for a series, newest first."""
        ...

    @abstractmethod
    async def purge_before(self, cutoff_date: date) -> int:
        """Delete snapshots older than cutoff_date. Returns deleted count."""
        ...

    @abstractmethod
    async def read_movers(
        self, threshold: Decimal, limit: int = 50
    ) -> list[MoverItem]:
        """Day-over-day movers for TRACKED markets only.

        For each (venue, market_key, outcome) series with at least two snapshot
        dates, compare the two most recent dates. Return those where
        |current - previous| >= threshold, sorted by |delta| DESC.
        """
        ...

    @abstractmethod
    async def read_tracked_current(self) -> list[MarketObservation]:
        """Current (latest) observations for all tracked markets.

        Equivalent to market_latest JOIN market_topics WHERE tracked=true,
        DISTINCT ON (venue, market_key, outcome) to avoid topic fan-out.
        """
        ...


# ---------------------------------------------------------------------------
# SQL constants
# ---------------------------------------------------------------------------

_INSERT_RUN = """
INSERT INTO ingestion_runs (snapshot_date) VALUES ($1) RETURNING run_id
"""

_FINISH_RUN = """
UPDATE ingestion_runs
SET finished_at = now(), status = $2, topics = $3, rows_written = $4
WHERE run_id = $1
"""

# Bulk snapshot upsert via unnest — one round-trip for any batch size.
_UPSERT_SNAPSHOTS = """
INSERT INTO market_snapshots
    (snapshot_date, venue, market_key, outcome, event_title,
     probability, raw_price, volume_24h, volume_total, liquidity,
     close_date, best_bid, best_ask, spread, last_trade_price, open_interest,
     confidence, observed_at, run_id)
SELECT * FROM unnest(
    $1::date[], $2::text[], $3::text[], $4::text[], $5::text[],
    $6::numeric[], $7::numeric[], $8::numeric[], $9::numeric[], $10::numeric[],
    $11::timestamptz[], $12::numeric[], $13::numeric[], $14::numeric[],
    $15::numeric[], $16::numeric[],
    $17::text[], $18::timestamptz[], $19::bigint[]
)
ON CONFLICT (snapshot_date, venue, market_key, outcome) DO UPDATE SET
    event_title      = EXCLUDED.event_title,
    probability      = EXCLUDED.probability,
    raw_price        = EXCLUDED.raw_price,
    volume_24h       = EXCLUDED.volume_24h,
    volume_total     = EXCLUDED.volume_total,
    liquidity        = EXCLUDED.liquidity,
    close_date       = EXCLUDED.close_date,
    best_bid         = EXCLUDED.best_bid,
    best_ask         = EXCLUDED.best_ask,
    spread           = EXCLUDED.spread,
    last_trade_price = EXCLUDED.last_trade_price,
    open_interest    = EXCLUDED.open_interest,
    confidence       = EXCLUDED.confidence,
    observed_at      = EXCLUDED.observed_at,
    ingested_at      = now(),
    run_id           = EXCLUDED.run_id
"""

# Bulk topic-mapping upsert via unnest.
_UPSERT_TOPICS = """
INSERT INTO market_topics
    (venue, market_key, topic, category, priority, tracked, event_title, updated_at)
SELECT * FROM unnest(
    $1::text[], $2::text[], $3::text[], $4::text[],
    $5::text[], $6::boolean[], $7::text[], $8::timestamptz[]
)
ON CONFLICT (venue, market_key, topic) DO UPDATE SET
    category    = COALESCE(EXCLUDED.category, market_topics.category),
    priority    = CASE WHEN market_topics.priority = 'high'
                       THEN 'high' ELSE EXCLUDED.priority END,
    tracked     = market_topics.tracked OR EXCLUDED.tracked,
    event_title = COALESCE(EXCLUDED.event_title, market_topics.event_title),
    updated_at  = now()
"""

_PURGE = """
DELETE FROM market_snapshots WHERE snapshot_date < $1
"""

# Read from market_latest JOIN market_topics for the current view.
_READ_TOPIC_LATEST = """
SELECT
    ml.venue, ml.market_key, ml.outcome, ml.event_title,
    ml.probability, ml.raw_price,
    ml.volume_24h, ml.volume_total, ml.liquidity,
    ml.close_date, ml.best_bid, ml.best_ask, ml.spread,
    ml.last_trade_price, ml.open_interest,
    ml.confidence, ml.observed_at,
    mt.topic, mt.category, mt.priority, mt.tracked
FROM market_latest ml
JOIN market_topics mt ON ml.venue = mt.venue AND ml.market_key = mt.market_key
WHERE mt.topic = $1
ORDER BY ml.event_title, ml.outcome
"""

# Point-in-time: latest snapshot on or before as_of per series, joined with topics.
_READ_TOPIC_AS_OF = """
SELECT DISTINCT ON (s.venue, s.market_key, s.outcome)
    s.venue, s.market_key, s.outcome, s.event_title,
    s.probability, s.raw_price,
    s.volume_24h, s.volume_total, s.liquidity,
    s.close_date, s.best_bid, s.best_ask, s.spread,
    s.last_trade_price, s.open_interest,
    s.confidence, s.observed_at,
    mt.topic, mt.category, mt.priority, mt.tracked
FROM market_snapshots s
JOIN market_topics mt ON s.venue = mt.venue AND s.market_key = mt.market_key
WHERE mt.topic = $1
  AND s.snapshot_date <= $2
ORDER BY s.venue, s.market_key, s.outcome, s.snapshot_date DESC
"""

# No topic join: a market under N topics would fan out to N rows per outcome,
# inflating distribution_from_observations' normalisation sum. Market detail
# doesn't need topic/category, so leave them None (one row per outcome).
_READ_MARKET_LATEST = """
SELECT
    ml.venue, ml.market_key, ml.outcome, ml.event_title,
    ml.probability, ml.raw_price,
    ml.volume_24h, ml.volume_total, ml.liquidity,
    ml.close_date, ml.best_bid, ml.best_ask, ml.spread,
    ml.last_trade_price, ml.open_interest,
    ml.confidence, ml.observed_at
FROM market_latest ml
WHERE ml.venue = $1 AND ml.market_key = $2
ORDER BY ml.outcome
"""

_READ_MARKET_AS_OF = """
SELECT DISTINCT ON (s.venue, s.market_key, s.outcome)
    s.venue, s.market_key, s.outcome, s.event_title,
    s.probability, s.raw_price,
    s.volume_24h, s.volume_total, s.liquidity,
    s.close_date, s.best_bid, s.best_ask, s.spread,
    s.last_trade_price, s.open_interest,
    s.confidence, s.observed_at,
    mt.topic, mt.category, mt.priority, mt.tracked
FROM market_snapshots s
LEFT JOIN market_topics mt ON s.venue = mt.venue AND s.market_key = mt.market_key
WHERE s.venue = $1 AND s.market_key = $2
  AND s.snapshot_date <= $3
ORDER BY s.venue, s.market_key, s.outcome, s.snapshot_date DESC
"""

_SEARCH = """
SELECT DISTINCT ON (ml.venue, ml.market_key, ml.outcome)
    ml.venue, ml.market_key, ml.outcome, ml.event_title,
    ml.probability, ml.raw_price,
    ml.volume_24h, ml.volume_total, ml.liquidity,
    ml.close_date, ml.best_bid, ml.best_ask, ml.spread,
    ml.last_trade_price, ml.open_interest,
    ml.confidence, ml.observed_at,
    mt.topic, mt.category, mt.priority, mt.tracked
FROM market_latest ml
LEFT JOIN market_topics mt ON ml.venue = mt.venue AND ml.market_key = mt.market_key
WHERE ($2::text IS NULL OR ml.venue = $2)
  AND (mt.topic ILIKE $1 OR ml.event_title ILIKE $1)
ORDER BY ml.venue, ml.market_key, ml.outcome
LIMIT $3
"""

_HISTORY = """
SELECT snapshot_date::date AS observed_at_date, probability, observed_at
FROM market_snapshots
WHERE venue = $1 AND market_key = $2 AND outcome = $3
ORDER BY snapshot_date DESC
LIMIT $4
"""

# Day-over-day movers: tracked markets only, two most recent snapshot dates per series.
# Uses LAG() window function partitioned by (venue, market_key, outcome) over
# snapshot_date order, then filters to the latest row per series.
#
# CRITICAL: market_topics is M2M (PK venue, market_key, topic). A plain JOIN would
# fan out each snapshot row once per topic, duplicating snapshot_dates within a
# partition and corrupting LAG/ROW_NUMBER (LAG could return a duplicate of the SAME
# date → delta 0 → the mover is silently dropped). DISTINCT ON cannot fix this
# because the corruption happens before the final SELECT. We filter tracked
# membership with an EXISTS predicate so each snapshot row appears exactly once.
_READ_MOVERS = """
WITH ranked AS (
    SELECT
        s.venue, s.market_key, s.outcome, s.event_title, s.close_date,
        s.snapshot_date,
        s.probability AS current_prob,
        LAG(s.probability) OVER (
            PARTITION BY s.venue, s.market_key, s.outcome
            ORDER BY s.snapshot_date
        ) AS prev_prob,
        ROW_NUMBER() OVER (
            PARTITION BY s.venue, s.market_key, s.outcome
            ORDER BY s.snapshot_date DESC
        ) AS rn
    FROM market_snapshots s
    WHERE EXISTS (
        SELECT 1 FROM market_topics mt
        WHERE mt.venue = s.venue
          AND mt.market_key = s.market_key
          AND mt.tracked = true
    )
)
SELECT
    venue, market_key, outcome, event_title, close_date,
    current_prob, prev_prob,
    (current_prob - prev_prob) AS delta
FROM ranked
WHERE rn = 1
  AND prev_prob IS NOT NULL
  AND abs(current_prob - prev_prob) >= $1
ORDER BY abs(current_prob - prev_prob) DESC
LIMIT $2
"""

# Current state for all tracked markets — DISTINCT ON to prevent topic fan-out.
_READ_TRACKED_CURRENT = """
SELECT DISTINCT ON (ml.venue, ml.market_key, ml.outcome)
    ml.venue, ml.market_key, ml.outcome, ml.event_title,
    ml.probability, ml.raw_price,
    ml.volume_24h, ml.volume_total, ml.liquidity,
    ml.close_date, ml.best_bid, ml.best_ask, ml.spread,
    ml.last_trade_price, ml.open_interest,
    ml.confidence, ml.observed_at,
    mt.topic, mt.category, mt.priority, mt.tracked
FROM market_latest ml
INNER JOIN market_topics mt
    ON ml.venue = mt.venue AND ml.market_key = mt.market_key
WHERE mt.tracked = true
ORDER BY ml.venue, ml.market_key, ml.outcome
"""


def _row_to_observation(row: asyncpg.Record) -> MarketObservation:
    """Map a query row (from market_latest or market_snapshots) to MarketObservation.

    The snapshot/latest rows don't have previous_probability or probability_delta —
    those are always None on the read path (movers view used separately).
    updated_at is populated from observed_at for compatibility with pricing helpers.
    """
    d = dict(row)
    # Normalise field names: observed_at → updated_at for pricing helper compat
    observed_at = d.pop("observed_at", None)
    # Remove fields not in MarketObservation
    d.pop("snapshot_date", None)
    d.pop("ingested_at", None)
    d.pop("run_id", None)
    return MarketObservation(
        venue=d["venue"],
        market_key=d["market_key"],
        outcome=d["outcome"],
        event_title=d["event_title"],
        topic=d.get("topic"),
        category=d.get("category"),
        probability=d["probability"],
        raw_price=d["raw_price"],
        volume_24h=d.get("volume_24h"),
        volume_total=d.get("volume_total"),
        liquidity=d.get("liquidity"),
        close_date=d.get("close_date"),
        best_bid=d.get("best_bid"),
        best_ask=d.get("best_ask"),
        spread=d.get("spread"),
        last_trade_price=d.get("last_trade_price"),
        open_interest=d.get("open_interest"),
        confidence=d.get("confidence", "ok"),
        priority=d.get("priority", "normal"),
        tracked=d.get("tracked", False),
        updated_at=observed_at,
    )


class PostgresMarketRepository(MarketRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ping(self) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def start_run(self, snapshot_date: date) -> int:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(_INSERT_RUN, snapshot_date)
            run_id = int(row["run_id"])
            logger.info(
                "repo.run_started",
                extra={"run_id": run_id, "snapshot_date": str(snapshot_date)},
            )
            return run_id
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"start_run failed: {exc}") from exc

    async def write_snapshots(
        self,
        observations: list[MarketObservation],
        snapshot_date: date,
        run_id: int,
    ) -> int:
        if not observations:
            return 0
        rows = _dedupe_observations(observations)
        now = datetime.now(timezone.utc)

        dates            = [snapshot_date   for _ in rows]
        venues           = [o.venue         for o in rows]
        market_keys      = [o.market_key    for o in rows]
        outcomes         = [o.outcome       for o in rows]
        titles           = [o.event_title   for o in rows]
        probs            = [o.probability   for o in rows]
        raw_prices       = [o.raw_price     for o in rows]
        vols_24h         = [o.volume_24h    for o in rows]
        vols_total       = [o.volume_total  for o in rows]
        liquidities      = [o.liquidity     for o in rows]
        close_dates      = [o.close_date    for o in rows]
        best_bids        = [o.best_bid      for o in rows]
        best_asks        = [o.best_ask      for o in rows]
        spreads          = [o.spread        for o in rows]
        last_trades      = [o.last_trade_price for o in rows]
        open_interests   = [o.open_interest for o in rows]
        confidences      = [o.confidence    for o in rows]
        obs_ats          = [now             for _ in rows]
        run_ids          = [run_id          for _ in rows]

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    _UPSERT_SNAPSHOTS,
                    dates, venues, market_keys, outcomes, titles,
                    probs, raw_prices, vols_24h, vols_total, liquidities,
                    close_dates, best_bids, best_asks, spreads, last_trades, open_interests,
                    confidences, obs_ats, run_ids,
                )
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"write_snapshots failed: {exc}") from exc

        logger.info(
            "repo.write_snapshots",
            extra={"rows": len(observations), "deduped": len(rows), "run_id": run_id},
        )
        return len(rows)

    async def upsert_market_topics(self, pairs: list[dict]) -> None:
        if not pairs:
            return
        now = datetime.now(timezone.utc)
        venues      = [p["venue"]       for p in pairs]
        market_keys = [p["market_key"]  for p in pairs]
        topics      = [p["topic"]       for p in pairs]
        categories  = [p.get("category") for p in pairs]
        priorities  = [p.get("priority", "normal") for p in pairs]
        trackeds    = [p.get("tracked", False)      for p in pairs]
        titles      = [p.get("event_title")         for p in pairs]
        updated_ats = [now for _ in pairs]

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    _UPSERT_TOPICS,
                    venues, market_keys, topics, categories,
                    priorities, trackeds, titles, updated_ats,
                )
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"upsert_market_topics failed: {exc}") from exc
        logger.info("repo.upsert_topics", extra={"pairs": len(pairs)})

    async def refresh_latest(self) -> None:
        try:
            async with self._pool.acquire() as conn:
                try:
                    await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY market_latest")
                    logger.info("repo.refresh_latest", extra={"concurrent": True})
                except Exception:  # noqa: BLE001 — first run: matview empty, concurrent not allowed
                    await conn.execute("REFRESH MATERIALIZED VIEW market_latest")
                    logger.info("repo.refresh_latest", extra={"concurrent": False})
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"refresh_latest failed: {exc}") from exc

    async def finish_run(
        self, run_id: int, status: str, topics: int, rows_written: int
    ) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(_FINISH_RUN, run_id, status, topics, rows_written)
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"finish_run failed: {exc}") from exc
        logger.info(
            "repo.run_finished",
            extra={"run_id": run_id, "status": status, "rows": rows_written},
        )

    async def _fetch_obs(self, query: str, *args: object) -> list[MarketObservation]:
        try:
            async with self._pool.acquire() as conn:
                records = await conn.fetch(query, *args)
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"read failed: {exc}") from exc
        return [_row_to_observation(r) for r in records]

    async def read_topic(
        self, topic: str, as_of: date | None = None
    ) -> list[MarketObservation]:
        if as_of is None:
            return await self._fetch_obs(_READ_TOPIC_LATEST, topic)
        return await self._fetch_obs(_READ_TOPIC_AS_OF, topic, as_of)

    async def read_market(
        self, venue: str, market_key: str, as_of: date | None = None
    ) -> list[MarketObservation]:
        if as_of is None:
            return await self._fetch_obs(_READ_MARKET_LATEST, venue, market_key)
        return await self._fetch_obs(_READ_MARKET_AS_OF, venue, market_key, as_of)

    async def search_markets(
        self, q: str, venue: str | None, limit: int
    ) -> list[MarketObservation]:
        return await self._fetch_obs(_SEARCH, f"%{q}%", venue, limit)

    async def history(
        self, venue: str, market_key: str, outcome: str, limit: int = 100
    ) -> list[HistoryPoint]:
        try:
            async with self._pool.acquire() as conn:
                records = await conn.fetch(_HISTORY, venue, market_key, outcome, limit)
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"history failed: {exc}") from exc
        return [
            HistoryPoint(
                observed_at=r["observed_at"],
                probability=r["probability"],
            )
            for r in records
        ]

    async def purge_before(self, cutoff_date: date) -> int:
        try:
            async with self._pool.acquire() as conn:
                result = await conn.execute(_PURGE, cutoff_date)
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"purge_before failed: {exc}") from exc
        deleted = int(result.split()[-1]) if result else 0
        logger.info("repo.purge", extra={"deleted": deleted, "cutoff_date": str(cutoff_date)})
        return deleted

    async def read_movers(
        self, threshold: Decimal, limit: int = 50
    ) -> list[MoverItem]:
        try:
            async with self._pool.acquire() as conn:
                records = await conn.fetch(_READ_MOVERS, threshold, limit)
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"read_movers failed: {exc}") from exc
        return [
            MoverItem(
                venue=r["venue"],
                event_title=r["event_title"],
                market_key=r["market_key"],
                outcome=r["outcome"],
                previous=r["prev_prob"],
                current=r["current_prob"],
                delta=r["delta"],
                close_date=r["close_date"],
            )
            for r in records
        ]

    async def read_tracked_current(self) -> list[MarketObservation]:
        return await self._fetch_obs(_READ_TRACKED_CURRENT)
