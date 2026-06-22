"""Service tests for /analyze: live path, degradation, store freshness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from app.models.domain import MarketObservation, MarketRef
from app.models.requests import AnalyzeRequest
from app.services.analysis_service import analyze
from tests.fakes import FakeGateway, InMemoryMarketRepository


def _settings() -> Settings:
    return Settings(database_url="")


def _poly_ref() -> MarketRef:
    return MarketRef(
        venue="polymarket", event_id="E", market_key="m1", event_title="Fed decision",
        outcomes=["Yes", "No"], quoted_prices=[Decimal("0.62"), Decimal("0.38")],
        volume=Decimal("5000"),
    )


def _obs(prob: str) -> MarketObservation:
    return MarketObservation(
        venue="polymarket", market_key="m1", outcome="Yes", event_title="Fed decision",
        topic="fed", probability=Decimal(prob), raw_price=Decimal(prob),
    )


async def test_live_path_builds_distribution() -> None:
    gw = FakeGateway(refs_by_topic={"fed": [_poly_ref()]})
    repo = InMemoryMarketRepository()
    res = await analyze(AnalyzeRequest(topic="fed"), repo=repo, gateway=gw, settings=_settings())
    assert res.stale is False
    assert len(res.distributions) == 1
    assert res.distributions[0].outcomes[0].probability == Decimal("0.62")  # 0.62/1.00


def _multi_ref() -> MarketRef:
    return MarketRef(
        venue="polymarket", event_id="EVT", market_key="EVT", event_title="World Cup Winner",
        outcomes=["Spain", "France", "Brazil"],
        quoted_prices=[Decimal("0.40"), Decimal("0.35"), Decimal("0.25")],
        volume=Decimal("1000000"),
    )


async def test_multi_outcome_event_normalises_to_one() -> None:
    gw = FakeGateway(refs_by_topic={"world cup": [_multi_ref()]})
    res = await analyze(AnalyzeRequest(topic="world cup"), repo=InMemoryMarketRepository(),
                        gateway=gw, settings=_settings())
    assert len(res.distributions) == 1
    dist = res.distributions[0]
    assert [o.outcome for o in dist.outcomes] == ["Spain", "France", "Brazil"]
    total = sum(o.probability for o in dist.outcomes)
    assert total == Decimal(1)  # normalised across the 3 candidates (0.40+0.35+0.25=1.00)
    assert dist.outcomes[0].probability == Decimal("0.40")  # already summed to 1, factor 1


async def test_degrades_to_polymarket_only_with_note() -> None:
    gw = FakeGateway(refs_by_topic={"fed": [_poly_ref()]})  # no kalshi ref
    res = await analyze(AnalyzeRequest(topic="fed"), repo=InMemoryMarketRepository(),
                        gateway=gw, settings=_settings())
    avail = {a.venue: a for a in res.venue_availability}
    assert avail["polymarket"].matched is True
    assert avail["kalshi"].matched is False
    assert any("kalshi" in (n or "") for n in res.notes)


async def test_served_from_fresh_store_without_live() -> None:
    repo = InMemoryMarketRepository()
    await repo.upsert_observations([_obs("0.60")])  # updated_at = now (fresh)
    gw = FakeGateway()  # would return nothing if it went live
    res = await analyze(AnalyzeRequest(topic="fed"), repo=repo, gateway=gw, settings=_settings())
    assert res.stale is False
    assert len(res.distributions) == 1


async def test_stale_store_served_when_live_empty() -> None:
    repo = InMemoryMarketRepository()
    await repo.upsert_observations([_obs("0.60")])
    key = ("polymarket", "m1", "Yes")
    repo.store[key] = repo.store[key].model_copy(
        update={"updated_at": datetime.now(timezone.utc) - timedelta(hours=2)}
    )
    res = await analyze(AnalyzeRequest(topic="fed"), repo=repo, gateway=FakeGateway(),
                        settings=_settings())
    assert res.stale is True
    assert len(res.distributions) == 1


async def test_no_data_anywhere_returns_empty_with_note() -> None:
    res = await analyze(AnalyzeRequest(topic="ghost"), repo=InMemoryMarketRepository(),
                        gateway=FakeGateway(), settings=_settings())
    assert res.distributions == []
    assert res.notes
