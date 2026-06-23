"""Contract tests for the repository semantics, via the in-memory implementation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.domain import MarketObservation
from tests.fakes import InMemoryMarketRepository


def _obs(prob: str, *, tracked: bool = False, priority: str = "normal") -> MarketObservation:
    return MarketObservation(
        venue="polymarket",
        market_key="0xabc",
        outcome="Yes",
        event_title="Fed",
        topic="fed",
        probability=Decimal(prob),
        raw_price=Decimal(prob),
        tracked=tracked,
        priority=priority,  # type: ignore[arg-type]
    )


async def test_first_upsert_has_no_previous() -> None:
    repo = InMemoryMarketRepository()
    await repo.upsert_observations([_obs("0.60")])
    stored = (await repo.read_topic("fed"))[0]
    assert stored.previous_probability is None
    assert stored.probability_delta is None


async def test_second_upsert_computes_delta() -> None:
    repo = InMemoryMarketRepository()
    await repo.upsert_observations([_obs("0.60")])
    await repo.upsert_observations([_obs("0.62")])
    stored = (await repo.read_topic("fed"))[0]
    assert stored.previous_probability == Decimal("0.60")
    assert stored.probability_delta == Decimal("0.02")


async def test_upsert_never_downgrades_priority_or_tracked() -> None:
    repo = InMemoryMarketRepository()
    await repo.upsert_observations([_obs("0.60", tracked=True, priority="high")])
    await repo.upsert_observations([_obs("0.62", tracked=False, priority="normal")])
    stored = (await repo.read_topic("fed"))[0]
    assert stored.priority == "high"
    assert stored.tracked is True


async def test_purge_removes_only_stale_untracked() -> None:
    repo = InMemoryMarketRepository()
    await repo.upsert_observations([_obs("0.60")])
    # Force the row to look old.
    key = ("polymarket", "0xabc", "Yes")
    repo.store[key] = repo.store[key].model_copy(
        update={"last_seen_at": datetime.now(timezone.utc) - timedelta(days=30)}
    )
    deleted = await repo.purge_stale(retention_days=15)
    assert deleted == 1
    assert await repo.read_topic("fed") == []


async def test_purge_keeps_tracked_even_if_old() -> None:
    repo = InMemoryMarketRepository()
    await repo.upsert_observations([_obs("0.60", tracked=True, priority="high")])
    key = ("polymarket", "0xabc", "Yes")
    repo.store[key] = repo.store[key].model_copy(
        update={"last_seen_at": datetime.now(timezone.utc) - timedelta(days=30)}
    )
    deleted = await repo.purge_stale(retention_days=15)
    assert deleted == 0
