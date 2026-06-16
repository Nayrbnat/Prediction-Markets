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


class MarketRepository(ABC):
    """Storage contract. The upsert must compute previous/delta atomically and must
    never downgrade a ``tracked`` or ``high``-priority row."""

    @abstractmethod
    async def ping(self) -> bool: ...

    @abstractmethod
    async def upsert_observations(self, observations: list[MarketObservation]) -> None: ...

    @abstractmethod
    async def append_changes(self, observations: list[MarketObservation]) -> None: ...

    @abstractmethod
    async def read_topic(self, topic: str) -> list[MarketObservation]: ...

    @abstractmethod
    async def read_market(self, venue: str, market_key: str) -> list[MarketObservation]: ...

    @abstractmethod
    async def read_tracked(self, limit: int = 50) -> list[MarketObservation]: ...

    @abstractmethod
    async def history(
        self, venue: str, market_key: str, outcome: str, limit: int = 100
    ) -> list[HistoryPoint]: ...

    @abstractmethod
    async def purge_stale(self, retention_days: int) -> int: ...


_UPSERT = """
INSERT INTO market_observations
    (venue, market_key, outcome, event_title, topic, category,
     probability, raw_price, volume, liquidity, confidence, priority, tracked)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
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
"""

_APPEND_CHANGE = """
INSERT INTO market_change_log
    (venue, market_key, outcome, probability, previous_probability, delta, raw_price)
VALUES ($1,$2,$3,$4,$5,$6,$7)
"""

_PURGE = """
DELETE FROM market_observations
WHERE NOT tracked AND priority <> 'high'
  AND last_seen_at < now() - make_interval(days => $1)
"""


def _row_to_observation(row: asyncpg.Record) -> MarketObservation:
    return MarketObservation(**dict(row))


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

    async def upsert_observations(self, observations: list[MarketObservation]) -> None:
        if not observations:
            return
        rows = [
            (
                o.venue, o.market_key, o.outcome, o.event_title, o.topic, o.category,
                o.probability, o.raw_price, o.volume, o.liquidity, o.confidence,
                o.priority, o.tracked,
            )
            for o in observations
        ]
        try:
            async with self._pool.acquire() as conn:
                await conn.executemany(_UPSERT, rows)
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"upsert failed: {exc}") from exc
        logger.info("repo.upsert", extra={"rows": len(rows)})

    async def append_changes(self, observations: list[MarketObservation]) -> None:
        if not observations:
            return
        rows = [
            (o.venue, o.market_key, o.outcome, o.probability,
             o.previous_probability, o.probability_delta, o.raw_price)
            for o in observations
        ]
        try:
            async with self._pool.acquire() as conn:
                await conn.executemany(_APPEND_CHANGE, rows)
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"append_changes failed: {exc}") from exc
        logger.info("repo.changelog", extra={"rows": len(rows)})

    async def _fetch(self, query: str, *args: object) -> list[MarketObservation]:
        try:
            async with self._pool.acquire() as conn:
                records = await conn.fetch(query, *args)
        except Exception as exc:  # noqa: BLE001
            raise PersistenceError(f"read failed: {exc}") from exc
        return [_row_to_observation(r) for r in records]

    async def read_topic(self, topic: str) -> list[MarketObservation]:
        return await self._fetch(
            "SELECT * FROM market_observations WHERE topic = $1 ORDER BY event_title, outcome",
            topic,
        )

    async def read_market(self, venue: str, market_key: str) -> list[MarketObservation]:
        return await self._fetch(
            "SELECT * FROM market_observations WHERE venue = $1 AND market_key = $2 "
            "ORDER BY outcome",
            venue, market_key,
        )

    async def read_tracked(self, limit: int = 50) -> list[MarketObservation]:
        return await self._fetch(
            "SELECT * FROM market_observations WHERE tracked "
            "ORDER BY abs(probability_delta) DESC NULLS LAST LIMIT $1",
            limit,
        )

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
