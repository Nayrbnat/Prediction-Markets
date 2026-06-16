"""End-to-end /analyze through HTTP with all externals mocked, exercising the
Kalshi-no-match degradation path."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.domain import MarketRef
from tests.fakes import FakeGateway, InMemoryMarketRepository


def test_e2e_analyze_polymarket_only_degradation() -> None:
    app = create_app()
    poly = MarketRef(
        venue="polymarket", event_id="E", market_key="m1", event_title="Fed decision in March",
        outcomes=["Yes", "No"], quoted_prices=[Decimal("0.62"), Decimal("0.38")],
        volume=Decimal("5000"), topic="fed rate decision",
    )
    with TestClient(app) as client:
        # Gateway returns only a Polymarket market -> Kalshi must degrade gracefully.
        app.state.gateway = FakeGateway(refs_by_topic={"fed rate decision": [poly]})
        app.state.repo = InMemoryMarketRepository()
        resp = client.post("/analyze", json={"topic": "fed rate decision"})

    assert resp.status_code == 200
    body = resp.json()

    # quantitative payload present
    assert len(body["distributions"]) == 1
    dist = body["distributions"][0]
    assert dist["venue"] == "polymarket"
    assert [o["outcome"] for o in dist["outcomes"]] == ["Yes", "No"]

    # per-venue availability + degradation note
    avail = {a["venue"]: a for a in body["venue_availability"]}
    assert avail["polymarket"]["matched"] is True
    assert avail["kalshi"]["matched"] is False
    assert any("kalshi" in (n or "") for n in body["notes"])

    # v1 seams + provenance
    assert body["llm_synthesis"] is None
    assert body["disclaimer"]
    assert dist["outcomes"][0]["provenance"]["venue"] == "polymarket"
