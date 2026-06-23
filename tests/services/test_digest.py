"""Tests for build_digest, render_digest, and the run_daily email integration."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.models.digest import (
    DivergenceItem,
    MarketDigest,
    MeetingMatrix,
    MoverItem,
    OutcomeProb,
    SourceProbs,
    TrackedMarket,
)
from app.models.domain import MarketObservation
from app.services.digest_render import _pct, _signed_pp, render_digest
from app.services.digest_service import _group_tracked, build_digest
from tests.fakes import FakeGateway, InMemoryMarketRepository

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "database_url": "",
        "ingest_topics": "fed",
        "high_priority_topics": "fed",
        "digest_enabled": True,
        "digest_to": "user@example.com",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _obs(
    market_key: str,
    outcome: str,
    prob: str,
    *,
    event_title: str = "Fed Decision",
    tracked: bool = True,
    topic: str = "fed",
) -> MarketObservation:
    return MarketObservation(
        venue="polymarket",
        market_key=market_key,
        outcome=outcome,
        event_title=event_title,
        topic=topic,
        probability=Decimal(prob),
        raw_price=Decimal(prob),
        tracked=tracked,
        priority="high" if tracked else "normal",
    )


class FakeEmailSender:
    """Captures the last send() call for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send(
        self, *, subject: str, html: str, text: str, to: list[str]
    ) -> None:
        self.calls.append({"subject": subject, "html": html, "text": text, "to": to})

    @property
    def last_call(self) -> dict:
        return self.calls[-1]


# ---------------------------------------------------------------------------
# Formatting helpers (pure)
# ---------------------------------------------------------------------------

def test_pct_formats_correctly() -> None:
    assert _pct(Decimal("0.7354")) == "73.5%"
    assert _pct(Decimal("0.10")) == "10.0%"
    assert _pct(Decimal("1.0")) == "100.0%"
    assert _pct(Decimal("0.0")) == "0.0%"


def test_signed_pp_positive() -> None:
    assert _signed_pp(Decimal("0.13")) == "+13.0pp"


def test_signed_pp_negative() -> None:
    assert _signed_pp(Decimal("-0.07")) == "-7.0pp"


def test_signed_pp_zero() -> None:
    assert _signed_pp(Decimal("0")) == "+0.0pp"


# ---------------------------------------------------------------------------
# _group_tracked (pure)
# ---------------------------------------------------------------------------

def test_group_tracked_groups_by_market() -> None:
    obs = [
        _obs("m1", "Yes", "0.65"),
        _obs("m1", "No", "0.35"),
        _obs("m2", "A", "0.50"),
    ]
    groups = _group_tracked(obs)
    assert len(groups) == 2
    m1 = next(g for g in groups if g.market_key == "m1")
    assert len(m1.outcomes) == 2
    # Outcomes sorted alphabetically
    assert m1.outcomes[0].outcome == "No"
    assert m1.outcomes[1].outcome == "Yes"


def test_group_tracked_outcome_probs_match() -> None:
    obs = [_obs("m1", "Yes", "0.72")]
    groups = _group_tracked(obs)
    assert groups[0].outcomes[0].probability == Decimal("0.72")


# ---------------------------------------------------------------------------
# build_digest
# ---------------------------------------------------------------------------

async def test_build_digest_assembles_movers_and_tracked() -> None:
    repo = InMemoryMarketRepository()
    settings = _settings()

    # Seed two dates so there is a mover
    await repo.seed([_obs("m1", "Yes", "0.50")], snapshot_date=YESTERDAY)
    await repo.seed([_obs("m1", "Yes", "0.65")], snapshot_date=TODAY)

    digest = await build_digest(repo, settings)

    assert digest.mover_count == 1
    assert digest.movers[0].outcome == "Yes"
    assert digest.movers[0].delta == Decimal("0.15")

    assert digest.tracked_count == 1
    assert digest.tracked[0].market_key == "m1"


async def test_build_digest_no_movers_when_single_date() -> None:
    """With only one snapshot date there is no previous to compare — no movers."""
    repo = InMemoryMarketRepository()
    settings = _settings()

    await repo.seed([_obs("m1", "Yes", "0.70")], snapshot_date=TODAY)

    digest = await build_digest(repo, settings)
    assert digest.mover_count == 0
    assert digest.tracked_count == 1


