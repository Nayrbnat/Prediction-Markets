"""Offline lead-lag study #2: for PM-NATIVE, equity-relevant events (recession, rate cuts,
government shutdown, tariffs), does a CHANGE in the Polymarket event probability LEAD or LAG
the matched equity index move?

Motivation (see RESEARCH.md / CHANGES_BASED_SIGNAL_RESEARCH.md): on crypto, where a deep
derivative prices the SAME underlying, the derivative LEADS Polymarket by ~1h. The open claim
is that for events equities care about but cannot price directly (no future/option on
"recession" or "government shutdown"), the prediction market is the only real-time aggregator
and may therefore LEAD the equity reaction. This harness tests that claim empirically.

Data (all free, no key; same Yahoo chart endpoint the Fed source uses, verified live):
  - Polymarket CLOB  /prices-history?market=<YES token>&interval=max&fidelity=60  -> hourly prob
  - Yahoo Finance    /v8/finance/chart/<ETF>?interval=60m&range=730d              -> hourly close

Both are floored to the hour and inner-joined (=> only equity RTH hours survive; PM is 24/7).
Convention for the cross-correlation corr(dPM_t, dEQ_{t-lag}):
    lag > 0  => equity LEADS the prediction market   (PM is the laggard)
    lag < 0  => prediction market LEADS the equity    (the claim we are testing)
We difference only across CONTIGUOUS hourly bars (gap == 1h) so overnight/weekend gaps do not
pollute the changes. Sign of the contemporaneous corr is reported (it encodes the economic
direction, e.g. recession-prob up <-> equity down => negative), so pairs are NOT pooled across
different signs — each market is read standalone.

Caveat baked in: equity 60m bars start at :30 past the hour; flooring introduces <=30min of
alignment slack, which biases AGAINST detecting a clean 1h lead (so a positive finding is
conservative). And SPY variance is dominated by everything OTHER than the single event, so the
shared-news component — and hence |r| — is expected to be small, unlike the crypto same-underlying
case. Power is the binding constraint here, not bias.

READ-ONLY research harness, not deployed. Run:
    .venv/Scripts/python.exe -m research.equity_leadlag
"""

from __future__ import annotations

import asyncio
import json
import time
import warnings

warnings.filterwarnings("ignore")
from dataclasses import dataclass, field

import httpx
import numpy as np
import pandas as pd

from research._common import _granger_p, _xcorr_lead, HOUR

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
YAHOO = "https://query1.finance.yahoo.com"
_UA = {"User-Agent": "Mozilla/5.0 (research; lead-lag study)"}

# Curated PM-native, equity-relevant topics. Each maps to ONE OR MORE liquid proxies. We test the
# broad index (SPY) AND, where one exists, the SHARPEST event-specific instrument whose own
# variance is most event-driven (=> highest shared-news share => most statistical power):
#   - rate cuts        -> TLT  (20y+ Treasuries: the cleanest liquid bet on rate expectations)
#   - US x China tariff -> FXI  (China large-cap ETF: directly exposed to the trade outcome)
# `expect` is the sign we expect for corr(dPM, dProxy-return) on the SHARP proxy (sanity check).
TOPICS = [
    {"query": "recession", "match": ("recession",), "etfs": ("SPY",), "expect": "neg"},
    {"query": "government shutdown", "match": ("shutdown",), "etfs": ("SPY",), "expect": "neg"},
    {"query": "fed rate cut", "match": ("rate cut", "cut rates", "fed decision", "basis point"),
     "etfs": ("TLT", "SPY"), "expect": "pos"},
    {"query": "tariff china", "match": ("tariff", "china"), "etfs": ("FXI", "SPY"), "expect": "neg"},
    {"query": "trump powell", "match": ("powell",), "etfs": ("SPY",), "expect": "neg"},
]


@dataclass
class Mkt:
    title: str
    yes_token: str
    etfs: tuple[str, ...]
    expect: str
    volume: float
    pm: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))


