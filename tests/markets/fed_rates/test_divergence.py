"""Hand-checked tests for the relative-value (market vs futures) comparator."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.markets.fed_rates.divergence import canonical_outcome, compare, cut_hold_raise
from app.models.domain import MarketObservation

SEP = datetime(2026, 9, 16, tzinfo=timezone.utc)


def _obs(
    venue: str, market_key: str, outcome: str, prob: str, close: datetime
) -> MarketObservation:
    return MarketObservation(
        venue=venue,
        market_key=market_key,
        outcome=outcome,
        event_title="Fed decision",
        probability=Decimal(prob),
        raw_price=Decimal(prob),
        close_date=close,
    )


def test_canonical_outcome_mapping_across_venues() -> None:
    assert canonical_outcome("Fed maintains rate") == "No change"  # Kalshi
    assert canonical_outcome("25 bps increase") == "25 bps hike"  # Polymarket
    assert canonical_outcome("Cut >25bps") == "50+ bps cut"  # Kalshi
    assert canonical_outcome("No change") == "No change"  # CME / Polymarket
    assert canonical_outcome("totally unknown") is None


def test_cut_hold_raise_collapses_buckets() -> None:
    pairs = [
        ("25 bps decrease", Decimal("0.05")),  # Polymarket cut
        ("50+ bps decrease", Decimal("0.02")),  # Polymarket big cut
        ("No change", Decimal("0.73")),
        ("25 bps increase", Decimal("0.18")),  # Polymarket hike
        ("50+ bps increase", Decimal("0.02")),  # Polymarket big hike
    ]
    result = cut_hold_raise(pairs)
    assert result is not None
    cut, hold, hike = result
    assert cut == Decimal("0.07")  # 0.05 + 0.02
    assert hold == Decimal("0.73")
    assert hike == Decimal("0.20")  # 0.18 + 0.02


def test_cut_hold_raise_returns_none_for_unmapped_market() -> None:
    # A "number of dissents" market has no Fed cut/hold/raise outcomes.
    assert cut_hold_raise([("0", Decimal("0.6")), ("1", Decimal("0.4"))]) is None


def test_gap_computed_market_minus_futures() -> None:
    obs = [
        _obs("cme", "FOMC-2026-09-16", "No change", "0.81", SEP),
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


def test_sub_threshold_gap_not_material() -> None:
    obs = [
        _obs("cme", "FOMC-2026-09-16", "No change", "0.80", SEP),
        _obs("kalshi", "k-sep", "Fed maintains rate", "0.83", SEP),
    ]
    items = compare(obs, gap_threshold=Decimal("0.05"))
    assert len(items) == 1
    assert items[0].gap == Decimal("0.03")
    assert items[0].material is False


def test_no_futures_for_meeting_yields_no_items() -> None:
    # Only prediction-market data, no cme -> nothing to compare.
    obs = [
        _obs("polymarket", "pm-sep", "No change", "0.74", SEP),
        _obs("kalshi", "k-sep", "Fed maintains rate", "0.72", SEP),
    ]
    assert compare(obs, gap_threshold=Decimal("0.05")) == []


def test_meeting_matched_by_close_date_month_across_venues() -> None:
    # CME close 09-16, Kalshi close 09-17 -> same (2026, 9) meeting, compared.
    sep16 = datetime(2026, 9, 16, tzinfo=timezone.utc)
    sep17 = datetime(2026, 9, 17, tzinfo=timezone.utc)
    obs = [
        _obs("cme", "FOMC-2026-09-16", "25 bps cut", "0.30", sep16),
        _obs("kalshi", "k-sep", "Cut 25bps", "0.45", sep17),
    ]
    items = compare(obs, gap_threshold=Decimal("0.05"))
    assert len(items) == 1
    assert items[0].outcome == "25 bps cut"
    assert items[0].gap == Decimal("0.15")


def test_multiple_venues_and_outcomes_sorted_by_gap() -> None:
    obs = [
        _obs("cme", "FOMC-2026-09-16", "No change", "0.80", SEP),
        _obs("cme", "FOMC-2026-09-16", "25 bps cut", "0.20", SEP),
        _obs("polymarket", "pm-sep", "No change", "0.78", SEP),  # gap -0.02
        _obs("polymarket", "pm-sep", "25 bps cut", "0.05", SEP),  # gap -0.15
    ]
    items = compare(obs, gap_threshold=Decimal("0.05"))
    assert len(items) == 2
    # Largest |gap| first within the meeting.
    assert items[0].outcome == "25 bps cut"
    assert abs(items[0].gap) > abs(items[1].gap)
