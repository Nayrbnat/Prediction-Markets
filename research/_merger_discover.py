"""Quick discovery probe: what live Polymarket M&A / deal-completion markets exist, how liquid
are they, and how much hourly history is available? READ-ONLY. Decides whether a merger-arb
lead-lag test (target stock vs PM deal-prob) is even feasible before building it.
    .venv/Scripts/python.exe -m research._merger_discover
"""
from __future__ import annotations

import asyncio
import json

import httpx

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
HOUR = 3600

QUERIES = ["acquire", "acquisition", "merger", "buyout", "take private", "deal", "Paramount",
           "Electronic Arts", "EA", "Skydance", "Discover", "Capital One", "Hess", "Juniper"]
MNA = ("acquire", "acquisition", "merger", "merge", "buyout", "buy ", "take over",
       "takeover", "take private", "deal close", "complete its", "go private")


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


async def _pm_hours(client: httpx.AsyncClient, token: str) -> int:
    r = await client.get(f"{CLOB}/prices-history",
                         params={"market": token, "interval": "max", "fidelity": 60})
    return len(r.json().get("history", [])) if r.status_code == 200 else 0


async def main() -> None:
    seen: set[str] = set()
    rows = []
    async with httpx.AsyncClient(timeout=30) as client:
        for q in QUERIES:
            r = await client.get(f"{GAMMA}/public-search",
                                 params={"q": q, "limit_per_type": "100", "events_status": "active"})
            if r.status_code != 200:
                continue
            for ev in r.json().get("events", []) or []:
                for m in ev.get("markets", []) or []:
                    if bool(m.get("closed", False)):
                        continue
                    title = m.get("question") or ev.get("title") or ""
                    low = title.lower()
                    if not any(h in low for h in MNA):
                        continue
                    toks = _as_list(m.get("clobTokenIds"))
                    if len(toks) != 2:
                        continue
                    key = str(m.get("conditionId") or m.get("id") or toks[0])
                    if key in seen:
                        continue
                    seen.add(key)
                    vol = _num(m.get("volumeNum") or m.get("volume"))
                    rows.append((vol, title, str(toks[0])))
        rows.sort(reverse=True)
        print(f"found {len(rows)} live M&A/deal binary markets\n")
        for vol, title, tok in rows[:30]:
            hrs = await _pm_hours(client, tok)
            print(f"  vol={vol:>12,.0f}  pm_hours={hrs:>4}  {title[:74]}")


if __name__ == "__main__":
    asyncio.run(main())