def _as_list(value: object) -> list:
    if isinstance(value, str):
        try:
            v = json.loads(value)
        except json.JSONDecodeError:
            return []
        return v if isinstance(v, list) else []
    return value if isinstance(value, list) else []


def _num(v: object) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


async def _discover(client: httpx.AsyncClient, topic: dict, *, top_n: int = 3) -> list[Mkt]:
    """Most-liquid active BINARY markets for a topic (a single YES token to track)."""
    r = await client.get(
        f"{GAMMA}/public-search",
        params={"q": topic["query"], "limit_per_type": "100", "events_status": "active"},
    )
    if r.status_code != 200:
        return []
    found: list[Mkt] = []
    seen: set[str] = set()
    for event in r.json().get("events", []) or []:
        for m in event.get("markets", []) or []:
            if bool(m.get("closed", False)):
                continue
            title = m.get("question") or event.get("title") or ""
            low = title.lower()
            if not any(h in low for h in topic["match"]):
                continue
            toks = _as_list(m.get("clobTokenIds"))
            outcomes = _as_list(m.get("outcomes"))
            if len(toks) != 2 or (outcomes and len(outcomes) != 2):
                continue  # keep it a clean binary Yes/No so token[0] is unambiguously "Yes"
            key = str(m.get("conditionId") or m.get("id") or toks[0])
            if key in seen:
                continue
            seen.add(key)
            vol = _num(m.get("volumeNum") or m.get("volume") or m.get("volume24hr"))
            found.append(Mkt(title=title, yes_token=str(toks[0]), etfs=tuple(topic["etfs"]),
                             expect=topic["expect"], volume=vol))
    found.sort(key=lambda x: x.volume, reverse=True)
    return found[:top_n]


