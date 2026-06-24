"""Hand-checked tests for BTC threshold relative value (parsing + comparison)."""

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
        venue=venue,
        market_key=market_key,
        outcome=outcome,
        event_title=title,
        probability=Decimal(prob),
        raw_price=Decimal(prob),
        close_date=close,
    )


def test_parse_strike_dollar_and_suffix_forms() -> None:
    assert _parse_strike("$150,000") == Decimal(150_000)
    assert _parse_strike("150k") == Decimal(150_000)
    assert _parse_strike("$150K") == Decimal(150_000)
    assert _parse_strike("$2.5M") == Decimal(2_500_000)


def test_parse_strike_ignores_bare_year() -> None:
    # "by December 31, 2026" has no $ and no k/M suffix -> not mistaken for a strike.
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


def test_prediction_yes_row_parsed_from_title() -> None:
    obs = _obs("polymarket", "pm-btc-150k", "Yes", "0.45",
               title="Will Bitcoin reach $150,000 by December 2026?")
    point = parse(obs)
    assert point is not None
    assert point.venue == "polymarket"
    assert point.strike == Decimal(150_000)
    assert point.prob_above == Decimal("0.45")


def test_prediction_no_row_is_dropped() -> None:
    obs = _obs("polymarket", "pm-btc-150k", "No", "0.55",
               title="Will Bitcoin reach $150,000 by December 2026?")
    assert parse(obs) is None


def test_non_btc_market_dropped() -> None:
    obs = _obs("polymarket", "pm-eth", "Yes", "0.40",
               title="Will Ethereum reach $5,000 by December 2026?")
    assert parse(obs) is None


def test_compare_emits_signed_gap_market_minus_derivative() -> None:
    obs = [
        _obs("deribit", "BTC-26DEC26-150000", "BTC ≥ $150,000", "0.30",
             title="BTC ≥ $150,000 by 26DEC26"),
        _obs("polymarket", "pm-btc-150k", "Yes", "0.45",
             title="Will Bitcoin reach $150,000 by December 2026?"),
    ]
    items = compare(obs, gap_threshold=Decimal("0.05"))
    assert len(items) == 1
    it = items[0]
    assert it.underlying == "BTC"
    assert it.strike == Decimal(150_000)
    assert it.market_venue == "polymarket"
    assert it.market_prob == Decimal("0.45")
    assert it.derivative_prob == Decimal("0.30")
    assert it.gap == Decimal("0.15")  # 0.45 - 0.30
    assert it.material is True


def test_compare_snaps_near_strikes_to_same_grid() -> None:
    # PM strike 149,950 and options 150,000 snap to the same 1000-grid -> compared.
    obs = [
        _obs("deribit", "BTC-26DEC26-150000", "BTC ≥ $150,000", "0.30",
             title="BTC ≥ $150,000 by 26DEC26"),
        _obs("polymarket", "pm-btc", "Yes", "0.31",
             title="Will BTC close above $149,950 in Dec 2026?"),
    ]
    items = compare(obs, gap_threshold=Decimal("0.05"))
    assert len(items) == 1
    assert items[0].material is False  # |0.31 - 0.30| = 0.01 < 0.05


def test_compare_no_derivative_yields_nothing() -> None:
    obs = [
        _obs("polymarket", "pm-btc-150k", "Yes", "0.45",
             title="Will Bitcoin reach $150,000 by December 2026?"),
    ]
    assert compare(obs, gap_threshold=Decimal("0.05")) == []
