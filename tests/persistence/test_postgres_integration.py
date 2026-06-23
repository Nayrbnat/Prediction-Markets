"""Optional integration test for the real asyncpg repository + schema.

Runs only when TEST_DATABASE_URL is set (and asyncpg is installed); skipped
otherwise so the default suite needs no database.
"""

from __future__ import annotations

import os
from decimal import Decimal
from importlib.resources import files

import pytest

from app.models.domain import MarketObservation

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set"
)


async def _setup_repo():
    import asyncpg

    from app.persistence.repository import PostgresMarketRepository

    pool = await asyncpg.create_pool(dsn=os.environ["TEST_DATABASE_URL"], min_size=1, max_size=2)
    sql = (files("app.persistence") / "schema.sql").read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS market_change_log, market_observations")
        await conn.execute(sql)
    return pool, PostgresMarketRepository(pool)


async def test_upsert_then_delta_roundtrip() -> None:
    pool, repo = await _setup_repo()
    try:
        obs = MarketObservation(
            venue="polymarket", market_key="0xabc", outcome="Yes", event_title="Fed",
            topic="fed", probability=Decimal("0.60"), raw_price=Decimal("0.60"),
        )
        await repo.upsert_observations([obs])
        await repo.upsert_observations([obs.model_copy(update={"probability": Decimal("0.62")})])
        stored = await repo.read_topic("fed")
        assert stored[0].previous_probability == Decimal("0.60")
        assert stored[0].probability_delta == Decimal("0.02")
    finally:
        await pool.close()