async def _pm_hourly(client: httpx.AsyncClient, token: str) -> pd.Series:
    r = await client.get(f"{CLOB}/prices-history",
                         params={"market": token, "interval": "max", "fidelity": 60})
    pts = r.json().get("history", []) if r.status_code == 200 else []
    if not pts:
        return pd.Series(dtype=float)
    idx = [int(p["t"]) // HOUR * HOUR for p in pts]
    return pd.Series([float(p["p"]) for p in pts], index=idx).groupby(level=0).last()


async def _equity_hourly(client: httpx.AsyncClient, symbol: str) -> pd.Series:
    r = await client.get(f"{YAHOO}/v8/finance/chart/{symbol}",
                         params={"interval": "60m", "range": "730d"}, headers=_UA)
    if r.status_code != 200:
        return pd.Series(dtype=float)
    try:
        res = r.json()["chart"]["result"][0]
        ts = res["timestamp"]
        close = res["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return pd.Series(dtype=float)
    pairs = [(int(t) // HOUR * HOUR, float(c)) for t, c in zip(ts, close) if c is not None]
    if not pairs:
        return pd.Series(dtype=float)
    idx, vals = zip(*pairs)
    return pd.Series(vals, index=idx).groupby(level=0).last()


def _contiguous_diffs(s: pd.Series) -> np.ndarray:
    """First differences only across adjacent hourly bars (gap == 1h); else NaN -> dropped.

    Returns the change vector aligned to the *later* bar of each contiguous pair.
    """
    idx = np.asarray(s.index)
    val = s.values
    d = np.diff(val)
    gaps = np.diff(idx)
    return d[gaps == HOUR]


def _aligned_changes(pm: pd.Series, eq: pd.Series) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Inner-join on the hourly grid, return (dPM, dEQ_logret, contiguous timestamps-of-later-bar)."""
    df = pd.DataFrame({"pm": pm, "eq": eq}).dropna().sort_index()
    if len(df) < 3:
        return np.array([]), np.array([]), np.array([])
    idx = np.asarray(df.index)
    gaps = np.diff(idx)
    keep = gaps == HOUR
    dpm = np.diff(df["pm"].values)[keep]
    deq = np.diff(np.log(df["eq"].values))[keep]
    return dpm, deq, idx[1:][keep]


def analyse(m: Mkt, sym: str, eq: pd.Series) -> dict:
    out: dict = {"title": m.title[:70], "etf": sym, "volume": round(m.volume), "expect": m.expect}
    dpm, deq, _ = _aligned_changes(m.pm, eq)
    out["n_changes"] = int(len(dpm))
    if len(dpm) < 40:
        out["skip"] = "too few aligned RTH-hour changes (<40)"
        return out
    if dpm.std() == 0 or deq.std() == 0:
        out["skip"] = "pinned/dead series (zero variance in changes)"
        return out
    out["corr_changes"] = round(float(np.corrcoef(dpm, deq)[0, 1]), 3)
    # corr(dPM_t, dEQ_{t-lag}); lag>0 => equity leads PM, lag<0 => PM leads equity
    lag, c = _xcorr_lead(dpm, deq, max_lag=6)
    out["xcorr_peak_lag_h"] = lag
    out["xcorr_peak_r"] = round(c, 3)
    out["lead"] = ("equity_leads_pm" if lag > 0 else
                   "pm_leads_equity" if lag < 0 else "contemporaneous")
    d2 = pd.DataFrame({"pm": dpm, "eq": deq})
    out["granger_pm_causes_eq_p"] = round(_granger_p(d2, "pm", "eq", maxlag=4), 4)
    out["granger_eq_causes_pm_p"] = round(_granger_p(d2, "eq", "pm", maxlag=4), 4)
    return out


async def run() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        # equity proxies first (one fetch each)
        etfs = sorted({sym for t in TOPICS for sym in t["etfs"]})
        eq_series: dict[str, pd.Series] = {}
        for sym in etfs:
            eq_series[sym] = await _equity_hourly(client, sym)
            print(f"[equity] {sym} hourly RTH bars = {len(eq_series[sym])}")

        markets: list[Mkt] = []
        for topic in TOPICS:
            found = await _discover(client, topic)
            print(f"[discover] {topic['query']!r}: {len(found)} liquid binary markets")
            for m in found:
                m.pm = await _pm_hourly(client, m.yes_token)
                print(f"   - vol={m.volume:>12,.0f}  pm_hours={len(m.pm):>4}  {m.title[:64]!r}")
                markets.append(m)

    print("\n" + "#" * 80)
    print("LEAD-LAG: dPM vs dEQ(logret).  lag>0 equity leads PM | lag<0 PM leads equity")
    print("#" * 80)
    results = [analyse(m, sym, eq_series.get(sym, pd.Series(dtype=float)))
               for m in markets for sym in m.etfs]
    pm_leads = eq_leads = contemp = 0
    for r in results:
        print("  " + "-" * 76)
        print(f"  {r['title']}  [{r['etf']}, vol~{r.get('volume')}, expect {r['expect']}]")
        if "skip" in r:
            print(f"     SKIP: {r['skip']} (n={r.get('n_changes')})")
            continue
        for k in ("n_changes", "corr_changes", "xcorr_peak_lag_h", "xcorr_peak_r", "lead",
                  "granger_pm_causes_eq_p", "granger_eq_causes_pm_p"):
            print(f"     {k:24} {r[k]}")
        if r["lead"] == "pm_leads_equity":
            pm_leads += 1
        elif r["lead"] == "equity_leads_pm":
            eq_leads += 1
        else:
            contemp += 1
    print("\n" + "=" * 80)
    print(f"VERDICT across {pm_leads+eq_leads+contemp} analysed markets: "
          f"PM leads equity = {pm_leads} | equity leads PM = {eq_leads} | contemporaneous = {contemp}")
    print("Read |r| too: if peak |r| < ~0.05 the lead direction is noise, not signal.")


if __name__ == "__main__":
    asyncio.run(run())
