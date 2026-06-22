"""Tests for build_digest, render_digest, and the run_daily email integration."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.models.digest import MarketDigest, MoverItem, OutcomeProb, TrackedMarket
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
