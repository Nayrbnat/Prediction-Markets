"""Contract tests for the repository semantics, via the in-memory implementation."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.domain import MarketObservation
from app.persistence.repository import _dedupe_observations
from tests.fakes import InMemoryMarketRepository


def _obs(
    prob: str, *, tracked: bool = False, priority: str = "normal", topic: str = "fed"
) -> MarketObservation:
    return MarketObservation(
        venue="polymarket",
        market_key="0xabc",
        outcome="Yes",
        event_title="Fed",
        topic=topic,
        probability=Decimal(prob),
        raw_price=Decimal(prob),
        tracked=tracked,
        priority=priority,  # type: ignore[arg-type]
    )


TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


async def test_write_snapshots_idempotent_same_day() -> None:
    """Writing the same (snapshot_date, venue, market_key, outcome) twice keeps one row."""
    repo = InMemoryMarketRepository()
    run_id = await repo.start_run(TODAY)
    await repo.write_snapshots([_obs("0.60")], TODAY, run_id)
    await repo.write_snapshots([_obs("0.65")], TODAY, run_id)
    # Only one snapshot for TODAY (idempotent overwrite)
    snaps = [k for k in repo.snapshots if k[0] == TODAY]
    assert len(snaps) == 1
    # Value should be the latest write
    key = (TODAY, "polymarket", "0xabc", "Yes")
    assert repo.snapshots[key].probability == Decimal("0.65")


async def test_read_topic_returns_latest_snapshot() -> None:
    """read_topic joins market_topics and returns the latest snapshot per series."""
    repo = InMemoryMarketRepository()
    await repo.seed([_obs("0.60")], snapshot_date=YESTERDAY)
    await repo.seed([_obs("0.70")], snapshot_date=TODAY)
    results = await repo.read_topic("fed")
    assert len(results) == 1
    assert results[0].probability == Decimal("0.70")


async def test_read_topic_as_of_returns_historical_value() -> None:
    """read_topic with as_of returns the snapshot as of that date."""
    repo = InMemoryMarketRepository()
    await repo.seed([_obs("0.60")], snapshot_date=YESTERDAY)
    await repo.seed([_obs("0.70")], snapshot_date=TODAY)
    results = await repo.read_topic("fed", as_of=YESTERDAY)
    assert len(results) == 1
    assert results[0].probability == Decimal("0.60")


async def test_market_mapped_to_two_topics() -> None:
    """A market mapped to two topics is returned by read_topic for BOTH."""
    repo = InMemoryMarketRepository()
    run_id = await repo.start_run(TODAY)
    obs = _obs("0.60", topic="fed")
    await repo.write_snapshots([obs], TODAY, run_id)
    # Map same market to two topics
    await repo.upsert_market_topics([
        {"venue": "polymarket", "market_key": "0xabc", "topic": "fed",
         "priority": "normal", "tracked": False, "event_title": "Fed"},
        {"venue": "polymarket", "market_key": "0xabc", "topic": "rates",
         "priority": "normal", "tracked": False, "event_title": "Fed"},
    ])
    await repo.finish_run(run_id, "ok", 2, 1)

    fed_results = await repo.read_topic("fed")
    rates_results = await repo.read_topic("rates")
    assert len(fed_results) == 1
    assert len(rates_results) == 1
    assert fed_results[0].market_key == "0xabc"
    assert rates_results[0].market_key == "0xabc"


async def test_read_market_no_duplicate_outcomes_when_multi_topic() -> None:
    """A market mapped to two topics returns exactly its distinct outcomes — no fan-out.

    Regression: the LATEST read must yield one row per (venue, market_key, outcome)
    even when the market belongs to multiple topics, or distribution normalisation
    would divide by an inflated sum.
    """
    repo = InMemoryMarketRepository()
    run_id = await repo.start_run(TODAY)
    yes = _obs("0.60")  # outcome="Yes"
    no = yes.model_copy(update={"outcome": "No", "probability": Decimal("0.40")})
    await repo.write_snapshots([yes, no], TODAY, run_id)
    # Same market mapped under two topics.
    await repo.upsert_market_topics([
        {"venue": "polymarket", "market_key": "0xabc", "topic": "fed",
         "priority": "normal", "tracked": False, "event_title": "Fed"},
        {"venue": "polymarket", "market_key": "0xabc", "topic": "rates",
         "priority": "normal", "tracked": False, "event_title": "Fed"},
    ])
    await repo.finish_run(run_id, "ok", 2, 2)

    rows = await repo.read_market("polymarket", "0xabc")
    assert [r.outcome for r in rows] == ["No", "Yes"]  # exactly the distinct outcomes
    assert len(rows) == 2


async def test_history_across_two_dates() -> None:
    """history returns one point per snapshot date, newest first."""
    repo = InMemoryMarketRepository()
    await repo.seed([_obs("0.60")], snapshot_date=YESTERDAY)
    await repo.seed([_obs("0.70")], snapshot_date=TODAY)
    points = await repo.history("polymarket", "0xabc", "Yes", limit=100)
    assert len(points) == 2
    # Newest first
    assert points[0].probability == Decimal("0.70")
    assert points[1].probability == Decimal("0.60")


async def test_purge_before_removes_old_snapshots() -> None:
    """purge_before removes snapshots older than the cutoff date."""
    repo = InMemoryMarketRepository()
    old_date = date(2024, 1, 1)
    await repo.seed([_obs("0.60")], snapshot_date=old_date)
    await repo.seed([_obs("0.70")], snapshot_date=TODAY)
    deleted = await repo.purge_before(date(2024, 6, 1))
    assert deleted == 1
    remaining = await repo.read_topic("fed")
    assert len(remaining) == 1
    assert remaining[0].probability == Decimal("0.70")


def test_dedupe_collapses_duplicate_keys_and_escalates_flags() -> None:
    """Two rows with the same key collapse to one; tracked/high are never lost."""
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


async def test_search_markets_no_duplicate_outcomes_when_multi_topic() -> None:
    """search_markets must return at most one row per (venue, market_key, outcome).

    Regression: a market mapped to N topics previously produced N duplicate rows
    per outcome in the SQL query (no de-duplication on the LEFT JOIN fan-out).
    The DISTINCT ON fix and the fake's _latest_per_series path both guard this.
    """
    repo = InMemoryMarketRepository()
    run_id = await repo.start_run(TODAY)
    yes = _obs("0.60", topic="fed")
    no = yes.model_copy(update={"outcome": "No", "probability": Decimal("0.40")})
    await repo.write_snapshots([yes, no], TODAY, run_id)
    # Map same market to two topics — the bug: N topics → N duplicate rows per outcome.
    await repo.upsert_market_topics([
        {"venue": "polymarket", "market_key": "0xabc", "topic": "fed",
         "priority": "normal", "tracked": False, "event_title": "Fed"},
        {"venue": "polymarket", "market_key": "0xabc", "topic": "rates",
         "priority": "normal", "tracked": False, "event_title": "Fed"},
    ])
    await repo.finish_run(run_id, "ok", 2, 2)

    # Search by topic name — market is under both "fed" and "rates"
    rows = await repo.search_markets("fed", venue=None, limit=100)
    outcomes = [r.outcome for r in rows]
    assert sorted(outcomes) == ["No", "Yes"], "expected exactly two distinct outcomes"
    assert len(outcomes) == len(set(outcomes)), "duplicate outcomes found"

    # Also search by event_title to exercise the other match branch
    rows_by_title = await repo.search_markets("Fed", venue=None, limit=100)
    outcomes_by_title = [r.outcome for r in rows_by_title]
    assert len(outcomes_by_title) == len(set(outcomes_by_title)), (
        "duplicate outcomes via title match"
    )


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


# ---------------------------------------------------------------------------
# Digest: read_movers + read_tracked_current contract tests
# ---------------------------------------------------------------------------


async def test_read_movers_returns_tracked_mover() -> None:
    """A tracked outcome that moved >= threshold appears in read_movers."""
    repo = InMemoryMarketRepository()
    # Two snapshot dates — move of 0.15 (>= 0.10 threshold)
    await repo.seed([_obs("0.50", tracked=True, priority="high")], snapshot_date=YESTERDAY)
    await repo.seed([_obs("0.65", tracked=True, priority="high")], snapshot_date=TODAY)

    movers = await repo.read_movers(Decimal("0.10"))
    assert len(movers) == 1
    mover = movers[0]
    assert mover.outcome == "Yes"
    assert mover.previous == Decimal("0.50")
    assert mover.current == Decimal("0.65")
    assert mover.delta == Decimal("0.15")


async def test_read_movers_excludes_small_move() -> None:
    """A move below threshold must NOT appear in read_movers."""
    repo = InMemoryMarketRepository()
    await repo.seed([_obs("0.50", tracked=True, priority="high")], snapshot_date=YESTERDAY)
    await repo.seed([_obs("0.55", tracked=True, priority="high")], snapshot_date=TODAY)

    movers = await repo.read_movers(Decimal("0.10"))
    assert movers == []


async def test_read_movers_excludes_untracked() -> None:
    """An untracked market must NEVER appear in read_movers regardless of move size."""
    repo = InMemoryMarketRepository()
    # Large move but NOT tracked
    await repo.seed([_obs("0.10", tracked=False, priority="normal")], snapshot_date=YESTERDAY)
    await repo.seed([_obs("0.90", tracked=False, priority="normal")], snapshot_date=TODAY)

    movers = await repo.read_movers(Decimal("0.10"))
    assert movers == []


async def test_read_movers_sorted_by_abs_delta_desc() -> None:
    """read_movers returns rows sorted by |delta| descending."""
    repo = InMemoryMarketRepository()

    big_mover = MarketObservation(
        venue="polymarket", market_key="0xbig", outcome="Yes",
        event_title="Big", topic="big", probability=Decimal("0.20"),
        raw_price=Decimal("0.20"), tracked=True, priority="high",
    )
    small_mover = MarketObservation(
        venue="polymarket", market_key="0xsmall", outcome="Yes",
        event_title="Small", topic="small", probability=Decimal("0.40"),
        raw_price=Decimal("0.40"), tracked=True, priority="high",
    )
    # Seed yesterday
    await repo.seed([big_mover, small_mover], snapshot_date=YESTERDAY)

    big_now = big_mover.model_copy(
        update={"probability": Decimal("0.80"), "raw_price": Decimal("0.80")}
    )
    small_now = small_mover.model_copy(
        update={"probability": Decimal("0.52"), "raw_price": Decimal("0.52")}
    )
    await repo.seed([big_now, small_now], snapshot_date=TODAY)

    movers = await repo.read_movers(Decimal("0.10"))
    assert len(movers) == 2
    assert movers[0].market_key == "0xbig"   # |0.60| > |0.12|
    assert movers[1].market_key == "0xsmall"


async def test_read_movers_no_topic_fanout() -> None:
    """A tracked market under TWO topics must yield ONE mover row with the correct delta.

    Regression: a fan-out JOIN on market_topics (M2M) duplicates every snapshot row
    per topic, corrupting the LAG window so the latest row's "previous" is a duplicate
    of the same date (delta 0 → dropped). The EXISTS predicate keeps one row per
    (snapshot_date, series), so LAG correctly compares to yesterday.
    """
    repo = InMemoryMarketRepository()

    # Two snapshot dates, ≥10pp move day-over-day.
    yest = _obs("0.50", tracked=True, priority="high", topic="fed rate decision")
    today = _obs("0.65", tracked=True, priority="high", topic="fed rate decision")
    run_y = await repo.start_run(YESTERDAY)
    await repo.write_snapshots([yest], YESTERDAY, run_y)
    await repo.finish_run(run_y, "ok", 1, 1)
    run_t = await repo.start_run(TODAY)
    await repo.write_snapshots([today], TODAY, run_t)
    await repo.finish_run(run_t, "ok", 1, 1)

    # Map the SAME market to two tracked topics.
    await repo.upsert_market_topics([
        {"venue": "polymarket", "market_key": "0xabc", "topic": "fed rate decision",
         "priority": "high", "tracked": True, "event_title": "Fed"},
        {"venue": "polymarket", "market_key": "0xabc", "topic": "fed interest rate",
         "priority": "high", "tracked": True, "event_title": "Fed"},
    ])

    movers = await repo.read_movers(Decimal("0.10"))
    # Exactly ONE row for the (venue, market_key, outcome) series — not duplicated.
    assert len(movers) == 1
    mover = movers[0]
    assert mover.market_key == "0xabc"
    assert mover.outcome == "Yes"
    assert mover.previous == Decimal("0.50")
    assert mover.current == Decimal("0.65")
    assert mover.delta == Decimal("0.15")


async def test_read_tracked_current_returns_latest_per_series() -> None:
    """read_tracked_current returns the latest row per series for tracked markets."""
    repo = InMemoryMarketRepository()
    await repo.seed([_obs("0.40", tracked=True, priority="high")], snapshot_date=YESTERDAY)
    await repo.seed([_obs("0.55", tracked=True, priority="high")], snapshot_date=TODAY)

    tracked = await repo.read_tracked_current()
    assert len(tracked) == 1
    assert tracked[0].probability == Decimal("0.55")
    assert tracked[0].tracked is True


async def test_read_tracked_current_excludes_untracked() -> None:
    """read_tracked_current must NOT include untracked markets."""
    repo = InMemoryMarketRepository()
    await repo.seed([_obs("0.60", tracked=False, priority="normal")], snapshot_date=TODAY)

    tracked = await repo.read_tracked_current()
    assert tracked == []


async def test_read_tracked_current_no_topic_fanout() -> None:
    """A tracked market under two topics returns exactly its distinct outcomes."""
    repo = InMemoryMarketRepository()
    run_id = await repo.start_run(TODAY)
    yes = _obs("0.60", tracked=True, priority="high")
    no = yes.model_copy(update={"outcome": "No", "probability": Decimal("0.40")})
    await repo.write_snapshots([yes, no], TODAY, run_id)
    await repo.upsert_market_topics([
        {"venue": "polymarket", "market_key": "0xabc", "topic": "fed",
         "priority": "high", "tracked": True, "event_title": "Fed"},
        {"venue": "polymarket", "market_key": "0xabc", "topic": "rates",
         "priority": "high", "tracked": True, "event_title": "Fed"},
    ])
    await repo.finish_run(run_id, "ok", 2, 2)

    tracked = await repo.read_tracked_current()
    outcomes = [o.outcome for o in tracked]
    assert len(outcomes) == len(set(outcomes)), "duplicate outcomes from topic fan-out"
    assert sorted(outcomes) == ["No", "Yes"]
