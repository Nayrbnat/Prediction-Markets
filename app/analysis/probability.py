"""Order-book / price -> market-implied probability, with a thin-market flag.

Prices arriving here are already expressed in 0..1 probability units by the source
layer (Polymarket token price is already 0..1; Kalshi cents are divided by 100 in
the source). This layer is venue-agnostic and pure.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.models.domain import OrderBookTop, OutcomeProbability
from app.models.provenance import ConfidenceFlag, Provenance


def q6(x: Decimal) -> Decimal:
    """Quantize to 6 decimal places (ROUND_HALF_UP). Pure."""
    return x.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def complement(x: Decimal) -> Decimal:
    """Return the probability complement (1 − x), quantized to 6 dp. Pure.

    Used exclusively for **binary** complementary outcome pairs (e.g. Yes/No)
    where the No side is the financial identity of 1 − Yes.  Never apply to
    multi-outcome independent candidates.
    """
    return q6(Decimal(1) - x)


def mid_price(book: OrderBookTop) -> Decimal | None:
    """Best-bid/ask midpoint, falling back to an explicit ``mid`` if present."""
    if book.best_bid is not None and book.best_ask is not None:
        return (book.best_bid + book.best_ask) / 2
    return book.mid


def assess_confidence(
    *,
    spread: Decimal | None,
    volume: Decimal | None,
    thin_spread: Decimal,
    thin_volume: Decimal,
) -> ConfidenceFlag:
    """Flag a market as ``thin`` when the spread is wide or volume is low."""
    reasons: list[str] = []
    if spread is not None and spread > thin_spread:
        reasons.append(f"spread {spread} > {thin_spread}")
    if volume is not None and volume < thin_volume:
        reasons.append(f"volume {volume} < {thin_volume}")
    return ConfidenceFlag(level="thin" if reasons else "ok", reasons=reasons)


def implied_probability(
    *,
    outcome: str,
    book: OrderBookTop,
    provenance: Provenance,
    thin_spread: Decimal,
    thin_volume: Decimal,
    volume: Decimal | None = None,
) -> OutcomeProbability | None:
    """Build an ``OutcomeProbability`` from an order book. Returns ``None`` when no
    mid can be derived (no fabricated value)."""
    mid = mid_price(book)
    if mid is None:
        return None
    return OutcomeProbability(
        outcome=outcome,
        probability=q6(mid),
        raw_price=q6(mid),
        provenance=provenance,
        confidence=assess_confidence(
            spread=book.spread, volume=volume, thin_spread=thin_spread, thin_volume=thin_volume
        ),
    )


def probability_from_price(
    *,
    outcome: str,
    price: Decimal,
    provenance: Provenance,
    thin_spread: Decimal,
    thin_volume: Decimal,
    volume: Decimal | None = None,
    spread: Decimal | None = None,
) -> OutcomeProbability:
    """Build an ``OutcomeProbability`` from a single quoted price (e.g. Gamma
    ``outcomePrices``) when no full order book is fetched."""
    return OutcomeProbability(
        outcome=outcome,
        probability=q6(price),
        raw_price=q6(price),
        provenance=provenance,
        confidence=assess_confidence(
            spread=spread, volume=volume, thin_spread=thin_spread, thin_volume=thin_volume
        ),
    )