async def test_build_digest_respects_threshold() -> None:
    """A small move (< mover_threshold) must not produce a mover."""
    repo = InMemoryMarketRepository()
    settings = _settings(mover_threshold=Decimal("0.10"))

    await repo.seed([_obs("m1", "Yes", "0.60")], snapshot_date=YESTERDAY)
    await repo.seed([_obs("m1", "Yes", "0.65")], snapshot_date=TODAY)  # 0.05 < 0.10

    digest = await build_digest(repo, settings)
    assert digest.mover_count == 0


async def test_build_digest_populates_divergences() -> None:
    """A cme + prediction-market pair for the same meeting yields a divergence."""
    repo = InMemoryMarketRepository()
    settings = _settings(rv_gap_threshold=Decimal("0.05"))
    close = datetime(2026, 9, 16, tzinfo=timezone.utc)

    def _d(venue: str, mk: str, outcome: str, prob: str) -> MarketObservation:
        return MarketObservation(
            venue=venue, market_key=mk, outcome=outcome,
            event_title="Fed decision in September 2026", topic="fed",
            probability=Decimal(prob), raw_price=Decimal(prob),
            tracked=True, priority="high", close_date=close,
        )

    await repo.seed(
        [
            _d("cme", "FOMC-2026-09-16", "No change", "0.81"),
            _d("polymarket", "pm-sep", "No change", "0.74"),
        ],
        snapshot_date=TODAY,
    )

    digest = await build_digest(repo, settings)
    assert digest.divergence_count == 1
    item = next(d for d in digest.divergences if d.outcome == "No change")
    assert item.market_venue == "polymarket"
    assert item.market_prob == Decimal("0.74")
    assert item.futures_prob == Decimal("0.81")
    assert item.gap == Decimal("-0.07")
    assert item.material is True


async def test_build_digest_builds_cut_hold_raise_matrix() -> None:
    """cme + polymarket + kalshi for one meeting -> one matrix, 3 source rows; an
    unmapped market falls back to the generic tracked list."""
    repo = InMemoryMarketRepository()
    settings = _settings()
    close = datetime(2026, 9, 16, tzinfo=timezone.utc)

    def _o(venue: str, mk: str, outcome: str, prob: str) -> MarketObservation:
        return MarketObservation(
            venue=venue, market_key=mk, outcome=outcome,
            event_title="Fed decision in September 2026", topic="fed",
            probability=Decimal(prob), raw_price=Decimal(prob),
            tracked=True, priority="high", close_date=close,
        )

    await repo.seed(
        [
            # Polymarket: 5% cut, 73% hold, 22% raise
            _o("polymarket", "pm", "25 bps decrease", "0.05"),
            _o("polymarket", "pm", "No change", "0.73"),
            _o("polymarket", "pm", "25 bps increase", "0.22"),
            # Kalshi
            _o("kalshi", "k", "Cut 25bps", "0.06"),
            _o("kalshi", "k", "Fed maintains rate", "0.74"),
            _o("kalshi", "k", "Hike 25bps", "0.20"),
            # Futures (cme)
            _o("cme", "FOMC-2026-09-16", "No change", "0.64"),
            _o("cme", "FOMC-2026-09-16", "25 bps hike", "0.36"),
            # Unmapped market (number of dissents) -> generic tracked
            _o("polymarket", "dissents", "0", "0.60"),
            _o("polymarket", "dissents", "1", "0.40"),
        ],
        snapshot_date=TODAY,
    )

    digest = await build_digest(repo, settings)

    assert len(digest.meeting_matrices) == 1
    matrix = digest.meeting_matrices[0]
    assert matrix.meeting == "Sep 2026"
    # Three sources, ordered Polymarket, Kalshi, Futures
    assert [r.source for r in matrix.rows] == ["Polymarket", "Kalshi", "Futures"]
    pm = matrix.rows[0]
    assert pm.cut == Decimal("0.05")
    assert pm.hold == Decimal("0.73")
    assert pm.raise_ == Decimal("0.22")
    fut = matrix.rows[2]
    assert fut.cut == Decimal("0")
    assert fut.hold == Decimal("0.64")
    assert fut.raise_ == Decimal("0.36")
    # The dissents market is not cut/hold/raise -> generic tracked list.
    assert digest.tracked_count == 1
    assert digest.tracked[0].market_key == "dissents"


# ---------------------------------------------------------------------------
# render_digest (pure)
# ---------------------------------------------------------------------------

