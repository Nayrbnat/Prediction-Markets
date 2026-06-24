"""Hand-checked tests for ETH threshold relative value (terminal-only parse + compare)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.markets.eth_price.divergence import _parse_strike, compare, parse
from app.models.domain import MarketObservation

DEC = datetime(2026, 12, 26, tzinfo=timezone.utc)


def _obs(
    venue: str, market_key: str, outcome: str, prob: str, *, title: str, close: datetime = DEC
) -> MarketObservation:
    return MarketObservation(
        venue=venue, market_key=market_key, outcome=outcome, event_title=title,
        probability=Decimal(prob), raw_price=Decimal(prob), close_date=close,
    )


def test_parse_strike_forms() -> None:
    assert _parse_strike("$5,000") == Decimal(5_000)
    assert _parse_strike("5k") == Decimal(5_000)


def test_deribit_row_parsed_from_market_key() -> None:
    obs = _obs("deribit", "ETH-26DEC26-5000", "ETH ≥ $5,000", "0.30",
               title="ETH ≥ $5,000 by 26DEC26")
    point = parse(obs)
    assert point is not None
    assert point.venue == "deribit"
    assert point.strike == Decimal(5_000)
    assert point.prob_above == Decimal("0.30")


def test_polymarket_terminal_above_parsed_from_question() -> None:
    obs = _obs("polymarket", "0xeth", "Yes", "0.45",
               title="Will the price of Ethereum be above $5,000 on December 26?")
    point = parse(obs)
    assert point is not None
    assert point.strike == Decimal(5_000)
    assert point.prob_above == Decimal("0.45")


def test_kalshi_or_above_parsed_from_outcome() -> None:
    obs = _obs("kalshi", "KXETHD-26DEC2617-T4999", "$5,000 or above", "0.40",
               title="ETH price on Dec 26, 2026?")
    point = parse(obs)
    assert point is not None
    assert point.strike == Decimal(5_000)
    assert point.prob_above == Decimal("0.40")


def test_barrier_and_downside_skipped() -> None:
    assert parse(_obs("polymarket", "0xr", "Yes", "0.5",
                      title="Will Ethereum reach $5,000 in December?")) is None
    assert parse(_obs("polymarket", "0xd", "Yes", "0.5",
                      title="Will Ethereum dip to $3,000 in December?")) is None


def test_no_outcome_and_non_eth_dropped() -> None:
    assert parse(_obs("polymarket", "0xeth", "No", "0.55",
                      title="Will Ethereum be above $5,000 on December 26?")) is None
    assert parse(_obs("polymarket", "0xbtc", "Yes", "0.4",
                      title="Will Bitcoin be above $150,000 on December 26?")) is None


def test_compare_emits_signed_gap() -> None:
    obs = [
        _obs("deribit", "ETH-26DEC26-5000", "ETH ≥ $5,000", "0.30",
             title="ETH ≥ $5,000 by 26DEC26"),
        _obs("polymarket", "0xeth", "Yes", "0.45",
             title="Will the price of Ethereum be above $5,000 on December 26?"),
    ]
    items = compare(obs, gap_threshold=Decimal("0.05"))
    assert len(items) == 1
    assert items[0].strike == Decimal(5_000)
    assert items[0].gap == Decimal("0.15")
    assert items[0].material is True
