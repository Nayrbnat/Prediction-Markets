"""Hand-checked tests for Nasdaq-100 threshold relative value + dynamic targeting."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.markets._shared.cboe import cboe_token
from app.markets._shared.threshold_parse import targets_from_refs
from app.markets.nasdaq_price.divergence import _parse_strike, compare, parse
from app.models.domain import MarketObservation, MarketRef

JUL17 = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)


def _obs(venue: str, key: str, outcome: str, prob: str, *, title: str) -> MarketObservation:
    return MarketObservation(
        venue=venue, market_key=key, outcome=outcome, event_title=title,
        probability=Decimal(prob), raw_price=Decimal(prob), close_date=JUL17,
    )


def test_cboe_row_parsed_from_market_key() -> None:
    obs = _obs("cboe", "NDX-260717-27000", "NDX ≥ $27,000", "0.50",
               title="NDX ≥ $27,000 at expiry 260717")
    point = parse(obs)
    assert point is not None
    assert point.venue == "cboe"
    assert point.underlying == "NDX"
    assert point.strike == Decimal(27000)
    assert point.prob_above == Decimal("0.50")


def test_kalshi_terminal_above_parsed() -> None:
    obs = _obs("kalshi", "KXNASDAQ100U-..", "27,000 or above", "0.60",
               title="Will the Nasdaq-100 be above 26999.99 at the end of Jul 17, 2026?")
    point = parse(obs)
    assert point is not None
    assert point.underlying == "NDX"  # set from the market's underlying param
    assert point.strike == Decimal(27000)
    assert point.prob_above == Decimal("0.60")


def test_barrier_hit_market_skipped() -> None:
    obs = _obs("polymarket", "pm", "Yes", "0.55",
               title="Will the Nasdaq hit (HIGH) 27000 this week?")
    assert parse(obs) is None


def test_compare_emits_gap() -> None:
    obs = [
        _obs("cboe", "NDX-260717-27000", "NDX ≥ $27,000", "0.50",
             title="NDX ≥ $27,000 at expiry 260717"),
        _obs("kalshi", "k", "27,000 or above", "0.60",
             title="Will the Nasdaq-100 be above 26999.99 at the end of Jul 17, 2026?"),
    ]
    items = compare(obs, gap_threshold=Decimal("0.05"))
    assert len(items) == 1
    it = items[0]
    assert it.underlying == "NDX"
    assert it.strike == Decimal(27000)
    assert it.market_prob == Decimal("0.60")
    assert it.derivative_prob == Decimal("0.50")
    assert it.gap == Decimal("0.10")
    assert it.material is True


def test_targets_from_refs_uses_cboe_token() -> None:
    ref = MarketRef(
        venue="kalshi", event_id="e", market_key="k",
        event_title="Will the Nasdaq-100 be above 26999.99 at the end of Jul 17, 2026?",
        outcomes=["27,000 or above", "No"], close_date=JUL17,
    )
    targets = targets_from_refs(
        [ref], underlying="NDX",
        aliases=("nasdaq", "ndx"), token_fn=cboe_token,
    )
    assert targets == [(Decimal(27000), "260717")]


def test_outcome_strike_handles_bare_index_number() -> None:
    # Kalshi index outcomes have NO $ ("27,300 or above"); the outcome parser handles bare
    # numbers, while the $-strict parser still works for $-style outcomes.
    from app.markets._shared.threshold_parse import parse_outcome_strike
    assert parse_outcome_strike("27,300 or above") == Decimal(27300)  # bare (NDX)
    assert parse_outcome_strike("$50,000 or above") == Decimal(50000)  # $-style (crypto)
    assert parse_outcome_strike("Yes") is None
    assert _parse_strike("27,300 or above") is None  # $-strict parser rejects bare