def _make_digest(
    *,
    mover_delta: str = "0.15",
    include_mover: bool = True,
    include_tracked: bool = True,
) -> MarketDigest:
    movers = []
    if include_mover:
        movers = [
            MoverItem(
                venue="polymarket",
                event_title="Fed Decision 2025",
                market_key="m1",
                outcome="Yes",
                previous=Decimal("0.50"),
                current=Decimal("0.65"),
                delta=Decimal(mover_delta),
            )
        ]
    tracked = []
    if include_tracked:
        tracked = [
            TrackedMarket(
                venue="kalshi",
                event_title="Fed Decision 2025",
                market_key="k1",
                outcomes=[
                    OutcomeProb(outcome="Yes", probability=Decimal("0.65")),
                    OutcomeProb(outcome="No", probability=Decimal("0.35")),
                ],
            )
        ]
    return MarketDigest(
        generated_for=TODAY,
        mover_threshold=Decimal("0.10"),
        movers=movers,
        tracked=tracked,
        mover_count=len(movers),
        tracked_count=len(tracked),
    )


def test_render_digest_subject_contains_count() -> None:
    digest = _make_digest()
    subject, _, _ = render_digest(digest)
    assert "1 sharp move(s)" in subject
    assert str(TODAY) in subject


def test_render_html_contains_event_title() -> None:
    digest = _make_digest()
    _, html, _ = render_digest(digest)
    assert "Fed Decision 2025" in html


def test_render_html_contains_outcome() -> None:
    digest = _make_digest()
    _, html, _ = render_digest(digest)
    assert "Yes" in html


def test_render_html_contains_signed_pp_delta_positive() -> None:
    digest = _make_digest(mover_delta="0.15")
    _, html, _ = render_digest(digest)
    assert "+15.0pp" in html


def test_render_html_contains_signed_pp_delta_negative() -> None:
    digest = _make_digest(mover_delta="-0.12")
    _, html, _ = render_digest(digest)
    assert "-12.0pp" in html


def test_render_html_contains_percentage() -> None:
    digest = _make_digest()
    _, html, _ = render_digest(digest)
    assert "65.0%" in html


def test_render_text_contains_event_title() -> None:
    digest = _make_digest()
    _, _, text = render_digest(digest)
    assert "Fed Decision 2025" in text


def test_render_text_negative_delta_has_minus_sign() -> None:
    digest = _make_digest(mover_delta="-0.13")
    _, _, text = render_digest(digest)
    assert "-13.0pp" in text


def test_render_text_contains_percentage() -> None:
    digest = _make_digest()
    _, _, text = render_digest(digest)
    assert "65.0%" in text


def test_render_no_movers_section() -> None:
    digest = _make_digest(include_mover=False)
    _, html, text = render_digest(digest)
    assert "0" in html
    assert "No tracked outcomes" in html


def test_render_matrix_section_three_sources() -> None:
    digest = MarketDigest(
        generated_for=TODAY,
        mover_threshold=Decimal("0.10"),
        meeting_matrices=[
            MeetingMatrix(
                meeting="Sep 2026",
                close_date=datetime(2026, 9, 16, tzinfo=timezone.utc),
                rows=[
                    SourceProbs(source="Polymarket", venue="polymarket",
                                cut=Decimal("0.05"), hold=Decimal("0.73"), raise_=Decimal("0.22")),
                    SourceProbs(source="Kalshi", venue="kalshi",
                                cut=Decimal("0.06"), hold=Decimal("0.74"), raise_=Decimal("0.20")),
                    SourceProbs(source="Futures", venue="cme",
                                cut=Decimal("0.0"), hold=Decimal("0.64"), raise_=Decimal("0.36")),
                ],
            )
        ],
    )
    _, html, text = render_digest(digest)
    for body in (html, text):
        assert "probabilities by source" in body.lower()
        assert "Sep 2026" in body
        for src in ("Polymarket", "Kalshi", "Futures"):
            assert src in body
        # cut/hold/raise headers and a couple of values
        assert "Cut" in body and "Hold" in body and "Raise" in body
        assert "73.0%" in body  # polymarket hold
        assert "36.0%" in body  # futures raise


def test_render_divergence_section_present_for_material_gap() -> None:
    digest = MarketDigest(
        generated_for=TODAY,
        mover_threshold=Decimal("0.10"),
        divergences=[
            DivergenceItem(
                meeting="September 2026",
                market_venue="polymarket",
                outcome="No change",
                market_prob=Decimal("0.74"),
                futures_prob=Decimal("0.81"),
                gap=Decimal("-0.07"),
                material=True,
            )
        ],
        divergence_count=1,
    )
    _, html, text = render_digest(digest)
    for body in (html, text):
        assert "fed funds futures" in body.lower()  # HTML title-case / text upper-case
        assert "September 2026" in body
        assert "-7.0pp" in body


