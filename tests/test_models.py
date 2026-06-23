"""Phase 1: models validate good data and reject bad."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.domain import MarketRef, OutcomeProbability
from app.models.provenance import Provenance
from app.models.requests import AnalyzeRequest
from app.models.responses import TopicAnalysis


def _provenance() -> Provenance:
    return Provenance(
        venue="polymarket",
        endpoint="/book",
        raw_value="0.62",
        observed_at=datetime.now(timezone.utc),
    )


def test_outcome_probability_round_trips() -> None:
    op = OutcomeProbability(
        outcome="Yes",
        probability=Decimal("0.62"),
        raw_price=Decimal("0.62"),
        provenance=_provenance(),
    )
    assert op.confidence.level == "ok"


def test_probability_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        OutcomeProbability(
            outcome="Yes",
            probability=Decimal("1.5"),
            raw_price=Decimal("0.62"),
            provenance=_provenance(),
        )


def test_analyze_request_requires_topic() -> None:
    with pytest.raises(ValidationError):
        AnalyzeRequest(topic="")


def test_topic_analysis_defaults_v1_seams() -> None:
    ta = TopicAnalysis(topic="fed rate decision")
    assert ta.llm_synthesis is None
    assert ta.disclaimer
    assert ta.stale is False


def test_market_ref_minimal() -> None:
    ref = MarketRef(
        venue="kalshi",
        event_id="E1",
        market_key="KXFED-26",
        event_title="Fed decision",
        outcomes=["Yes", "No"],
    )
    assert ref.enable_order_book is True
    assert ref.token_ids == []
