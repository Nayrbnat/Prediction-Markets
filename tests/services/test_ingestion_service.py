"""Service tests for the ingestion run: write_snapshots, topic mapping, idempotence."""

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


async def test_first_run_writes_snapshots() -> None:
    repo = InMemoryMarketRepository()
    gw = FakeGateway(refs_by_topic={"fed": [_ref("0.62", "0.38")]})
    result = await run_ingestion(repo=repo, gateway=gw, settings=_settings())
    assert result.markets == 2  # 2 outcomes deduped and written
    assert result.changes == 0
    # Snapshots written
    assert len(repo.snapshots) == 2
    # Run recorded as ok
    assert repo.runs[-1]["status"] == "ok"
    assert repo.refresh_count == 1


async def test_same_day_rerun_is_idempotent() -> None:
    """Running ingestion twice on the same day yields the same snapshot count."""
    repo = InMemoryMarketRepository()
    settings = _settings()
    gw = FakeGateway(refs_by_topic={"fed": [_ref("0.62", "0.38")]})
    await run_ingestion(repo=repo, gateway=gw, settings=settings)
    snapshot_count_after_first = len(repo.snapshots)

    # Same price — idempotent
    await run_ingestion(repo=repo, gateway=gw, settings=settings)
    assert len(repo.snapshots) == snapshot_count_after_first


async def test_topic_pairs_written() -> None:
    """Topic-to-market mappings are persisted after ingestion."""
    repo = InMemoryMarketRepository()
    gw = FakeGateway(refs_by_topic={"fed": [_ref("0.62", "0.38")]})
    await run_ingestion(repo=repo, gateway=gw, settings=_settings())
    # (polymarket, m1, fed) should be in topic_pairs
    assert ("polymarket", "m1", "fed") in repo.topic_pairs
