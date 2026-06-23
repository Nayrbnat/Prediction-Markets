"""Service tests for /analyze: pure store-read behavior and degradation notes."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.domain import MarketObservation
from app.models.requests import AnalyzeRequest
from app.services.analysis_service import analyze
from tests.fakes import InMemoryMarketRepository

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


def _obs(prob: str, topic: str = "fed", venue: str = "polymarket") -> MarketObservation:
    return MarketObservation(
        venue=venue, market_key="m1", outcome="Yes", event_title="Fed decision",
        topic=topic, probability=Decimal(prob), raw_price=Decimal(prob),
    )


async def test_served_from_store_with_data() -> None:
    repo = InMemoryMarketRepository()
    await repo.seed([_obs("0.60")])
    res = await analyze(AnalyzeRequest(topic="fed"), repo=repo)
    assert res.stale is True
    assert len(res.distributions) == 1
    assert res.distributions[0].outcomes[0].probability == Decimal("0.60")


async def test_empty_topic_returns_note() -> None:
    repo = InMemoryMarketRepository()
    res = await analyze(AnalyzeRequest(topic="ghost"), repo=repo)
    assert res.distributions == []
    assert res.markets == []
    assert any("no ingested data" in n for n in res.notes)


async def test_no_repo_returns_note() -> None:
    res = await analyze(AnalyzeRequest(topic="fed"), repo=None)
    assert res.distributions == []
    assert any("no ingested data" in n for n in res.notes)


async def test_venue_availability_reflects_store() -> None:
    repo = InMemoryMarketRepository()
    await repo.seed([_obs("0.62", venue="polymarket")])
    res = await analyze(AnalyzeRequest(topic="fed"), repo=repo)
    avail = {a.venue: a for a in res.venue_availability}
    assert avail["polymarket"].matched is True
    assert avail["kalshi"].matched is False


async def test_multi_venue_in_store() -> None:
    repo = InMemoryMarketRepository()
    await repo.seed([
        _obs("0.62", venue="polymarket"),
        MarketObservation(
            venue="kalshi", market_key="FED-2024", outcome="Yes",
            event_title="Fed decision", topic="fed",
            probability=Decimal("0.60"), raw_price=Decimal("0.60"),
        ),
    ])
    res = await analyze(AnalyzeRequest(topic="fed"), repo=repo)
    avail = {a.venue: a for a in res.venue_availability}
    assert avail["polymarket"].matched is True
    assert avail["kalshi"].matched is True
    assert len(res.distributions) == 2


async def test_store_path_does_not_emit_live_failure_notes() -> None:
    repo = InMemoryMarketRepository()
    await repo.seed([_obs("0.55")])
    res = await analyze(AnalyzeRequest(topic="fed"), repo=repo)
    assert not any("no live markets" in n for n in res.notes)
    assert res.stale is True


async def test_as_of_returns_historical_value() -> None:
    """analyze with as_of returns the point-in-time state as of that date."""
    repo = InMemoryMarketRepository()
    await repo.seed([_obs("0.60")], snapshot_date=YESTERDAY)
    await repo.seed([_obs("0.80")], snapshot_date=TODAY)
    res = await analyze(AnalyzeRequest(topic="fed", as_of=YESTERDAY), repo=repo)
    assert len(res.distributions) == 1
    assert res.distributions[0].outcomes[0].probability == Decimal("0.60")
    # as_of queries are not stale
    assert res.stale is False
