"""HTTP-level tests for the API, with the gateway and repo faked on app.state."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.models.domain import MarketObservation, MarketRef
from tests.fakes import FakeGateway, InMemoryMarketRepository


def _poly_ref() -> MarketRef:
    return MarketRef(
        venue="polymarket", event_id="E", market_key="m1", event_title="Fed decision",
        outcomes=["Yes", "No"], quoted_prices=[Decimal("0.62"), Decimal("0.38")],
        volume=Decimal("5000"), topic="fed",
    )


def test_analyze_endpoint_live() -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.gateway = FakeGateway(refs_by_topic={"fed": [_poly_ref()]})
        app.state.repo = InMemoryMarketRepository()
        resp = client.post("/analyze", json={"topic": "fed"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stale"] is False
    assert len(body["distributions"]) == 1
    assert body["llm_synthesis"] is None
    assert body["disclaimer"]


def test_markets_search_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.gateway = FakeGateway(refs_by_topic={"fed": [_poly_ref()]})
        resp = client.get("/markets/search", params={"q": "fed"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_market_detail_endpoint() -> None:
    app = create_app()
    repo = InMemoryMarketRepository()
    asyncio.run(
        repo.upsert_observations(
            [
                MarketObservation(
                    venue="polymarket", market_key="m1", outcome="Yes",
                    event_title="Fed decision", topic="fed",
                    probability=Decimal("0.62"), raw_price=Decimal("0.62"),
                )
            ]
        )
    )
    with TestClient(app) as client:
        app.state.repo = repo
        resp = client.get("/markets/polymarket/m1")
    assert resp.status_code == 200
    assert resp.json()["market"]["market_key"] == "m1"


def test_market_detail_404_when_absent() -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.repo = InMemoryMarketRepository()
        resp = client.get("/markets/polymarket/missing")
    assert resp.status_code == 404


def test_internal_refresh_requires_auth() -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.repo = InMemoryMarketRepository()
        app.state.gateway = FakeGateway()
        unauth = client.get("/internal/refresh")
        ok = client.get(
            "/internal/refresh",
            headers={"Authorization": f"Bearer {get_settings().cron_secret}"},
        )
    assert unauth.status_code == 401
    assert ok.status_code == 200
    assert ok.json()["status"] == "ok"


def test_ui_smoke() -> None:
    """GET /ui/ should serve the verification frontend with an HTML content-type."""
    app = create_app()
    with TestClient(app, follow_redirects=True) as client:
        resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
