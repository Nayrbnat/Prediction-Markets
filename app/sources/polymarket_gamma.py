"""Polymarket Gamma — discovery + quick-read prices (public, no auth).

Topic -> matched events via /public-search. Returns typed MarketRef grouped at the
EVENT level: a mutually-exclusive multi-outcome event (``negRisk``) becomes one
MarketRef whose outcomes are the candidate markets (label = ``groupItemTitle``,
price = each candidate's Yes price); a single binary market becomes one Yes/No
MarketRef; a non-exclusive multi-market event becomes one binary MarketRef per
child. Precise prices come from the CLOB client (binary events only). No analysis.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

import httpx

from app.analysis.probability import complement
from app.core.errors import SchemaDriftError
from app.core.http import fetch_json
from app.core.logging import get_logger
from app.core.rate_limit import AsyncRateLimiter
from app.models.domain import MarketRef
from app.sources._util import parse_iso_datetime

logger = get_logger(__name__)

VENUE = "polymarket"
# Gamma general limit is generous; keep well under it. Verify current limits at build time.
_limiter = AsyncRateLimiter(rate_per_sec=8.0)


def _as_list(value: object) -> list:
    """Gamma returns some array fields as JSON-encoded strings."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    if isinstance(value, list):
        return value
    return []


def _dec(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _binary_ref(
    market: dict,
    *,
    event_id: str,
    event_title: str,
    topic: str,
    event_open_interest: Decimal | None = None,
) -> MarketRef | None:
    """One Yes/No MarketRef from a single binary market (CLOB-priced).

    Polymarket only books the Yes token, so bid/ask/last are primary for outcome[0]
    (Yes).  For the complement side (outcome[1], "No") we apply the no-arbitrage
    identity for complementary binary pairs:
        no_bid  = 1 − yes_ask
        no_ask  = 1 − yes_bid
        no_last = 1 − yes_last
    Each is derived only when the Yes source value is present; never fabricated from
    None.  This is the same identity already used to derive the No *probability*.

    ``event_open_interest`` is the event-level OI threaded in from ``_event_to_refs``
    (market-level OI is not reliably present for binary markets).
    """
    try:
        outcomes = [str(o) for o in _as_list(market.get("outcomes"))]
        if not outcomes:
            return None
        prices = [
            p for p in (_dec(x) for x in _as_list(market.get("outcomePrices"))) if p is not None
        ]
        token_ids = [str(t) for t in _as_list(market.get("clobTokenIds"))]
        market_key = str(market.get("conditionId") or market.get("id") or "")
        if not market_key:
            return None

        n = len(outcomes)
        # Yes-token bid/ask/last (index 0); No-side derived via complement identity.
        yes_bid = _dec(market.get("bestBid"))
        yes_ask = _dec(market.get("bestAsk"))
        yes_last = _dec(market.get("lastTradePrice"))

        # Complement identity: no_bid = 1−yes_ask, no_ask = 1−yes_bid (binary only).
        no_bid: Decimal | None = complement(yes_ask) if yes_ask is not None else None
        no_ask: Decimal | None = complement(yes_bid) if yes_bid is not None else None
        no_last: Decimal | None = complement(yes_last) if yes_last is not None else None

        if n == 2:
            best_bids: list[Decimal | None] = [yes_bid, no_bid]
            best_asks: list[Decimal | None] = [yes_ask, no_ask]
            last_trades: list[Decimal | None] = [yes_last, no_last]
        else:
            # Non-standard outcome count: fill remaining slots with None.
            best_bids = [yes_bid] + [None] * (n - 1)
            best_asks = [yes_ask] + [None] * (n - 1)
            last_trades = [yes_last] + [None] * (n - 1)

        # Volume/volume_total are market-level (both outcomes share the same market).
        vol_24h = _dec(market.get("volume24hr"))
        vol_total = _dec(market.get("volumeNum") or market.get("volume"))
        outcome_volumes_24h: list[Decimal | None] = [vol_24h] * n
        outcome_volumes_total: list[Decimal | None] = [vol_total] * n

        # open_interest: use event-level value threaded in from _event_to_refs.
        open_interests: list[Decimal | None] = [event_open_interest] * n

        # Prefer the child market's own question as the title: in multi-strike price
        # events the event title is a template ("Bitcoin above ___ on June 24?") while
        # each child's `question` carries the actual strike + direction
        # ("Will the price of Bitcoin be above $60,000 on June 24?"), which downstream
        # relative-value matching needs. Falls back to the event title when absent.
        market_title = str(market.get("question") or event_title)

        return MarketRef(
            venue=VENUE,
            event_id=event_id,
            market_key=market_key,
            event_title=market_title,
            outcomes=outcomes,
            token_ids=token_ids,
            condition_id=market.get("conditionId"),
            resolved=bool(market.get("closed", False)),
            enable_order_book=bool(market.get("enableOrderBook", True)),
            volume=_dec(market.get("volume") or market.get("volumeNum")),
            liquidity=_dec(market.get("liquidity") or market.get("liquidityNum")),
            topic=topic,
            quoted_prices=prices or None,
            best_bids=best_bids,
            best_asks=best_asks,
            last_trades=last_trades,
            outcome_volumes_24h=outcome_volumes_24h,
            outcome_volumes_total=outcome_volumes_total,
            open_interests=open_interests,
            close_date=parse_iso_datetime(market.get("endDate") or market.get("endDateIso")),
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise SchemaDriftError(f"gamma market shape unexpected: {exc}") from exc


def _multi_outcome_ref(
    event: dict, active: list[dict], *, topic: str
) -> MarketRef | None:
    """One MarketRef for a mutually-exclusive event: each active child contributes
    one outcome (label = groupItemTitle, probability = its Yes price). token_ids is
    left empty so pricing uses these quick-read prices rather than N CLOB calls.

    Per-candidate bid/ask/last come from each child market's bestBid/bestAsk/
    lastTradePrice fields.  open_interest comes from event-level ``openInterest``
    (same value broadcast to all outcomes — the canonical multi-outcome source).
    """
    outcomes: list[str] = []
    prices: list[Decimal] = []
    best_bids: list[Decimal | None] = []
    best_asks: list[Decimal | None] = []
    last_trades: list[Decimal | None] = []
    vols_24h: list[Decimal | None] = []
    vols_total: list[Decimal | None] = []

    for m in active:
        op = _as_list(m.get("outcomePrices"))
        yes_price = _dec(op[0]) if op else None
        if yes_price is None:
            continue
        label = m.get("groupItemTitle") or m.get("question") or m.get("slug")
        outcomes.append(str(label))
        prices.append(yes_price)
        best_bids.append(_dec(m.get("bestBid")))
        best_asks.append(_dec(m.get("bestAsk")))
        last_trades.append(_dec(m.get("lastTradePrice")))
        vols_24h.append(_dec(m.get("volume24hr")))
        vols_total.append(_dec(m.get("volumeNum") or m.get("volume")))

    if not outcomes:
        return None

    # open_interest: use event-level value (same for all outcomes in a negRisk event).
    event_oi = _dec(event.get("openInterest"))
    open_interests: list[Decimal | None] = [event_oi] * len(outcomes)

    event_id = str(event.get("id", ""))
    return MarketRef(
        venue=VENUE,
        event_id=event_id,
        market_key=event_id,
        event_title=str(event.get("title", topic)),
        outcomes=outcomes,
        token_ids=[],  # grouped: use quoted prices, skip per-candidate CLOB
        resolved=False,
        enable_order_book=False,
        volume=_dec(event.get("volume")),
        liquidity=_dec(event.get("liquidity")),
        topic=topic,
        quoted_prices=prices,
        best_bids=best_bids,
        best_asks=best_asks,
        last_trades=last_trades,
        outcome_volumes_24h=vols_24h,
        outcome_volumes_total=vols_total,
        open_interests=open_interests,
        close_date=parse_iso_datetime(event.get("endDate")),
    )


def _event_to_refs(event: dict, *, topic: str) -> list[MarketRef]:
    # Category is left None here: Polymarket `tags` is a free-form folksonomy
    # (subjects, market-types, and internal flags mixed together), not a clean
    # taxonomy, so it is not a reliable category source. The ingestion service
    # applies the curated CATEGORY_MAP instead (see ingestion_service).
    markets = [m for m in event.get("markets", []) or [] if isinstance(m, dict)]
    active = [m for m in markets if not bool(m.get("closed", False))]
    if not active:
        return []
    event_id = str(event.get("id", ""))
    event_title = str(event.get("title", topic))

    if bool(event.get("negRisk", False)) and len(active) > 1:
        ref = _multi_outcome_ref(event, active, topic=topic)
        return [ref] if ref is not None else []

    event_oi = _dec(event.get("openInterest"))
    refs: list[MarketRef] = []
    for m in active:
        ref = _binary_ref(
            m,
            event_id=event_id,
            event_title=event_title,
            topic=topic,
            event_open_interest=event_oi,
        )
        if ref is not None:
            refs.append(ref)
    return refs


async def discover(client: httpx.AsyncClient, topic: str, *, limit: int = 50) -> list[MarketRef]:
    """Discover active Polymarket markets matching ``topic``, grouped by event."""
    payload = await fetch_json(
        client,
        "/public-search",
        venue=VENUE,
        limiter=_limiter,
        params={"q": topic, "limit_per_type": str(limit), "events_status": "active"},
    )
    if not isinstance(payload, dict) or "events" not in payload:
        raise SchemaDriftError("gamma /public-search missing 'events'")

    refs: list[MarketRef] = []
    for event in payload["events"]:
        if isinstance(event, dict):
            refs.extend(_event_to_refs(event, topic=topic))
    logger.info("gamma.discover", extra={"topic": topic, "markets": len(refs)})
    return refs
