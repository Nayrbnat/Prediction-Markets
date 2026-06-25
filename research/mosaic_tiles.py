"""Mosaic-theory readout: turn Polymarket markets into DIRECTIONAL TILES for bottom-up
fundamental thinking — NOT a quant signal. For each market we report the crowd's current
real-money probability, its recent momentum (where the view is moving), and a credibility
flag (depth). The point is direction + a place to dig, not statistical confirmation.

A tile is high-value when it (a) is credible (deep, real money) and (b) either confirms or —
more usefully — DISAGREES with your fundamental view, telling you where to focus diligence.

Data (free, no key): Polymarket CLOB /prices-history?market=<YES>&interval=max&fidelity=60.
READ-ONLY. Run:
    .venv/Scripts/python.exe -m research.mosaic_tiles
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pandas as pd

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
HOUR = 3600

# Curated watch-list: deals + macro catalysts that feed bottom-up theses. (query, title-filter)
WATCH = [
    ("nebius acquired", ("nebius",)),
    ("viking therapeutics acquired", ("viking",)),
    ("gitlab acquired", ("gitlab",)),
    ("zoom acquired", ("zoom",)),
    ("paypal acquired", ("paypal",)),
    ("snapchat acquired", ("snapchat",)),
    ("BP acquired", ("bp ",)),
    ("warner bros", ("warner bros",)),
    ("paramount warner", ("warner bros",)),
    ("US recession 2026", ("recession",)),
    ("government shutdown", ("shutdown",)),
    ("fed rate cut 2026", ("rate cut", "fed decision")),
    ("tariff china", ("tariff", "china")),
]


def _as_list(v: object) -> list:
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, list) else []
        except json.JSONDecodeError:
            return []
    return v if isinstance(v, list) else []


def _num(v: object) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


async def _best_market(client: httpx.AsyncClient, query: str, filt: tuple[str, ...]) -> dict | None:
    r = await client.get(f"{GAMMA}/public-search",
                         params={"q": query, "limit_per_type": "100", "events_status": "active"})
    if r.status_code != 200:
        return None
    best = None
    for ev in r.json().get("events", []) or []:
        for m in ev.get("markets", []) or []:
            if bool(m.get("closed", False)):
                continue
            title = m.get("question") or ev.get("title") or ""
            if not any(h in title.lower() for h in filt):
                continue
            toks = _as_list(m.get("clobTokenIds"))
            if len(toks) != 2:
                continue
            vol = _num(m.get("volumeNum") or m.get("volume"))
            if best is None or vol > best["vol"]:
                best = {"title": title, "token": str(toks[0]), "vol": vol}
    return best


async def _hourly(client: httpx.AsyncClient, token: str) -> pd.Series:
    r = await client.get(f"{CLOB}/prices-history",
                         params={"market": token, "interval": "max", "fidelity": 60})
    pts = r.json().get("history", []) if r.status_code == 200 else []
    if not pts:
        return pd.Series(dtype=float)
    idx = [int(p["t"]) // HOUR * HOUR for p in pts]
    return pd.Series([float(p["p"]) for p in pts], index=idx).groupby(level=0).last()


def _delta(s: pd.Series, hours: int) -> float | None:
    if len(s) < 2:
        return None
    last_t = int(s.index[-1])
    past = s[s.index <= last_t - hours * HOUR]
    if past.empty:
        return None
    return float(s.iloc[-1] - past.iloc[-1])


def _credibility(vol: float) -> str:
    if vol >= 1_000_000:
        return "DEEP   "
    if vol >= 100_000:
        return "solid  "
    if vol >= 10_000:
        return "moderate"
    return "THIN!  "


def _read(now: float, d7: float | None, d30: float | None, vol: float) -> str:
    arrow = "flat"
    ref = d30 if d30 is not None else d7
    if ref is not None:
        if ref >= 0.10:
            arrow = "RISING hard"
        elif ref >= 0.03:
            arrow = "rising"
        elif ref <= -0.10:
            arrow = "FALLING hard"
        elif ref <= -0.03:
            arrow = "falling"
    conf = "low-conviction (thin — treat as a hint)" if vol < 10_000 else "credible crowd read"
    return f"{int(round(now*100))}% and {arrow} -> {conf}"


async def run() -> None:
    rows = []
    async with httpx.AsyncClient(timeout=30) as client:
        for query, filt in WATCH:
            mk = await _best_market(client, query, filt)
            if not mk:
                print(f"   (no live market for {query!r})")
                continue
            s = await _hourly(client, mk["token"])
            if s.empty:
                continue
            rows.append({"title": mk["title"], "vol": mk["vol"], "now": float(s.iloc[-1]),
                         "d7": _delta(s, 7 * 24), "d30": _delta(s, 30 * 24)})

    # de-dupe identical titles, keep deepest
    uniq: dict[str, dict] = {}
    for r in rows:
        if r["title"] not in uniq or r["vol"] > uniq[r["title"]]["vol"]:
            uniq[r["title"]] = r

    print("\n" + "=" * 96)
    print("MOSAIC TILES — directional reads (level + momentum + credibility), NOT a quant signal")
    print("=" * 96)
    print(f"{'cred':8}  {'now':>4}  {'Δ7d':>6}  {'Δ30d':>6}   directional read / where to dig")
    print("-" * 96)
    for r in sorted(uniq.values(), key=lambda z: z["vol"], reverse=True):
        d7 = f"{r['d7']*100:+.0f}pp" if r["d7"] is not None else "  n/a"
        d30 = f"{r['d30']*100:+.0f}pp" if r["d30"] is not None else "  n/a"
        print(f"{_credibility(r['vol'])}  {int(round(r['now']*100)):>3}%  {d7:>6}  {d30:>6}   "
              f"{r['title'][:54]}")
        print(f"{'':8}  -> {_read(r['now'], r['d7'], r['d30'], r['vol'])}")
    print("-" * 96)
    print("Use: a DEEP tile that DISAGREES with your bottom-up view is the highest-value one — it")
    print("tells you the crowd sees something you don't (or vice versa). Thin tiles = weak hints only.")


if __name__ == "__main__":
    asyncio.run(run())
