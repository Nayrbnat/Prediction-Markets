"""Tests for dynamic-targeting helpers (deribit_token, targets_from_refs)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.markets._shared.threshold_parse import deribit_token, targets_from_refs
from app.models.domain import MarketRef


def test_deribit_token_format() -> None:
    assert deribit_token(datetime(2026, 6, 26, tzinfo=timezone.utc)) == "26JUN26"
    assert deribit_token(datetime(2026, 8, 1, tzinfo=timezone.utc)) == "1AUG26"  # no leading 0
    assert deribit_token(datetime(2026, 12, 5, tzinfo=timezone.utc)) == "5DEC26"


def _ref(venue: str, title: str, outcomes: list[str], close: datetime | None) -> MarketRef:
    return MarketRef(
        venue=venue, event_id="e", market_key="k", event_title=title,
        outcomes=outcomes, close_date=close,
    )


JUN24 = datetime(2026, 6, 24, tzinfo=timezone.utc)
JUN26 = datetime(2026, 6, 26, tzinfo=timezone.utc)


def test_targets_from_refs_derives_terminal_above_only() -> None:
    refs = [
        # Polymarket terminal above -> (60000, 24JUN26)
        _ref("polymarket", "Will the price of Bitcoin be above $60,000 on June 24?",
             ["Yes", "No"], JUN24),
        # Polymarket barrier ("reach") -> skipped
        _ref("polymarket", "Will Bitcoin reach $90,000 in June?", ["Yes", "No"], JUN26),
        # Kalshi terminal "$X or above" -> (62000, 26JUN26)
        _ref("kalshi", "BTC price on Jun 26, 2026?", ["$62,000 or above", "No"], JUN26),
        # Duplicate of the Kalshi one -> deduped
        _ref("kalshi", "BTC price on Jun 26, 2026?", ["$62,000 or above", "No"], JUN26),
        # Non-BTC -> skipped
        _ref("polymarket", "Will the S&P be above 7000 on June 24?", ["Yes", "No"], JUN24),
        # A derivative (deribit) ref is ignored (not a prediction venue)
        _ref("deribit", "BTC ≥ $60,000 by 24JUN26", ["BTC ≥ $60,000", "BTC < $60,000"], JUN24),
    ]
    targets = targets_from_refs(refs, underlying="BTC", aliases=("btc", "bitcoin"))
    assert targets == [(Decimal(60000), "24JUN26"), (Decimal(62000), "26JUN26")]


def test_targets_from_refs_skips_no_date_and_no_outcome() -> None:
    refs = [
        _ref("kalshi", "BTC price on Jun 26?", ["$60,000 or above", "No"], None),  # no date
        _ref("polymarket", "Will Bitcoin be above $60,000 on June 24?", ["No"], JUN24),  # no aff.
    ]
    assert targets_from_refs(refs, underlying="BTC", aliases=("btc", "bitcoin")) == []
