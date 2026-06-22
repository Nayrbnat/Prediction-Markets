"""GET /markets/search, /markets/{venue}/{id}, /markets/history."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.core.errors import NotFoundError, PersistenceError
from app.models.domain import MarketRef
from app.models.provenance import Venue
from app.models.responses import HistoryPoint, MarketDetail
from app.services import pricing

router = APIRouter()


def _require_repo(request: Request):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise PersistenceError("no database configured")
    return repo


@router.get("/markets/search", response_model=list[MarketRef], tags=["markets"])
async def search(
    request: Request,
    q: str = Query(min_length=1),
    venue: Venue | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MarketRef]:
    repo = _require_repo(request)
    observations = await repo.search_markets(q, venue, limit)
    # Group by (venue, market_key) and return one MarketRef per market.
    groups: dict[tuple[str, str], list] = {}
    for obs in observations:
        groups.setdefault((obs.venue, obs.market_key), []).append(obs)
    return [pricing.ref_from_observations(grp) for grp in groups.values()]


@router.get("/markets/history", response_model=list[HistoryPoint], tags=["markets"])
async def history(
    request: Request,
    venue: Venue,
    market_key: str,
    outcome: str = "Yes",
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[HistoryPoint]:
    repo = _require_repo(request)
    return await repo.history(venue, market_key, outcome, limit)


@router.get("/markets/{venue}/{market_key}", response_model=MarketDetail, tags=["markets"])
async def market_detail(request: Request, venue: Venue, market_key: str) -> MarketDetail:
    repo = _require_repo(request)
    observations = await repo.read_market(venue, market_key)
    if not observations:
        raise NotFoundError(f"no stored market {venue}/{market_key}")
    return MarketDetail(
        market=pricing.ref_from_observations(observations),
        distribution=pricing.distribution_from_observations(observations),
        stale=True,
    )
