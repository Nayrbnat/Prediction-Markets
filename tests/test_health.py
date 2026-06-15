"""Phase 0 smoke test: the app boots and /health reports degraded (no DB) cleanly."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_boots_and_reports_db_state() -> None:
    app = create_app()
    with TestClient(app) as client:  # runs lifespan; no DATABASE_URL -> pool is None
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] is False
    assert body["status"] == "degraded"
    assert "x-request-id" in {k.lower(): v for k, v in resp.headers.items()}


def test_openapi_renders() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert "/health" in resp.json()["paths"]
