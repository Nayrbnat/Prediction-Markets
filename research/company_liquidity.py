"""Offline probe: how liquid are COMPANY-specific bets on Kalshi + Polymarket, and
how many are even comparable to a tradeable derivative?  Feeds RESEARCH-style analysis
of whether company bets can be monetized.

Reuses the production sources (no new parsing):
  - Kalshi  "Companies" category: /series -> per-series events -> nested markets
    (volume_fp / open_interest_fp; liquidity_dollars is deprecated upstream = useless).
  - Polymarket: gamma.discover(company_name) -> refs with volume / liquidity.

Classifies each market: price/market-cap (=> equity-options comparable), M&A/deal
(=> merger-arb comparable), else KPI/corporate-event (PM-native, no derivative).
Then reports the liquidity distribution per bucket. READ-ONLY. Run:
    .venv/Scripts/python.exe -m research.company_liquidity
"""

from __future__ import annotations

import asyncio
import statistics
from decimal import Decimal

from app.config import get_settings
from app.core.http import fetch_json, make_client
from app.core.rate_limit import AsyncRateLimiter
from app.sources import kalshi, polymarket_gamma

_lim = AsyncRateLimiter(rate_per_sec=8.0)

PRICE = ("$", "market cap", "valuation", "most valuable", "share price", "stock price", "all-time high")
MNA = ("acquire", "acquisition", "merger", "merge", "buyout", "buy ", "take over", "takeover", "deal close")


def _kind(title: str) -> str:
    low = title.lower()
    if any(h in low for h in MNA):
        return "M&A/deal"
    if any(h in low for h in PRICE):
        return "price/mktcap"
    return "KPI/event"


def _oi(ref) -> float:
    vals = [float(x) for x in (ref.open_interests or []) if x is not None]
    return max(vals) if vals else 0.0


def _vol(ref) -> float:
    return float(ref.volume) if ref.volume is not None else 0.0


async def _kalshi(client) -> list:
    """All markets across every series in the configured Kalshi company categories."""
    out = []
    for category in get_settings().company_kalshi_category_list:
        payload = await fetch_json(client, "/series", venue="kalshi", limiter=_lim,
                                   params={"category": category})
        series = [s.get("ticker") for s in (payload.get("series") or []) if s.get("ticker")]
        print(f"  Kalshi category '{category}': {len(series)} series")
        for i, tk in enumerate(series):
            try:
                refs = await kalshi._events_for_series(client, str(tk), topic="company", limit=200)
                out.extend(refs)
            except Exception as exc:  # noqa: BLE001
                print(f"    !{tk}: {type(exc).__name__}")
            if (i + 1) % 40 == 0:
                print(f"    ...{i+1}/{len(series)} series scanned, {len(out)} markets so far")
    return out


async def _polymarket(client) -> list:
    out = []
    for name in get_settings().company_name_list:
        try:
            out.extend(await polymarket_gamma.discover(client, name, limit=30))
        except Exception as exc:  # noqa: BLE001
            print(f"    !{name}: {type(exc).__name__}")
    return out


def _summary(label: str, refs: list, vol_floor: float) -> None:
    print("\n" + "=" * 74)
    print(f"{label}: {len(refs)} markets")
    if not refs:
        return
    by_kind: dict[str, list] = {}
    for r in refs:
        by_kind.setdefault(_kind(r.event_title), []).append(r)
    for kind in ("price/mktcap", "M&A/deal", "KPI/event"):
        group = by_kind.get(kind, [])
        if not group:
            print(f"  {kind:14} 0")
            continue
        vols = sorted(_vol(r) for r in group)
        ois = sorted(_oi(r) for r in group)
        dead = sum(1 for v in vols if v < vol_floor)
        print(f"  {kind:14} n={len(group):3}  "
              f"vol[median={statistics.median(vols):,.0f} max={vols[-1]:,.0f}]  "
              f"OI[median={statistics.median(ois):,.0f} max={ois[-1]:,.0f}]  "
              f"dead(<{vol_floor:,.0f})={dead}/{len(group)} ({100*dead/len(group):.0f}%)")
    # show the most-liquid handful overall
    top = sorted(refs, key=_vol, reverse=True)[:8]
    print(f"  most-liquid {label} markets (by volume):")
    for r in top:
        print(f"    {_vol(r):>12,.0f}  [{_kind(r.event_title)[:10]:10}] {r.event_title[:62]}")


async def main() -> None:
    s = get_settings()
    async with make_client(base_url=s.kalshi_base_url) as kc, make_client(base_url=s.gamma_base_url) as gc:
        print("[company-liquidity] scanning Kalshi company series + Polymarket company searches...")
        kalshi_refs = await _kalshi(kc)
        pm_refs = await _polymarket(gc)
    # Kalshi vol is fixed-point CONTRACTS (24h-preferred); PM volume is USD. Floors differ.
    _summary("KALSHI (volume = contracts, 24h-pref)", kalshi_refs, vol_floor=1000)
    _summary("POLYMARKET (volume = USD, total)", pm_refs, vol_floor=10000)


if __name__ == "__main__":
    asyncio.run(main())
