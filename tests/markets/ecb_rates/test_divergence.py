"""Hand-checked tests for the ECB relative-value (market vs €STR futures) comparator."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.markets.ecb_rates.divergence import canonical_outcome, compare
from app.models.domain import MarketObservation

SEP = datetime(2026, 9, 16, tzinfo=timezone.utc)


def _obs(
    venue: str, market_key: str, outcome: str, prob: str, close: datetime
) -> MarketObservation:
    return MarketObservation(
        venue=venue,
        market_key=market_key,
        outcome=outcome,
        event_title="ECB decision",
        probability=Decimal(prob),
        raw_price=Decimal(prob),
        close_date=close,
    )


def test_canonical_outcome_mapping_across_venues() -> None:
    assert canonical_outcome("ECB maintains rate") == "No change"
    assert canonical_outcome("ECB cuts 25bps") == "25 bps cut"
    assert canonical_outcome("25 bps increase") == "25 bps hike"  # Polymarket
    assert canonical_outcome("No change") == "No change"  # €STR / Polymarket
    assert canonical_outcome("totally unknown") is None


def test_gap_computed_market_minus_futures() -> None:
    obs = [
        _obs("estr", "ECB-2026-09-16", "No change", "0.81", SEP),
        _obs("polymarket", "pm-sep", "No change", "0.74", SEP),
    ]
    items = compare(obs, gap_threshold=Decimal("0.05"))
    assert len(items) == 1
    it = items[0]
    assert it.market_venue == "polymarket"
    assert it.meeting == "September 2026"
    assert it.market_prob == Decimal("0.74")
    assert it.futures_prob == Decimal("0.81")
    assert it.gap == Decimal("-0.07")  # 0.74 - 0.81
    assert it.material is True  # |0.07| >= 0.05
