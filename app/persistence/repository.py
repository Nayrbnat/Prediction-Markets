"""Market repository: the contract for reading/writing the store, plus the asyncpg
implementation. Callers depend on the ABC, never on asyncpg.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.core.errors import PersistenceError
from app.core.logging import get_logger
from app.models.domain import MarketObservation
from app.models.responses import HistoryPoint

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg

logger = get_logger(__name__)

# Explicit column list shared by all SELECT queries — never SELECT *.
_OBS_COLS = (
    "venue, market_key, outcome, event_title, topic, category, "
    "probability, previous_probability, probability_delta, raw_price, "
    "volume, liquidity, confidence, priority, tracked, "
    "first_seen_at, last_seen_at, last_changed_at, updated_at"
)


class MarketRepository(ABC):
    """Storage contract. The upsert must compute previous/delta atomically and must
    never downgrade a ``tracked`` or ``high``-priority row.

    ``upsert_observations`` returns the upserted rows (with previous_probability and
    probability_delta populated from the DB SET clause) so the caller can derive
    change-log entries without a second round-trip.
    """

    @abstractmethod
    async def ping(self) -> bool: ...

    @abstractmethod
    async def upsert_observations(
        self, observations: list[MarketObservation]
    ) -> list[MarketObservation]: ...

    @abstractmethod
    async def append_changes(self, observations: list[MarketObservation]) -> None: ...

    @abstractmethod
    async def read_topic(self, topic: str) -> list[MarketObservation]: ...

    @abstractmethod
    async def read_market(self, venue: str, market_key: str) -> list[MarketObservation]: ...

    @abstractmethod
    async def read_tracked(self, limit: int = 50) -> list[MarketObservation]: ...

    @abstractmethod
    async def search_markets(
        self, q: str, venue: str | None, limit: int
    ) -> list[MarketObservation]: ...

    @abstractmethod
    async def history(
        self, venue: str, market_key: str, outcome: str, limit: int = 100
    ) -> list[HistoryPoint]: ...

    @abstractmethod
    async def purge_stale(self, retention_days: int) -> int: ...


# Single-statement bulk upsert via unnest — one round-trip for any batch size.
# RETURNING gives back the post-upsert row (including previous_probability/delta
# computed by the SET clause), so the service can detect material moves in Python.
_UPSERT_UNNEST = f"""
INSERT INTO market_observations
    (venue, market_key, outcome, event_title, topic, category,
     probability, raw_price, volume, liquidity, confidence, priority, tracked)
SELECT * FROM unnest(
    $1::text[], $2::text[], $3::text[], $4::text[], $5::text[], $6::text[],
    $7::numeric[], $8::numeric[], $9::numeric[], $10::numeric[],
    $11::text[], $12::text[], $13::boolean[]
)
ON CONFLICT (venue, market_key, outcome) DO UPDATE SET
    previous_probability = market_observations.probability,
    probability          = EXCLUDED.probability,
    probability_delta    = EXCLUDED.probability - market_observations.probability,
    raw_price            = EXCLUDED.raw_price,
    volume               = EXCLUDED.volume,
    liquidity            = EXCLUDED.liquidity,
    confidence           = EXCLUDED.confidence,
    event_title          = EXCLUDED.event_title,
    topic                = COALESCE(EXCLUDED.topic, market_observations.topic),
    category             = COALESCE(EXCLUDED.category, market_observations.category),
    priority             = CASE WHEN market_observations.priority = 'high'
                                THEN 'high' ELSE EXCLUDED.priority END,
    tracked              = market_observations.tracked OR EXCLUDED.tracked,
    last_seen_at         = now(),
    last_changed_at      = CASE WHEN EXCLUDED.probability <> market_observations.probability
                                THEN now() ELSE market_observations.last_changed_at END,
    updated_at           = now()
RETURNING {_OBS_COLS}
"""

# Bulk change-log insert via unnest — one round-trip for any batch size.
_APPEND_CHANGES_UNNEST = """
INSERT INTO market_change_log
    (venue, market_key, outcome, probability, previous_probability, delta, raw_price)
SELECT * FROM unnest(
    $1::text[], $2::text[], $3::text[],
    $4::numeric[], $5::numeric[], $6::numeric[], $7::numeric[]
)
"""

_PURGE = """
DELETE FROM market_observations
WHERE NOT tracked AND priority <> 'high'
  AND last_seen_at < now() - make_interval(days => $1)
"""

_SEARCH = f"""
SELECT {_OBS_COLS}
FROM market_observations
WHERE ($2::text IS NULL OR venue = $2)
  AND (topic ILIKE $1 OR event_title ILIKE $1)
ORDER BY venue, market_key, outcome
LIMIT $3
"""


def _row_to_observation(row: asyncpg.Record) -> MarketObservation:
    return MarketObservation(**dict(row))


def _dedupe_observations(observations: list[MarketObservation]) -> list[MarketObservation]:
    """Collapse rows sharing a (venue, market_key, outcome) key into one.

    The single-statement ``INSERT ... ON CONFLICT DO UPDATE`` cannot affect the same
    target row twice in one command, so a batch with duplicate keys would error and
    fail wholesale. ``run_ingestion`` concatenates observations across topics, and the
    same market can surface under overlapping watchlist topics, so duplicates are
    reachable.

    Keep the later row's values (last-write-wins, matching prior executemany ordering)
    but ESCALATE flags so tracked/high status is never lost:
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


