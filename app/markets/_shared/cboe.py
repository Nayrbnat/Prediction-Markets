"""CBOE options data — free public delayed quotes for equity-index threshold markets.

CBOE's delayed-quotes endpoint needs no key:
  ``GET https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json``
  -> ``data.options`` = [{option, bid, ask, last_trade_price, iv, ...}]
(symbol is ``_NDX`` / ``_SPX`` for indices, ``SPY`` / ``QQQ`` for ETFs.)

Each ``option`` is an OSI symbol, e.g. ``NDX260717C04000000`` = root NDX, expiry 2026-07-17,
Call, strike 4000.000 (last 8 digits in thousandths). For the risk-neutral ``P(S_T > strike)``
we use the option's **delta** (interpolated at the target strike), NOT Breeden-Litzenberger
finite-differencing of prices: CBOE quotes are *delayed* and far/0-DTE strikes are too noisy
for ``-dC/dK`` (it yields non-monotonic, out-of-range probabilities). A call's delta is the
smooth, monotonic, exchange-computed ``N(d1) ≈ P(S_T > K)`` (≈ N(d2) for short-dated options),
which is robust to delayed-quote noise. One ``MarketRef`` per (strike, expiry) target, under
the ``cboe`` venue.

I/O only; the math is a pure interpolation. Degrades gracefully: a failed fetch or an
unbracketable target is skipped, never fabricated.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from app.analysis.probability import q6
from app.core.errors import RateLimitError, SourceError
from app.core.http import fetch_json
from app.core.logging import get_logger
from app.core.rate_limit import AsyncRateLimiter
from app.models.domain import MarketRef

logger = get_logger(__name__)

VENUE = "cboe"
_limiter = AsyncRateLimiter(rate_per_sec=4.0)

# OSI tail: YYMMDD + C/P + 8-digit strike (in thousandths). Root is whatever precedes it.
_OSI = re.compile(r"(\d{6})([CP])(\d{8})$")


def cboe_token(when: datetime) -> str:
    """A UTC datetime -> CBOE/OSI expiry token (YYMMDD), e.g. 2026-07-17 -> '260717'."""
    return f"{when.year % 100:02d}{when.month:02d}{when.day:02d}"


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_osi(symbol: str) -> tuple[str, Decimal] | None:
    """OSI symbol -> (yymmdd_token, strike) for CALLS only; None otherwise."""
    m = _OSI.search(symbol)
    if not m or m.group(2) != "C":
        return None
    strike = _to_decimal(m.group(3))
    if strike is None:
        return None
    return m.group(1), strike / Decimal(1000)


def _clamp01(value: Decimal) -> Decimal:
    return Decimal(0) if value < 0 else (Decimal(1) if value > 1 else value)


async def get_options(client: httpx.AsyncClient, symbol: str) -> list[dict]:
    payload = await fetch_json(
        client, f"/api/global/delayed_quotes/options/{symbol}.json",
        venue=VENUE, limiter=_limiter,
    )
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    options = data.get("options") if isinstance(data, dict) else None
    return [o for o in options if isinstance(o, dict)] if isinstance(options, list) else []


def delta_curve(options: list[dict], *, token: str) -> list[tuple[Decimal, Decimal]]:
    """Build the (strike, call_delta) curve for one expiry token, pure given payloads."""
    curve: list[tuple[Decimal, Decimal]] = []
    for opt in options:
        parsed = _parse_osi(str(opt.get("option", "")))
        if parsed is None or parsed[0] != token:
            continue
        delta = _to_decimal(opt.get("delta"))
        if delta is not None:
            curve.append((parsed[1], abs(delta)))  # call delta in [0,1]
    return sorted(curve)


def prob_from_delta(curve: list[tuple[Decimal, Decimal]], strike: Decimal) -> Decimal | None:
    """Risk-neutral P(S_T > strike) ≈ call delta, linearly interpolated at ``strike``.

    Requires the strike to be bracketed by listed strikes (delta is monotonic in strike);
    returns None otherwise — never extrapolates.
    """
    pts = sorted(curve)
    if len(pts) < 2 or strike < pts[0][0] or strike > pts[-1][0]:
        return None
    for (k1, d1), (k2, d2) in zip(pts, pts[1:], strict=False):
        if k1 <= strike <= k2 and k2 > k1:
            frac = (strike - k1) / (k2 - k1)
            return q6(_clamp01(d1 + (d2 - d1) * frac))
    return None


def _expiry_datetime(token: str) -> datetime:
    """YYMMDD token -> expiry datetime (UTC, ~US market close)."""
    year = 2000 + int(token[0:2])
    return datetime(year, int(token[2:4]), int(token[4:6]), 20, 0, tzinfo=timezone.utc)


def _market_ref(
    *, underlying: str, strike: Decimal, token: str, prob: Decimal, topic: str,
) -> MarketRef:
    above = f"{underlying} ≥ ${strike:,.0f}"
    below = f"{underlying} < ${strike:,.0f}"
    key = f"{underlying}-{token}-{strike:.0f}"
    return MarketRef(
        venue=VENUE,
        event_id=key,
        market_key=key,
        event_title=f"{above} at expiry {token}",
        outcomes=[above, below],
        resolved=False,
        enable_order_book=False,
        topic=topic,
        quoted_prices=[prob, Decimal(1) - prob],
        close_date=_expiry_datetime(token),
    )


async def discover_thresholds(
    client: httpx.AsyncClient,
    topic: str,
    *,
    symbol: str,
    underlying: str,
    targets: list[tuple[Decimal, str]],
    limit: int = 50,
) -> list[MarketRef]:
    """Emit one MarketRef per (strike, expiry token) target with the options-implied
    ``P(above)``. Returns ``[]`` (clean degradation) on no targets or a failed fetch."""
    if not targets:
        logger.warning("cboe.no_targets", extra={"topic": topic, "symbol": symbol})
        return []
    try:
        options = await get_options(client, symbol)
    except (SourceError, RateLimitError) as exc:
        logger.warning("cboe.fetch_failed", extra={"symbol": symbol, "error": str(exc)})
        return []
    if not options:
        logger.warning("cboe.empty", extra={"symbol": symbol})
        return []

    by_token: dict[str, list[Decimal]] = defaultdict(list)
    for strike, token in targets:
        by_token[token].append(strike)

    refs: list[MarketRef] = []
    for token, strikes in by_token.items():
        curve = delta_curve(options, token=token)
        if len(curve) < 2:
            logger.warning("cboe.thin_curve", extra={"symbol": symbol, "token": token})
            continue
        for strike in strikes[:limit]:
            prob = prob_from_delta(curve, strike)
            if prob is None:
                logger.warning(
                    "cboe.unbracketable",
                    extra={"symbol": symbol, "token": token, "strike": str(strike)},
                )
                continue
            refs.append(
                _market_ref(
                    underlying=underlying, strike=strike, token=token, prob=prob, topic=topic
                )
            )
            logger.info(
                "cboe.threshold_priced",
                extra={"symbol": symbol, "token": token, "strike": str(strike), "prob": str(prob)},
            )

    logger.info("cboe.discover", extra={"topic": topic, "markets": len(refs)})
    return refs
