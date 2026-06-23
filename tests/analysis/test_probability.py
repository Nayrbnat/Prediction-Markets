"""Hand-checked tests for the probability layer."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.analysis.probability import assess_confidence, implied_probability, mid_price
from app.models.domain import OrderBookTop
from app.models.provenance import Provenance

THIN_SPREAD = Decimal("0.05")
THIN_VOLUME = Decimal("1000")


def _book(bid: str | None, ask: str | None, spread: str | None = None) -> OrderBookTop:
    return OrderBookTop(
        best_bid=Decimal(bid) if bid else None,
        best_ask=Decimal(ask) if ask else None,
        spread=Decimal(spread) if spread else None,
        observed_at=datetime.now(timezone.utc),
    )


def _prov() -> Provenance:
    return Provenance(
        venue="polymarket", endpoint="/book", raw_value="x", observed_at=datetime.now(timezone.utc)
    )


def test_mid_price_is_bid_ask_midpoint() -> None:
    # (0.60 + 0.64) / 2 = 0.62  -- hand-checked
    assert mid_price(_book("0.60", "0.64")) == Decimal("0.62")


def test_mid_price_none_when_no_quotes() -> None:
    assert mid_price(_book(None, None)) is None


def test_confidence_ok_when_tight_and_liquid() -> None:
    flag = assess_confidence(
        spread=Decimal("0.02"), volume=Decimal("5000"),
        thin_spread=THIN_SPREAD, thin_volume=THIN_VOLUME,
    )
    assert flag.level == "ok"
    assert flag.reasons == []


def test_confidence_thin_on_wide_spread_and_low_volume() -> None:
    flag = assess_confidence(
        spread=Decimal("0.10"), volume=Decimal("500"),
        thin_spread=THIN_SPREAD, thin_volume=THIN_VOLUME,
    )
    assert flag.level == "thin"
    assert len(flag.reasons) == 2  # both spread and volume tripped


def test_confidence_boundary_not_thin_at_threshold() -> None:
    # spread exactly == threshold is NOT thin (strictly greater required)
    flag = assess_confidence(
        spread=Decimal("0.05"), volume=Decimal("1000"),
        thin_spread=THIN_SPREAD, thin_volume=THIN_VOLUME,
    )
    assert flag.level == "ok"


def test_implied_probability_builds_outcome() -> None:
    op = implied_probability(
        outcome="Yes", book=_book("0.60", "0.64", spread="0.04"), provenance=_prov(),
        thin_spread=THIN_SPREAD, thin_volume=THIN_VOLUME, volume=Decimal("5000"),
    )
    assert op is not None
    assert op.probability == Decimal("0.62")
    assert op.confidence.level == "ok"


def test_implied_probability_none_without_mid() -> None:
    op = implied_probability(
        outcome="Yes", book=_book(None, None), provenance=_prov(),
        thin_spread=THIN_SPREAD, thin_volume=THIN_VOLUME,
    )
    assert op is None
