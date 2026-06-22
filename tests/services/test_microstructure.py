"""Tests asserting per-outcome microstructure fields (bid/ask/spread/volume_24h/
volume_total/last_trade/open_interest) thread correctly from MarketRef → distribution
→ MarketObservation.  These are new fields added in the Tier-2 pit-remodel.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.analysis.probability import q6
from app.config import Settings
from app.models.domain import MarketRef
from app.services.pricing import (
    _get,
    _spread,
    build_distribution,
    observations_from_distribution,
)
from tests.fakes import FakeGateway

# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------


def test_spread_both_sides_present() -> None:
    s = _spread(Decimal("0.58"), Decimal("0.62"))
    assert s == Decimal("0.04").quantize(Decimal("0.000001"))


def test_spread_missing_bid_returns_none() -> None:
    assert _spread(None, Decimal("0.62")) is None


def test_spread_missing_ask_returns_none() -> None:
    assert _spread(Decimal("0.58"), None) is None


def test_get_safe_index() -> None:
    arr = [Decimal("0.1"), None, Decimal("0.3")]
    assert _get(arr, 0) == Decimal("0.1")
    assert _get(arr, 1) is None
    assert _get(arr, 2) == Decimal("0.3")
    # out-of-bounds → None, not IndexError
    assert _get(arr, 5) is None
    # None array → None
    assert _get(None, 0) is None


# ---------------------------------------------------------------------------
# Integration: ref → distribution → observation round-trip
# ---------------------------------------------------------------------------


def _settings() -> Settings:
    return Settings(database_url="", ingest_topics="test", high_priority_topics="")


def _ref_with_microstructure() -> MarketRef:
    """Binary Yes/No ref with microstructure arrays populated (simulating Kalshi binary)."""
    return MarketRef(
        venue="kalshi",
        event_id="KXTEST",
        market_key="KXTEST-A",
        event_title="Test question",
        outcomes=["Yes", "No"],
        quoted_prices=[Decimal("0.60"), Decimal("0.40")],
        volume=Decimal("2000"),
        liquidity=Decimal("500"),
        close_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        # Per-outcome arrays:
        best_bids=[Decimal("0.58"), Decimal("0.38")],
        best_asks=[Decimal("0.62"), Decimal("0.42")],
        last_trades=[Decimal("0.60"), None],  # last_price_dollars is Yes-side only
        outcome_volumes_24h=[Decimal("100"), Decimal("100")],
        outcome_volumes_total=[Decimal("2000"), Decimal("2000")],
        open_interests=[Decimal("50"), Decimal("50")],
    )


@pytest.mark.asyncio
async def test_build_distribution_attaches_microstructure() -> None:
    """build_distribution threads per-outcome fields onto each OutcomeProbability."""
    ref = _ref_with_microstructure()
    gw = FakeGateway()  # no CLOB books → uses quoted_prices
    dist = await build_distribution(gw, ref, _settings())
    assert dist is not None
    assert len(dist.outcomes) == 2

    yes = dist.outcomes[0]
    no = dist.outcomes[1]

    # Yes side: full bid/ask/spread/last
    assert yes.best_bid == q6(Decimal("0.58"))
    assert yes.best_ask == q6(Decimal("0.62"))
    assert yes.spread == q6(Decimal("0.04"))  # ask - bid
    assert yes.last_trade_price == q6(Decimal("0.60"))

    # No side: bid/ask/spread present, last is NULL (not fabricated)
    assert no.best_bid == q6(Decimal("0.38"))
    assert no.best_ask == q6(Decimal("0.42"))
    assert no.spread == q6(Decimal("0.04"))
    assert no.last_trade_price is None  # NULL, not 0

    # Volume fields are distinct from each other (both outcomes have the same
    # market-level value in this fixture, but 24h ≠ total)
    assert yes.volume_24h == Decimal("100")
    assert yes.volume_total == Decimal("2000")
    assert yes.volume_24h != yes.volume_total

    # Open interest
    assert yes.open_interest == Decimal("50")


@pytest.mark.asyncio
async def test_observations_from_distribution_maps_new_fields() -> None:
    """observations_from_distribution carries microstructure through to MarketObservation."""
    ref = _ref_with_microstructure()
    gw = FakeGateway()
    dist = await build_distribution(gw, ref, _settings())
    assert dist is not None

    obs = observations_from_distribution(
        dist, ref, priority="normal", tracked=False, category="test"
    )
    assert len(obs) == 2

    yes_obs = obs[0]
    no_obs = obs[1]

    # close_date propagated from ref
    assert yes_obs.close_date == datetime(2026, 12, 31, tzinfo=timezone.utc)

    # bid/ask/spread carried through
    assert yes_obs.best_bid is not None
    assert yes_obs.best_ask is not None
    assert yes_obs.spread is not None
    assert yes_obs.spread == yes_obs.best_ask - yes_obs.best_bid  # spread = ask - bid

    # last_trade_price: Yes has it, No is NULL
    assert yes_obs.last_trade_price is not None
    assert no_obs.last_trade_price is None

    # volume_24h and volume_total are distinct
    assert yes_obs.volume_24h is not None
    assert yes_obs.volume_total is not None
    assert yes_obs.volume_24h != yes_obs.volume_total

    # open_interest carried through
    assert yes_obs.open_interest == Decimal("50")


@pytest.mark.asyncio
async def test_null_bid_ask_means_null_spread() -> None:
    """When one side of the book is absent, spread is NULL — never fabricated."""
    ref = MarketRef(
        venue="polymarket",
        event_id="E1",
        market_key="0xabc",
        event_title="Fed",
        outcomes=["Yes", "No"],
        quoted_prices=[Decimal("0.62"), Decimal("0.38")],
        # Only Yes bid present; ask absent → spread must be NULL for Yes
        best_bids=[Decimal("0.60"), None],
        best_asks=[None, None],
        last_trades=[None, None],
        outcome_volumes_24h=[Decimal("500"), Decimal("500")],
        outcome_volumes_total=[Decimal("5000"), Decimal("5000")],
        open_interests=[None, None],
    )
    gw = FakeGateway()
    dist = await build_distribution(gw, ref, _settings())
    assert dist is not None
    yes = dist.outcomes[0]
    assert yes.best_bid == q6(Decimal("0.60"))
    assert yes.best_ask is None
    assert yes.spread is None  # NULL, not fabricated
