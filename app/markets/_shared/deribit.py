"""Deribit options data — shared, currency-parameterised I/O for crypto threshold markets.

Deribit's public JSON-RPC-over-HTTP API needs no key. We use:
  - ``/api/v2/public/get_instruments?currency=BTC&kind=option&expired=false``
    -> [{instrument_name, strike, option_type, expiration_timestamp(ms), ...}]
  - ``/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option``
    -> [{instrument_name, mark_price(in coin units), underlying_price(USD), mark_iv, ...}]

A call's USD price = ``mark_price * underlying_price``. We build the call-price curve for
one expiry and hand it to the pure Breeden-Litzenberger helper (``_shared/density``) to get
the risk-neutral ``P(S_T > strike)``. One ``MarketRef`` is emitted per configured target
(strike @ expiry token like ``26DEC26``), persisted under the ``deribit`` venue.

I/O only; the probability math is pure and lives in ``density.py``. Degrades gracefully:
a failed fetch or an unbracketable target is skipped, never fabricated.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from app.core.errors import RateLimitError, SourceError
from app.core.http import fetch_json
from app.core.logging import get_logger
from app.core.rate_limit import AsyncRateLimiter
from app.markets._shared.density import prob_above
from app.models.domain import MarketRef

logger = get_logger(__name__)

VENUE = "deribit"
_limiter = AsyncRateLimiter(rate_per_sec=5.0)


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


async def _public_list(
    client: httpx.AsyncClient, path: str, params: dict[str, str]
) -> list[dict]:
    """GET a public Deribit endpoint and return its ``result`` list (or [])."""
    payload = await fetch_json(client, path, venue=VENUE, limiter=_limiter, params=params)
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    return [r for r in result if isinstance(r, dict)] if isinstance(result, list) else []


async def get_instruments(client: httpx.AsyncClient, currency: str) -> list[dict]:
    return await _public_list(
        client,
        "/api/v2/public/get_instruments",
        {"currency": currency, "kind": "option", "expired": "false"},
    )


async def get_book_summary(client: httpx.AsyncClient, currency: str) -> list[dict]:
    return await _public_list(
        client,
        "/api/v2/public/get_book_summary_by_currency",
        {"currency": currency, "kind": "option"},
    )


def call_curve(
    instruments: list[dict], summaries: list[dict], *, expiry_token: str
) -> list[tuple[Decimal, Decimal]]:
    """Build the (strike, call_price_usd) curve for one expiry, pure given raw payloads."""
    by_name = {s.get("instrument_name"): s for s in summaries}
    token = f"-{expiry_token}-"
    curve: list[tuple[Decimal, Decimal]] = []
    for inst in instruments:
        name = inst.get("instrument_name", "")
        if inst.get("option_type") != "call" or token not in name:
            continue
        strike = _to_decimal(inst.get("strike"))
        summary = by_name.get(name)
        if strike is None or not isinstance(summary, dict):
            continue
        mark = _to_decimal(summary.get("mark_price"))
        under = _to_decimal(summary.get("underlying_price"))
        if mark is None or under is None:
            continue
        curve.append((strike, mark * under))
    return sorted(curve)


def expiry_datetime(instruments: list[dict], *, expiry_token: str) -> datetime | None:
    """The expiry timestamp (UTC) for a token, read from a matching instrument."""
    token = f"-{expiry_token}-"
    for inst in instruments:
        if token in inst.get("instrument_name", ""):
            ts = inst.get("expiration_timestamp")
            if isinstance(ts, (int, float)):
                return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return None


def _market_ref(
    *, underlying: str, strike: Decimal, expiry_token: str, prob: Decimal,
    close_date: datetime | None, topic: str,
) -> MarketRef:
    above = f"{underlying} ≥ ${strike:,.0f}"
    below = f"{underlying} < ${strike:,.0f}"
    key = f"{underlying}-{expiry_token}-{strike:.0f}"
    return MarketRef(
        venue=VENUE,
        event_id=key,
        market_key=key,
        event_title=f"{above} by {expiry_token}",
        outcomes=[above, below],
        resolved=False,
        enable_order_book=False,
        topic=topic,
        quoted_prices=[prob, Decimal(1) - prob],
        close_date=close_date,
    )


async def discover_thresholds(
    client: httpx.AsyncClient,
    topic: str,
    *,
    currency: str,
    underlying: str,
    targets: list[tuple[Decimal, str]],
    limit: int = 50,
) -> list[MarketRef]:
    """Emit one MarketRef per (strike, expiry_token) target with the options-implied
    ``P(above)``. Returns ``[]`` (clean degradation) on no targets or a failed fetch."""
    if not targets:
        logger.warning("deribit.no_targets", extra={"topic": topic, "currency": currency})
        return []
    try:
        instruments = await get_instruments(client, currency)
        summaries = await get_book_summary(client, currency)
    except (SourceError, RateLimitError) as exc:
        logger.warning("deribit.fetch_failed", extra={"currency": currency, "error": str(exc)})
        return []
    if not instruments or not summaries:
        logger.warning("deribit.empty", extra={"currency": currency})
        return []

    by_expiry: dict[str, list[Decimal]] = defaultdict(list)
    for strike, token in targets:
        by_expiry[token].append(strike)

    refs: list[MarketRef] = []
    for token, strikes in by_expiry.items():
        curve = call_curve(instruments, summaries, expiry_token=token)
        if len(curve) < 2:
            logger.warning("deribit.thin_curve", extra={"currency": currency, "expiry": token})
            continue
        close = expiry_datetime(instruments, expiry_token=token)
        for strike in strikes[:limit]:
            prob = prob_above(curve, strike)
            if prob is None:
                logger.warning(
                    "deribit.unbracketable",
                    extra={"currency": currency, "expiry": token, "strike": str(strike)},
                )
                continue
            refs.append(
                _market_ref(
                    underlying=underlying, strike=strike, expiry_token=token,
                    prob=prob, close_date=close, topic=topic,
                )
            )
            logger.info(
                "deribit.threshold_priced",
                extra={"currency": currency, "expiry": token,
                       "strike": str(strike), "prob_above": str(prob)},
            )

    logger.info("deribit.discover", extra={"topic": topic, "markets": len(refs)})
    return refs
