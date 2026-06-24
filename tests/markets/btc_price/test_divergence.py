"""Hand-checked tests for BTC threshold relative value (terminal-only parse + compare).

Reflects real live shapes (verified 2026-06-24): Polymarket carries strike+direction in
the market question (now the event_title); Kalshi carries them in the outcome label
("$X or above"). Only TERMINAL above-thresholds are compared; barrier/touch/downside are
skipped to stay apples-to-apples with Deribit's terminal P(S_T>K).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.markets.btc_price.divergence import _parse_strike, compare, parse
from app.models.domain import MarketObservation

DEC = datetime(2026, 12, 26, tzinfo=timezone.utc)


def _obs(
    venue: str, market_key: str, outcome: str, prob: str, *, title: str, close: datetime = DEC
) -> MarketObservation:
    return MarketObservation(
        venue=venue, market_key=market_key, outcome=outcome, event_title=title,
        probability=Decimal(prob), raw_price=Decimal(prob), close_date=close,
    )


def test_parse_strike_dollar_and_suffix_forms() -> None:
    assert _parse_strike("$150,000") == Decimal(150_000)
    assert _parse_strike("150k") == Decimal(150_000)
    assert _parse_strike("$150K") == Decimal(150_000)
    assert _parse_strike("$2.5M") == Decimal(2_500_000)


def test_parse_strike_ignores_bare_year() -> None:
    assert _parse_strike("by December 31, 2026") is None


def test_deribit_row_parsed_from_market_key() -> None:
    obs = _obs("deribit", "BTC-26DEC26-150000", "BTC ≥ $150,000", "0.30",
               title="BTC ≥ $150,000 by 26DEC26")
    point = parse(obs)
    assert point is not None
    assert point.venue == "deribit"
    assert point.strike == Decimal(150_000)
    assert point.prob_above == Decimal("0.30")


def test_deribit_below_row_is_dropped() -> None:
    obs = _obs("deribit", "BTC-26DEC26-150000", "BTC < $150,000", "0.70",
               title="BTC ≥ $150,000 by 26DEC26")
    assert parse(obs) is None


def test_polymarket_terminal_above_parsed_from_question() -> None:
    # Real Polymarket shape: child question is the event_title, outcome "Yes".
    obs = _obs("polymarket", "0xabc", "Yes", "0.45",
               title="Will the price of Bitcoin be above $150,000 on December 26?")
    point = parse(obs)
    assert point is not None
    assert point.venue == "polymarket"
    assert point.strike == Decimal(150_000)
    assert point.prob_above == Decimal("0.45")


def test_kalshi_or_above_parsed_from_outcome() -> None:
    # Real Kalshi shape: yes_sub_title is the outcome, event title is the date.
    obs = _obs("kalshi", "KXBTCD-26DEC2617-T149999", "$150,000 or above", "0.40",
               title="BTC price on Dec 26, 2026?")
    point = parse(obs)
    assert point is not None
    assert point.strike == Decimal(150_000)
    assert point.prob_above == Decimal("0.40")


def test_barrier_reach_market_skipped() -> None:
    # "reach" = touch/barrier, not terminal -> skipped (§13).
    obs = _obs("polymarket", "0xr", "Yes", "0.55",
               title="Will Bitcoin reach $150,000 in December?")
    assert parse(obs) is None


def test_downside_dip_market_skipped() -> None:
    obs = _obs("polymarket", "0xd", "Yes", "0.20",
               title="Will Bitcoin dip to $80,000 in December?")
    assert parse(obs) is None


def test_no_outcome_dropped() -> None:
    obs = _obs("polymarket", "0xabc", "No", "0.55",
               title="Will the price of Bitcoin be above $150,000 on December 26?")
    assert parse(obs) is None


def test_non_btc_market_dropped() -> None:
    obs = _obs("polymarket", "0xeth", "Yes", "0.40",
               title="Will Ethereum be above $5,000 on December 26?")
    assert parse(obs) is None


def test_compare_emits_signed_gap_market_minus_derivative() -> None:
    obs = [
        _obs("deribit", "BTC-26DEC26-150000", "BTC ≥ $150,000", "0.30",
             title="BTC ≥ $150,000 by 26DEC26"),
        _obs("polymarket", "0xabc", "Yes", "0.45",
             title="Will the price of Bitcoin be above $150,000 on December 26?"),
    ]
    items = compare(obs, gap_threshold=Decimal("0.05"))
    assert len(items) == 1
    it = items[0]
    assert it.market_venue == "polymarket"
    assert it.strike == Decimal(150_000)
    assert it.market_prob == Decimal("0.45")
    assert it.derivative_prob == Decimal("0.30")
    assert it.gap == Decimal("0.15")  # 0.45 - 0.30
    assert it.material is True


def test_compare_snaps_near_strikes_to_same_grid() -> None:
    obs = [
        _obs("deribit", "BTC-26DEC26-150000", "BTC ≥ $150,000", "0.30",
             title="BTC ≥ $150,000 by 26DEC26"),
        _obs("kalshi", "kx", "$149,950 or above", "0.31", title="BTC price on Dec 26, 2026?"),
    ]
    items = compare(obs, gap_threshold=Decimal("0.05"))
    assert len(items) == 1
    assert items[0].material is False  # |0.31 - 0.30| = 0.01 < 0.05


def test_compare_no_derivative_yields_nothing() -> None:
    obs = [
        _obs("polymarket", "0xabc", "Yes", "0.45",
             title="Will the price of Bitcoin be above $150,000 on December 26?"),
    ]
    assert compare(obs, gap_threshold=Decimal("0.05")) == []