class PostgresMarketRepository(MarketRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def ping(self) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001 - ping reports, never raises
            return False

    async def upsert_observations(
        self, observations: list[MarketObservation]
    ) -> list[MarketObservation]:
        if not observations:
            return []
        # De-dup by key first: ON CONFLICT DO UPDATE cannot affect a row twice in one
        # statement. Duplicates arise when one market surfaces under overlapping topics.
        rows = _dedupe_observations(observations)
        # Transpose rows into per-column arrays for unnest.
        # None values in numeric columns become NULL array elements (supported by asyncpg).
        venues        = [o.venue        for o in rows]
        market_keys   = [o.market_key   for o in rows]
        outcomes      = [o.outcome      for o in rows]
        event_titles  = [o.event_title  for o in rows]
        topics        = [o.topic        for o in rows]
        categories    = [o.category     for o in rows]
        probabilities = [o.probability  for o in rows]
        raw_prices    = [o.raw_price    for o in rows]
        volumes       = [o.volume       for o in rows]
        liquidities   = [o.liquidity    for o in rows]
        confidences   = [o.confidence   for o in rows]
        priorities    = [o.priority     for o in rows]
        trackeds      = [o.tracked      for o in rows]

        try:
            async with self._pool.acquire() as conn:
                records = await conn.fetch(
                    _UPSERT_UNNEST,
                    venues, market_keys, outcomes, event_titles, topics, categories,
                    probabilities, raw_prices, volumes, liquidities,
                    confidences, priorities, trackeds,
                )
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"upsert failed: {exc}") from exc

        result = [_row_to_observation(r) for r in records]
        logger.info(
            "repo.upsert",
            extra={"rows": len(observations), "deduped": len(rows), "returned": len(result)},
        )
        return result

    async def append_changes(self, observations: list[MarketObservation]) -> None:
        if not observations:
            return
        venues        = [o.venue               for o in observations]
        market_keys   = [o.market_key           for o in observations]
        outcomes      = [o.outcome              for o in observations]
        probs         = [o.probability          for o in observations]
        prev_probs    = [o.previous_probability for o in observations]
        deltas        = [o.probability_delta    for o in observations]
        raw_prices    = [o.raw_price            for o in observations]

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    _APPEND_CHANGES_UNNEST,
                    venues, market_keys, outcomes, probs, prev_probs, deltas, raw_prices,
                )
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"append_changes failed: {exc}") from exc
        logger.info("repo.changelog", extra={"rows": len(observations)})

    async def _fetch(self, query: str, *args: object) -> list[MarketObservation]:
        try:
            async with self._pool.acquire() as conn:
                records = await conn.fetch(query, *args)
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"read failed: {exc}") from exc
        return [_row_to_observation(r) for r in records]

    async def read_topic(self, topic: str) -> list[MarketObservation]:
        return await self._fetch(
            f"SELECT {_OBS_COLS} FROM market_observations "
            "WHERE topic = $1 ORDER BY event_title, outcome",
            topic,
        )

    async def read_market(self, venue: str, market_key: str) -> list[MarketObservation]:
        return await self._fetch(
            f"SELECT {_OBS_COLS} FROM market_observations "
            "WHERE venue = $1 AND market_key = $2 ORDER BY outcome",
            venue, market_key,
        )

    async def read_tracked(self, limit: int = 50) -> list[MarketObservation]:
        return await self._fetch(
            f"SELECT {_OBS_COLS} FROM market_observations WHERE tracked "
            "ORDER BY abs(probability_delta) DESC NULLS LAST LIMIT $1",
            limit,
        )

    async def search_markets(
        self, q: str, venue: str | None, limit: int
    ) -> list[MarketObservation]:
        return await self._fetch(_SEARCH, f"%{q}%", venue, limit)

    async def history(
        self, venue: str, market_key: str, outcome: str, limit: int = 100
    ) -> list[HistoryPoint]:
        try:
            async with self._pool.acquire() as conn:
                records = await conn.fetch(
                    "SELECT observed_at, probability, previous_probability, delta "
                    "FROM market_change_log WHERE venue=$1 AND market_key=$2 AND outcome=$3 "
                    "ORDER BY observed_at DESC LIMIT $4",
                    venue, market_key, outcome, limit,
                )
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"history failed: {exc}") from exc
        return [HistoryPoint(**dict(r)) for r in records]

    async def purge_stale(self, retention_days: int) -> int:
        try:
            async with self._pool.acquire() as conn:
                result = await conn.execute(_PURGE, retention_days)
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"purge failed: {exc}") from exc
        deleted = int(result.split()[-1]) if result else 0
        logger.info("repo.purge", extra={"deleted": deleted, "retention_days": retention_days})
        return deleted
