"""Service tests for the ingestion run: upsert, change detection, idempotence."""

from __future__ import annotations

from decimal import Decimal

from app.config import Settings
from app.models.domain import MarketRef
from app.services.ingestion_service import run_ingestion
from tests.fakes import FakeGateway, InMemoryMarketRepository


def _settings() -> Settings:
    return Settings(database_url="", ingest_topics="fed", high_priority_topics="fed")


def _ref(yes: str, no: str) -> MarketRef:
    return MarketRef(
        venue="polymarket", event_id="E", market_key="m1", event_title="Fed decision",
        outcomes=["Yes", "No"], quoted_prices=[Decimal(yes), Decimal(no)], volume=Decimal("5000"),
        topic="fed",
    )


async def test_first_run_upserts_no_changes() -> None:
    repo = InMemoryMarketRepository()
    gw = FakeGateway(refs_by_topic={"fed": [_ref("0.62", "0.38")]})
    result = await run_ingestion(repo=repo, gateway=gw, settings=_settings())
    assert result.markets == 2
    assert result.changes == 0  # no previous observation yet
    assert len(repo.changelog) == 0


async def test_second_run_logs_material_change() -> None:
    repo = InMemoryMarketRepository()
    settings = _settings()
    gw = FakeGateway(refs_by_topic={"fed": [_ref("0.62", "0.38")]})
    await run_ingestion(repo=repo, gateway=gw, settings=settings)

    gw.refs["fed"] = [_ref("0.70", "0.30")]  # Yes +0.08, No -0.08 -> both material
    result = await run_ingestion(repo=repo, gateway=gw, settings=settings)
    assert result.changes == 2
    assert len(repo.changelog) == 2
    stored = {o.outcome: o for o in await repo.read_topic("fed")}
    assert stored["Yes"].probability == Decimal("0.70")
    assert stored["Yes"].previous_probability == Decimal("0.62")


async def test_untracked_topic_writes_no_changelog() -> None:
    repo = InMemoryMarketRepository()
    settings = Settings(database_url="", ingest_topics="fed", high_priority_topics="")
    gw = FakeGateway(refs_by_topic={"fed": [_ref("0.62", "0.38")]})
    await run_ingestion(repo=repo, gateway=gw, settings=settings)
    gw.refs["fed"] = [_ref("0.90", "0.10")]
    result = await run_ingestion(repo=repo, gateway=gw, settings=settings)
    assert result.changes == 0  # not tracked -> never logged
