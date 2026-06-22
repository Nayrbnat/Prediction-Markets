"""Integration test against a real Postgres database.

Skipped automatically when ``TEST_DATABASE_URL`` is not set.
Run with::

    TEST_DATABASE_URL=postgresql://... pytest tests/persistence/test_postgres_integration.py -v
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from importlib.resources import files

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


@pytest.fixture()
async def pool():
    url = os.environ["TEST_DATABASE_URL"]
    p = await asyncpg.create_pool(url)
    yield p
    await p.close()


_TRUNCATE = (
    "TRUNCATE market_snapshots, market_topics, ingestion_runs RESTART IDENTITY CASCADE"
)


@pytest.fixture()
async def fresh_schema(pool):
    """Apply schema DDL then truncate before AND after, leaving DB pristine."""
    sql = (files("app.persistence") / "schema.sql").read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(sql)
        await conn.execute(_TRUNCATE)
    yield pool
    async with pool.acquire() as conn:
        await conn.execute(_TRUNCATE)


async def test_snapshot_roundtrip(fresh_schema) -> None:
    """Two snapshots on consecutive dates → history has 2 points; latest shows most recent."""
    pool = fresh_schema
    today = date.today()
    yesterday = today - timedelta(days=1)

    async with pool.acquire() as conn:
        # Insert run
        row = await conn.fetchrow(
            "INSERT INTO ingestion_runs (snapshot_date) VALUES ($1) RETURNING run_id", today
        )
        run_id = row["run_id"]

        # Insert yesterday snapshot
        await conn.execute(
            """INSERT INTO market_snapshots
               (snapshot_date, venue, market_key, outcome, event_title,
                probability, raw_price, observed_at, run_id)
               VALUES ($1, 'polymarket', 'test-key', 'Yes', 'Test Event',
                       0.60, 0.60, now(), $2)""",
            yesterday, run_id,
        )
        # Insert today snapshot
        await conn.execute(
            """INSERT INTO market_snapshots
               (snapshot_date, venue, market_key, outcome, event_title,
                probability, raw_price, observed_at, run_id)
               VALUES ($1, 'polymarket', 'test-key', 'Yes', 'Test Event',
                       0.62, 0.62, now(), $2)
               ON CONFLICT (snapshot_date, venue, market_key, outcome) DO UPDATE
               SET probability = EXCLUDED.probability""",
            today, run_id,
        )
        # Refresh matview
        await conn.execute("REFRESH MATERIALIZED VIEW market_latest")

        # Check history
        history = await conn.fetch(
            "SELECT snapshot_date, probability FROM market_snapshots "
            "WHERE venue='polymarket' AND market_key='test-key' AND outcome='Yes' "
            "ORDER BY snapshot_date DESC"
        )
        assert len(history) == 2
        assert Decimal(str(history[0]["probability"])) == Decimal("0.62")
        assert Decimal(str(history[1]["probability"])) == Decimal("0.60")

        # Check latest
        latest = await conn.fetchrow(
            "SELECT probability FROM market_latest "
            "WHERE venue='polymarket' AND market_key='test-key' AND outcome='Yes'"
        )
        assert latest is not None
        assert Decimal(str(latest["probability"])) == Decimal("0.62")
