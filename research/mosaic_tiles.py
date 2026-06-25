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
import re

import httpx
import pandas as pd

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
HOUR = 3600

# Sector watch-list for bottom-up mosaic use. Each term is BOTH a search query and a
# word-boundary title filter (so "sap" != "soap", "meta" != "metaverse"). A market is
# bucketed into the first sector (in this order) whose term matches its title.
SECTORS: dict[str, list[str]] = {
    "Tech / AI mega-cap": [
        "nvidia", "apple", "microsoft", "google", "alphabet", "meta", "amazon", "tesla",
        "openai", "anthropic", "perplexity", "tiktok", "spacex", "xai", "bytedance",
        "largest company", "best ai", "ai model", "agi",
    ],
    "Luxury": [
        "lvmh", "hermes", "louis vuitton", "gucci", "kering", "luxury", "chanel",
        "rolex", "prada", "richemont", "birkin", "cartier",
    ],
    "Alcohol / spirits": [
        "diageo", "pernod", "pernod ricard", "constellation brands", "anheuser",
        "budweiser", "heineken", "molson", "alcohol", "spirits", "whiskey", "whisky",
    ],
    "Enterprise software": [
        "adobe", "constellation software", "salesforce", "oracle", "sap", "figma",
        "canva", "servicenow", "workday", "atlassian", "datadog", "snowflake",
        "databricks", "intuit", "shopify",
    ],
    "Cybersecurity": [
        "palo alto", "crowdstrike", "fortinet", "zscaler", "sentinelone",
        "cybersecurity", "cyberark", "okta", "cloudflare", "darktrace",
    ],
}

# Title patterns that mean the match is NOT a corporate/fundamental tile (sports, esports,
# entertainment, people) — e.g. "KT Wiz" (baseball), "Shopify Rebellion" (esports), a movie,
# a person named Chanel. Drop these so a name-collision never masquerades as a sector tile.
_BLOCK = re.compile(
    r"\bvs\.?\b|\bkbo\b|valorant|\bbo[35]\b|\bvcl\b|wears prada|president of|"
    r"\bfilm\b|\bmovie\b|\bacademy\b|\bleague\b|\bmatch\b|grand prix",
    re.IGNORECASE,
)


def _sector_of(title: str) -> str | None:
    if _BLOCK.search(title):
        return None
    low = title.lower()
    for sector, terms in SECTORS.items():
        for t in terms:
            if re.search(r"\b" + re.escape(t) + r"\b", low):
                return sector
    return None


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


async def _gather(client: httpx.AsyncClient) -> dict[str, dict]:
    """Search every sector term, bucket each active binary market by sector. token -> tile-stub."""
    out: dict[str, dict] = {}
    terms = {t for terms in SECTORS.values() for t in terms}
    for q in sorted(terms):
        try:
            r = await client.get(f"{GAMMA}/public-search",
                                 params={"q": q, "limit_per_type": "100", "events_status": "active"})
        except httpx.HTTPError:
            continue
        if r.status_code != 200:
            continue
        for ev in r.json().get("events", []) or []:
            for m in ev.get("markets", []) or []:
                if bool(m.get("closed", False)):
                    continue
                title = m.get("question") or ev.get("title") or ""
                sector = _sector_of(title)
                if not sector:
                    continue
                toks = _as_list(m.get("clobTokenIds"))
                if len(toks) != 2:
                    continue
                tok = str(toks[0])
                vol = _num(m.get("volumeNum") or m.get("volume"))
                if tok not in out or vol > out[tok]["vol"]:
                    out[tok] = {"title": title, "token": tok, "vol": vol, "sector": sector}
    return out


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


TOP_PER_SECTOR = 12


async def run() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        stubs = await _gather(client)
        # group by sector, keep the deepest TOP_PER_SECTOR per sector, then fetch history
        by_sector: dict[str, list[dict]] = {s: [] for s in SECTORS}
        for st in stubs.values():
            by_sector[st["sector"]].append(st)
        tiles: dict[str, list[dict]] = {}
        for sector, lst in by_sector.items():
            lst.sort(key=lambda z: z["vol"], reverse=True)
            picked = []
            for st in lst[:TOP_PER_SECTOR]:
                s = await _hourly(client, st["token"])
                if s.empty:
                    continue
                picked.append({**st, "now": float(s.iloc[-1]),
                               "d7": _delta(s, 7 * 24), "d30": _delta(s, 30 * 24)})
            tiles[sector] = picked

    print("\n" + "=" * 100)
    print("MOSAIC TILES by sector — directional reads (level + momentum + credibility), NOT a quant signal")
    print("=" * 100)
    for sector in SECTORS:
        picked = tiles.get(sector, [])
        print(f"\n### {sector}  ({len(picked)} live market{'s' if len(picked) != 1 else ''})")
        if not picked:
            print("    (no live Polymarket markets — the crowd offers NO tile here; rely on your own work)")
            continue
        for r in sorted(picked, key=lambda z: z["vol"], reverse=True):
            d7 = f"{r['d7']*100:+.0f}pp" if r["d7"] is not None else " n/a"
            d30 = f"{r['d30']*100:+.0f}pp" if r["d30"] is not None else " n/a"
            print(f"  [{_credibility(r['vol'])}] {int(round(r['now']*100)):>3}%  7d {d7:>6}  30d {d30:>6}  "
                  f"| {r['title'][:62]}")
    print("\n" + "-" * 100)
    print("Read: gate on credibility (DEEP/solid = real crowd money; THIN = a whisper). The highest-value")
    print("tile is a DEEP one that DISAGREES with your bottom-up view. Mind resolution clauses (a falling")
    print("'acquired before 2027' can be time-decay, not a weakening thesis).")


if __name__ == "__main__":
    asyncio.run(run())
