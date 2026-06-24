"""respx-mocked tests for the company-bet scan (discovery/listing only)."""

from __future__ import annotations

from datetime import date

import respx
from httpx import Response

from app.config import Settings
from app.core.http import make_client
from app.services import company_scan

KALSHI = "https://kalshi.test"
GAMMA = "https://gamma.test"
TODAY = date(2026, 6, 24)

_KALSHI_SERIES = {
    "series": [
        {"ticker": "JPMCEOCHANGE", "title": "Jamie Dimon leaves JPMorgan"},
        {"ticker": "TESLA", "title": "Tesla deliveries"},
        {"ticker": "KXMSTRCAP", "title": "MicroStrategy market cap above $100B"},
    ]
}
_GAMMA_NVDA = {
    "events": [
        {
            "id": "E1", "title": "What will NVDA hit week of June 22?", "negRisk": False,
            "markets": [
                {"conditionId": "0xn", "outcomes": '["Yes","No"]',
                 "outcomePrices": '["0.3","0.7"]', "closed": False,
                 "question": "Will NVDA hit $236 week of June 22?"}
            ],
        }
    ]
}


def _settings() -> Settings:
    return Settings(
        database_url="", company_scan_enabled=True, company_kalshi_categories="Companies",
        company_names="Nvidia", company_scan_limit=150,
    )


async def test_scan_lists_kalshi_and_polymarket_bets() -> None:
    async with respx.mock:
        respx.get(f"{KALSHI}/series").mock(return_value=Response(200, json=_KALSHI_SERIES))
        respx.get(f"{GAMMA}/public-search").mock(return_value=Response(200, json=_GAMMA_NVDA))
        async with make_client(base_url=GAMMA) as g, make_client(base_url=KALSHI) as k:
            result = await company_scan.scan(g, k, settings=_settings(), generated_for=TODAY)

    assert result.count == 4
    assert result.kalshi_count == 3
    assert result.polymarket_count == 1
    titles = {b.title for b in result.bets}
    assert "Tesla deliveries" in titles
    assert "Will NVDA hit $236 week of June 22?" in titles  # PM uses child question
    kinds = {b.title: b.kind for b in result.bets}
    assert kinds["Tesla deliveries"] == "kpi-or-event"
    assert kinds["MicroStrategy market cap above $100B"] == "price"  # has "$" + "market cap"
    assert kinds["Will NVDA hit $236 week of June 22?"] == "price"  # has "$"


async def test_scan_dedupes_and_degrades_on_kalshi_failure() -> None:
    async with respx.mock:
        respx.get(f"{KALSHI}/series").mock(return_value=Response(500))  # Kalshi fails
        respx.get(f"{GAMMA}/public-search").mock(return_value=Response(200, json=_GAMMA_NVDA))
        async with make_client(base_url=GAMMA) as g, make_client(base_url=KALSHI) as k:
            result = await company_scan.scan(g, k, settings=_settings(), generated_for=TODAY)
    # Kalshi degraded to nothing; Polymarket still listed (graceful).
    assert result.kalshi_count == 0
    assert result.polymarket_count == 1


def test_render_company_scan_lists_titles() -> None:
    from app.models.company import CompanyBet, CompanyScan
    result = CompanyScan(
        generated_for=TODAY,
        bets=[
            CompanyBet(venue="kalshi", source_key="TESLA", title="Tesla deliveries"),
            CompanyBet(venue="polymarket", source_key="0xn",
                       title="Will NVDA hit $236?", kind="price"),
        ],
        count=2, kalshi_count=1, polymarket_count=1,
    )
    subject, html, text = company_scan.render_company_scan(result)
    assert "2 available" in subject
    assert "Tesla deliveries" in text and "Tesla deliveries" in html
    assert "Will NVDA hit $236?" in text
