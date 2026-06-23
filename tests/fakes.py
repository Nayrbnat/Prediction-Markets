"""In-memory MarketRepository mirroring the SQL contract. Used by persistence and
service tests so the suite needs no database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.domain import MarketObservation, MarketRef, OrderBookTop
from app.models.provenance import Venue
from app.models.responses import HistoryPoint
from app.persistence.repository import MarketRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FakeGateway:
    """In-memory Gateway: returns canned refs per topic and books per token id."""

    def __init__(
        self,
        refs_by_topic: dict[str, list[MarketRef]] | None = None,
        books: dict[str, OrderBookTop] | None = None,
    ) -> None:
        self.refs: dict[str, list[MarketRef]] = refs_by_topic or {}
        self.books: dict[str, OrderBookTop] = books or {}
        self.closed = False

    async def discover(
        self, topic: str, *, venues: list[Venue] | None = None, limit: int = 50
    ) -> list[MarketRef]:
        refs = self.refs.get(topic, [])
        if venues is not None:
            refs = [r for r in refs if r.venue in venues]
        return list(refs)

    async def order_book(self, token_id: str) -> OrderBookTop | None:
        return self.books.get(token_id)

    async def aclose(self) -> None:
        self.closed = True


class InMemoryMarketRepository(MarketRepository):
    def __init__(self) -> None:
        self.store: dict[tuple[str, str, str], MarketObservation] = {}
        self.changelog: list[MarketObservation] = []

    async def ping(self) -> bool:
        return True

    async def upsert_observations(self, observations: list[MarketObservation]) -> None:
        for obs in observations:
            key = (obs.venue, obs.market_key, obs.outcome)
            existing = self.store.get(key)
            now = _now()
            if existing is None:
                self.store[key] = obs.model_copy(
                    update={
                        "previous_probability": None,
                        "probability_delta": None,
                        "first_seen_at": now,
                        "last_seen_at": now,
                        "last_changed_at": None,
                        "updated_at": now,
                    }
                )
            else:
                changed = obs.probability != existing.probability
                self.store[key] = obs.model_copy(
                    update={
                        "previous_probability": existing.probability,
                        "probability_delta": obs.probability - existing.probability,
                        # never downgrade priority/tracked
                        "priority": "high" if existing.priority == "high" else obs.priority,
                        "tracked": existing.tracked or obs.tracked,
                        "first_seen_at": existing.first_seen_at,
                        "last_seen_at": now,
                        "last_changed_at": now if changed else existing.last_changed_at,
                        "updated_at": now,
                    }
                )

    async def append_changes(self, observations: list[MarketObservation]) -> None:
        self.changelog.extend(o.model_copy() for o in observations)

    async def read_topic(self, topic: str) -> list[MarketObservation]:
        return [o for o in self.store.values() if o.topic == topic]

    async def read_market(self, venue: str, market_key: str) -> list[MarketObservation]:
        return [
            o for o in self.store.values() if o.venue == venue and o.market_key == market_key
        ]

    async def read_tracked(self, limit: int = 50) -> list[MarketObservation]:
        tracked = [o for o in self.store.values() if o.tracked]
        tracked.sort(key=lambda o: abs(o.probability_delta or 0), reverse=True)
        return tracked[:limit]

    async def history(
        self, venue: str, market_key: str, outcome: str, limit: int = 100
    ) -> list[HistoryPoint]:
        rows = [
            o for o in self.changelog
            if o.venue == venue and o.market_key == market_key and o.outcome == outcome
        ]
        rows.reverse()
        return [
            HistoryPoint(
                observed_at=o.updated_at or _now(),
                probability=o.probability,
                previous_probability=o.previous_probability,
                delta=o.probability_delta,
            )
            for o in rows[:limit]
        ]

    async def purge_stale(self, retention_days: int) -> int:
        cutoff = _now() - timedelta(days=retention_days)
        doomed = [
            key
            for key, o in self.store.items()
            if not o.tracked
            and o.priority != "high"
            and (o.last_seen_at or _now()) < cutoff
        ]
        for key in doomed:
            del self.store[key]
        return len(doomed)