def test_render_divergence_section_absent_when_no_material_gap() -> None:
    digest = MarketDigest(
        generated_for=TODAY,
        mover_threshold=Decimal("0.10"),
        divergences=[
            DivergenceItem(
                meeting="September 2026", market_venue="kalshi", outcome="No change",
                market_prob=Decimal("0.80"), futures_prob=Decimal("0.81"),
                gap=Decimal("-0.01"), material=False,
            )
        ],
        divergence_count=0,
    )
    _, html, text = render_digest(digest)
    assert "fed funds futures" not in html.lower()
    assert "fed funds futures" not in text.lower()


# ---------------------------------------------------------------------------
# run_daily integration (email wiring)
# ---------------------------------------------------------------------------

async def test_run_daily_sends_email_when_enabled() -> None:
    from app.services.ingestion_service import run_daily

    settings = _settings(digest_enabled=True, digest_to="user@example.com")
    repo = InMemoryMarketRepository()
    sender = FakeEmailSender()

    # Seed two dates so there's a mover
    await repo.seed([_obs("m1", "Yes", "0.50")], snapshot_date=YESTERDAY)
    await repo.seed([_obs("m1", "Yes", "0.70")], snapshot_date=TODAY)

    gw = FakeGateway()  # no new discovery — uses already-seeded data

    await run_daily(repo=repo, gateway=gw, sender=sender, settings=settings)

    assert len(sender.calls) == 1
    call = sender.last_call
    assert "sharp move" in call["subject"].lower()
    assert "user@example.com" in call["to"]
    assert len(call["html"]) > 0
    assert len(call["text"]) > 0


async def test_run_daily_skips_email_when_disabled() -> None:
    from app.services.ingestion_service import run_daily

    settings = _settings(digest_enabled=False)
    repo = InMemoryMarketRepository()
    sender = FakeEmailSender()
    gw = FakeGateway()

    await run_daily(repo=repo, gateway=gw, sender=sender, settings=settings)

    assert sender.calls == []


async def test_run_daily_skips_send_when_no_recipients() -> None:
    """digest_enabled=True but digest_to="" → no send (warns, but no crash)."""
    from app.services.ingestion_service import run_daily

    settings = _settings(digest_enabled=True, digest_to="")
    repo = InMemoryMarketRepository()
    sender = FakeEmailSender()
    gw = FakeGateway()

    await run_daily(repo=repo, gateway=gw, sender=sender, settings=settings)

    assert sender.calls == []


# ---------------------------------------------------------------------------
# Email factory + ConsoleEmailSender unit tests
# ---------------------------------------------------------------------------

def test_make_email_sender_returns_console_when_no_smtp() -> None:
    from app.notifications.email import ConsoleEmailSender, make_email_sender

    settings = _settings(smtp_host="", smtp_username="", smtp_password="")
    sender = make_email_sender(settings)
    assert isinstance(sender, ConsoleEmailSender)


def test_make_email_sender_returns_smtp_when_configured() -> None:
    from app.notifications.email import SmtpEmailSender, make_email_sender

    settings = _settings(
        smtp_host="smtp.example.com",
        smtp_username="user@example.com",
        smtp_password="secret",
    )
    sender = make_email_sender(settings)
    assert isinstance(sender, SmtpEmailSender)


async def test_console_sender_does_not_raise(capsys: pytest.CaptureFixture) -> None:
    from app.notifications.email import ConsoleEmailSender

    sender = ConsoleEmailSender()
    await sender.send(
        subject="Test subject",
        html="<p>Hello</p>",
        text="Hello",
        to=["a@b.com"],
    )
    captured = capsys.readouterr()
    assert "Test subject" in captured.out


async def test_smtp_sender_calls_smtplib(monkeypatch: pytest.MonkeyPatch) -> None:
    """SmtpEmailSender uses asyncio.to_thread; patch smtplib.SMTP to avoid real network."""

    from app.notifications.email import SmtpEmailSender

    smtp_instance = MagicMock()
    smtp_instance.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_instance.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=smtp_instance):
        sender = SmtpEmailSender(
            host="smtp.example.com",
            port=587,
            username="user",
            password="pass",
            from_addr="user@example.com",
        )
        await sender.send(
            subject="Hello",
            html="<p>Hi</p>",
            text="Hi",
            to=["recipient@example.com"],
        )

    smtp_instance.starttls.assert_called_once()
    smtp_instance.login.assert_called_once_with("user", "pass")
    smtp_instance.sendmail.assert_called_once()
