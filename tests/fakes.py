"""In-memory MarketRepository mirroring the new append-only SQL contract.
Used by persistence and service tests so the suite needs no database.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.models.domain import MarketObservation, MarketRef, OrderBookTop
from app.models.provenance import Venue
from app.models.responses import HistoryPoint
from app.persistence.repository import MarketRepository, _dedupe_observations


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return datetime.now(timezone.utc).date()


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
    """Thread-safe-enough for tests.

    Snapshots keyed by (snapshot_date, venue, market_key, outcome).
    """

    def __init__(self) -> None:
        # Main snapshot store: (snapshot_date, venue, market_key, outcome) -> MarketObservation
        self.snapshots: dict[tuple[date, str, str, str], MarketObservation] = {}
        # Topic mappings: (venue, market_key, topic) -> dict of metadata
        self.topic_pairs: dict[tuple[str, str, str], dict] = {}
        # Run tracking
        self._run_counter: int = 0
        self.runs: list[dict] = []
        self.refresh_count: int = 0

    async def ping(self) -> bool:
        return True

    async def start_run(self, snapshot_date: date) -> int:
        self._run_counter += 1
        run_id = self._run_counter
        self.runs.append({
            "run_id": run_id,
            "snapshot_date": snapshot_date,
            "status": "running",
            "started_at": _now(),
        })
        return run_id

    async def write_snapshots(
        self,
        observations: list[MarketObservation],
        snapshot_date: date,
        run_id: int,
    ) -> int:
        rows = _dedupe_observations(observations)
        now = _now()
        for obs in rows:
            key = (snapshot_date, obs.venue, obs.market_key, obs.outcome)
            self.snapshots[key] = obs.model_copy(update={"updated_at": now})
        return len(rows)

    async def upsert_market_topics(self, pairs: list[dict]) -> None:
        for pair in pairs:
            key = (pair["venue"], pair["market_key"], pair["topic"])
            existing = self.topic_pairs.get(key)
            if existing is None:
                self.topic_pairs[key] = dict(pair)
            else:
                # Escalate priority/tracked
                if pair.get("priority") == "high" or existing.get("priority") == "high":
                    existing["priority"] = "high"
                existing["tracked"] = existing.get("tracked", False) or pair.get("tracked", False)
                if pair.get("category"):
                    existing["category"] = pair["category"]
                if pair.get("event_title"):
                    existing["event_title"] = pair["event_title"]

    async def refresh_latest(self) -> None:
        self.refresh_count += 1

    async def finish_run(
        self, run_id: int, status: str, topics: int, rows_written: int
    ) -> None:
        for run in self.runs:
            if run["run_id"] == run_id:
                run.update({
                    "status": status,
                    "topics": topics,
                    "rows_written": rows_written,
                    "finished_at": _now(),
                })
                break

    def _latest_per_series(
        self, as_of: date | None = None
    ) -> dict[tuple[str, str, str], MarketObservation]:
        """Return max-snapshot_date obs per (venue, market_key, outcome), optionally as-of."""
        best: dict[tuple[str, str, str], tuple[date, MarketObservation]] = {}
        for (snap_date, venue, market_key, outcome), obs in self.snapshots.items():
            if as_of is not None and snap_date > as_of:
                continue
            series_key = (venue, market_key, outcome)
            current_best = best.get(series_key)
            if current_best is None or snap_date > current_best[0]:
                best[series_key] = (snap_date, obs)
        return {k: v[1] for k, v in best.items()}

    def _enrich_with_topic(
        self, obs: MarketObservation, venue: str, market_key: str
    ) -> MarketObservation:
        """Add topic/category/priority/tracked from first matching topic pair."""
        for (v, mk, _topic), meta in self.topic_pairs.items():
            if v == venue and mk == market_key:
                return obs.model_copy(update={
                    "topic": meta.get("topic"),
                    "category": meta.get("category"),
                    "priority": meta.get("priority", "normal"),
                    "tracked": meta.get("tracked", False),
                })
        return obs

    async def read_topic(
        self, topic: str, as_of: date | None = None
    ) -> list[MarketObservation]:
        # Find all (venue, market_key) pairs associated with this topic
        matching_markets: set[tuple[str, str]] = {
            (v, mk)
            for (v, mk, t) in self.topic_pairs
            if t == topic
        }
        if not matching_markets:
            # Fallback: search snapshots by obs.topic field directly
            latest = self._latest_per_series(as_of)
            return [
                obs for obs in latest.values()
                if obs.topic == topic
            ]

        latest = self._latest_per_series(as_of)
        result = []
        for (venue, market_key, _outcome), obs in latest.items():
            if (venue, market_key) in matching_markets:
                meta_key = next(
                    ((v, mk, t) for (v, mk, t) in self.topic_pairs
                     if v == venue and mk == market_key and t == topic),
                    None,
                )
                if meta_key:
                    meta = self.topic_pairs[meta_key]
                    obs = obs.model_copy(update={
                        "topic": meta.get("topic"),
                        "category": meta.get("category"),
                        "priority": meta.get("priority", "normal"),
                        "tracked": meta.get("tracked", False),
                    })
                result.append(obs)
        return result

    async def read_market(
        self, venue: str, market_key: str, as_of: date | None = None
    ) -> list[MarketObservation]:
        latest = self._latest_per_series(as_of)
        result = [
            obs for (v, mk, _outcome), obs in latest.items()
            if v == venue and mk == market_key
        ]
        return sorted(result, key=lambda o: o.outcome)

    async def search_markets(
        self, q: str, venue: str | None, limit: int
    ) -> list[MarketObservation]:
        q_lower = q.lower()
        latest = self._latest_per_series()
        results = []
        for obs in latest.values():
            if venue is not None and obs.venue != venue:
                continue
            enriched = self._enrich_with_topic(obs, obs.venue, obs.market_key)
            if q_lower in (enriched.topic or "").lower() or q_lower in obs.event_title.lower():
                results.append(enriched)
        results.sort(key=lambda o: (o.venue, o.market_key, o.outcome))
        return results[:limit]

    async def history(
        self, venue: str, market_key: str, outcome: str, limit: int = 100
    ) -> list[HistoryPoint]:
        rows = [
            (snap_date, obs)
            for (snap_date, v, mk, out), obs in self.snapshots.items()
            if v == venue and mk == market_key and out == outcome
        ]
        rows.sort(key=lambda x: x[0], reverse=True)
        return [
            HistoryPoint(
                observed_at=obs.updated_at or _now(),
                probability=obs.probability,
            )
            for snap_date, obs in rows[:limit]
        ]

    async def purge_before(self, cutoff_date: date) -> int:
        doomed = [
            key for key in self.snapshots
            if key[0] < cutoff_date
        ]
        for key in doomed:
            del self.snapshots[key]
        return len(doomed)

    # ------------------------------------------------------------------
    # Convenience helpers for seeding tests (mirrors the old upsert_observations API).
    # Tests call write_snapshots directly now, but we keep a thin helper.
    # ------------------------------------------------------------------
    async def seed(
        self,
        observations: list[MarketObservation],
        snapshot_date: date | None = None,
    ) -> None:
        """Seed snapshots for a given date (default: today). Also seeds topic pairs."""
        sd = snapshot_date or _today()
        run_id = await self.start_run(sd)
        await self.write_snapshots(observations, sd, run_id)
        # Auto-seed topic pairs from obs.topic
        pairs = []
        seen: set[tuple[str, str, str]] = set()
        for obs in observations:
            if obs.topic:
                key = (obs.venue, obs.market_key, obs.topic)
                if key not in seen:
                    seen.add(key)
                    pairs.append({
                        "venue": obs.venue,
                        "market_key": obs.market_key,
                        "topic": obs.topic,
                        "category": obs.category,
                        "priority": obs.priority,
                        "tracked": obs.tracked,
                        "event_title": obs.event_title,
                    })
        if pairs:
            await self.upsert_market_topics(pairs)
        await self.finish_run(run_id, "ok", 1, len(observations))
