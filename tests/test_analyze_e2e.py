"""End-to-end /analyze through HTTP with the repo seeded, exercising the
Kalshi-no-match degradation path (store-only read path).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.domain import MarketObservation
from tests.fakes import FakeGateway, InMemoryMarketRepository


def test_e2e_analyze_polymarket_only_degradation() -> None:
    """Store has only Polymarket data -> Kalshi shows as unmatched, degradation note present."""
    app = create_app()
    repo = InMemoryMarketRepository()
    asyncio.run(
        repo.seed(
            [
                MarketObservation(
                    venue="polymarket", market_key="m1", outcome="Yes",
                    event_title="Fed decision in March", topic="fed rate decision",
                    probability=Decimal("0.62"), raw_price=Decimal("0.62"),
                    volume_24h=Decimal("5000"),
                ),
                MarketObservation(
                    venue="polymarket", market_key="m1", outcome="No",
                    event_title="Fed decision in March", topic="fed rate decision",
                    probability=Decimal("0.38"), raw_price=Decimal("0.38"),
                    volume_24h=Decimal("5000"),
                ),
            ]
        )
    )

    with TestClient(app) as client:
        app.state.gateway = FakeGateway()
        app.state.repo = repo
        resp = client.post("/analyze", json={"topic": "fed rate decision"})

    assert resp.status_code == 200
    body = resp.json()

    # quantitative payload present
    assert len(body["distributions"]) == 1
    dist = body["distributions"][0]
    assert dist["venue"] == "polymarket"
    assert [o["outcome"] for o in dist["outcomes"]] == ["Yes", "No"]

    # per-venue availability: Polymarket matched, Kalshi not (no data in store)
    avail = {a["venue"]: a for a in body["venue_availability"]}
    assert avail["polymarket"]["matched"] is True
    assert avail["kalshi"]["matched"] is False

    # v1 seams + provenance
    assert body["llm_synthesis"] is None
    assert body["disclaimer"]
    assert dist["outcomes"][0]["provenance"]["venue"] == "polymarket"
