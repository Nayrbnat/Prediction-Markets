"""Hand-checked tests for distribution normalisation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.analysis.distribution import normalise_distribution
from app.models.domain import OutcomeProbability
from app.models.provenance import Provenance


def _outcome(label: str, prob: str) -> OutcomeProbability:
    return OutcomeProbability(
        outcome=label,
        probability=Decimal(prob),
        raw_price=Decimal(prob),
        provenance=Provenance(
            venue="polymarket", endpoint="/markets", raw_value=prob,
            observed_at=datetime.now(timezone.utc),
        ),
    )


def test_normalise_factor_equals_raw_sum() -> None:
    # 0.20 + 0.87 = 1.07  -> factor 1.07 (hand-checked)
    dist = normalise_distribution(
        venue="polymarket", event_title="Fed", market_key="m1",
        outcomes=[_outcome("Yes", "0.20"), _outcome("No", "0.87")],
    )
    assert dist.raw_sum == Decimal("1.07")
    assert dist.factor == Decimal("1.07")
    assert dist.normalised is True


def test_normalised_outcomes_sum_to_one() -> None:
    dist = normalise_distribution(
        venue="polymarket", event_title="Fed", market_key="m1",
        outcomes=[_outcome("Yes", "0.20"), _outcome("No", "0.87")],
    )
    total = sum(o.probability for o in dist.outcomes)
    assert total == Decimal("1.000000")
    # 0.20 / 1.07 = 0.186915887...  -> quantized to 6 dp = 0.186916  (hand-checked)
    assert dist.outcomes[0].probability == Decimal("0.186916")
    assert dist.outcomes[0].provenance.normalisation_factor == Decimal("1.07")


def test_zero_sum_is_handled_without_fabrication() -> None:
    dist = normalise_distribution(
        venue="kalshi", event_title="Edge", market_key="m0",
        outcomes=[_outcome("Yes", "0"), _outcome("No", "0")],
    )
    assert dist.factor == Decimal(0)
    assert dist.normalised is False
