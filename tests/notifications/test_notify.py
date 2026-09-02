"""Tests for the fan-out helper: email and Telegram are delivered independently."""

from __future__ import annotations

from app.services import notify


class FakeEmailSender:
    """Records send() calls; optionally raises to simulate a failing sink."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self._fail = fail

    async def send(self, *, subject: str, html: str, text: str, to: list[str]) -> None:
        if self._fail:
            raise RuntimeError("smtp down")
        self.calls.append({"subject": subject, "html": html, "text": text, "to": to})


class FakeTelegramSender:
    """Records send() calls; optionally raises to simulate a failing sink."""

    def __init__(self, *, fail: bool = False) -> None:
        self.texts: list[str] = []
        self._fail = fail

    async def send(self, *, text: str) -> None:
        if self._fail:
            raise RuntimeError("telegram down")
        self.texts.append(text)


async def _fan(email: FakeEmailSender, tg: FakeTelegramSender, recipients: list[str]) -> None:
    await notify.fan_out(
        sender=email,
        tg_sender=tg,
        subject="S",
        html="<p>H</p>",
        text="digest body",
        recipients=recipients,
        context="test",
    )


async def test_both_sinks_receive_when_configured() -> None:
    email, tg = FakeEmailSender(), FakeTelegramSender()
    await _fan(email, tg, ["user@example.com"])
    assert len(email.calls) == 1
    assert email.calls[0]["to"] == ["user@example.com"]
    assert tg.texts == ["digest body"]


async def test_telegram_still_sends_when_no_email_recipients() -> None:
    """Empty digest_to + configured Telegram is a legitimate state — TG must still fire."""
    email, tg = FakeEmailSender(), FakeTelegramSender()
    await _fan(email, tg, [])
    assert email.calls == []  # email skipped, no recipients
    assert tg.texts == ["digest body"]  # telegram unaffected


async def test_email_failure_does_not_block_telegram() -> None:
    email, tg = FakeEmailSender(fail=True), FakeTelegramSender()
    await _fan(email, tg, ["user@example.com"])  # must not raise
    assert tg.texts == ["digest body"]


async def test_telegram_failure_does_not_block_email() -> None:
    email, tg = FakeEmailSender(), FakeTelegramSender(fail=True)
    await _fan(email, tg, ["user@example.com"])  # must not raise
    assert len(email.calls) == 1


async def test_both_failing_is_swallowed() -> None:
    email, tg = FakeEmailSender(fail=True), FakeTelegramSender(fail=True)
    await _fan(email, tg, ["user@example.com"])  # must not raise
    assert email.calls == []
    assert tg.texts == []
