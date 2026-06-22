"""Contract tests for the repository semantics, via the in-memory implementation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.domain import MarketObservation
from app.persistence.repository import _dedupe_observations
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


def test_dedupe_collapses_duplicate_keys_and_escalates_flags() -> None:
    """Two rows with the same key collapse to one; tracked/high are never lost.

    Guards the single-statement upsert: ON CONFLICT DO UPDATE cannot affect the same
    target row twice, so duplicates (one tracked/high, one not) must be merged first.
    """
    tracked_high = _obs("0.60", tracked=True, priority="high")
    plain = _obs("0.65", tracked=False, priority="normal")

    # Order 1: tracked/high first, plain second (plain's values win, flags escalate).
    merged = _dedupe_observations([tracked_high, plain])
    assert len(merged) == 1
    row = merged[0]
    assert row.probability == Decimal("0.65")  # later row's values
    assert row.tracked is True  # escalated
    assert row.priority == "high"  # escalated

    # Order 2: plain first, tracked/high second — flags still escalate.
    merged_rev = _dedupe_observations([plain, tracked_high])
    assert len(merged_rev) == 1
    row_rev = merged_rev[0]
    assert row_rev.probability == Decimal("0.60")  # later (tracked_high) values
    assert row_rev.tracked is True
    assert row_rev.priority == "high"


def test_dedupe_preserves_distinct_keys_and_order() -> None:
    a = _obs("0.50")
    b = a.model_copy(update={"outcome": "No", "probability": Decimal("0.50")})
    c = a.model_copy(update={"market_key": "0xdef"})
    merged = _dedupe_observations([a, b, c])
    assert len(merged) == 3
    assert [(o.market_key, o.outcome) for o in merged] == [
        ("0xabc", "Yes"),
        ("0xabc", "No"),
        ("0xdef", "Yes"),
    ]


async def test_inmemory_upsert_tolerates_duplicate_keys_in_one_call() -> None:
    """The fake processes sequentially so a single call with dupes must not error;
    the last write wins and flags escalate (matching the deduped Postgres path)."""
    repo = InMemoryMarketRepository()
    await repo.upsert_observations(
        [
            _obs("0.60", tracked=True, priority="high"),
            _obs("0.65", tracked=False, priority="normal"),
        ]
    )
    stored = await repo.read_topic("fed")
    assert len(stored) == 1
    assert stored[0].probability == Decimal("0.65")
    assert stored[0].tracked is True
    assert stored[0].priority == "high"
