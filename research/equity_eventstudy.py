"""Offline lead-lag study #3 (catalyst-day EVENT STUDY): the rolling hourly regression in
research.equity_leadlag found ~zero co-movement because it averaged over quiet hours where a
PM-native event barely touches the broad index. This harness instead CONDITIONS on the moments
that matter — the hours where the Polymarket probability JUMPS — and measures the equity around
them. Two tests:

  A. INTRADAY RACE (both markets open). On PM-catalyst hours (|dPM| large) where the equity is
     also trading, compute corr(dPM_t, dEQ_{t+k}) at lags k=-H..H, POOLED across markets with
     per-market sign alignment. Peak at k>0 => equity reacts AFTER the PM jump (PM leads);
     k<0 => equity already moved (equity leads).

  B. OVERNIGHT INFORMATION (the tradeable one). When the PM moves hard WHILE equities are closed
     (gap between consecutive equity bars > 2h), does the equity GAP in the matching direction at
     the next open?  We correlate the closed-window dPM with the next-open equity gap-return,
     pooled with per-market sign alignment. A significant positive pooled corr => the prediction
     market carries information equities have not yet priced => a real, tradeable edge.

Data (free, no key; same endpoints verified in studies #1/#2):
  - Polymarket CLOB /prices-history?market=<YES>&interval=max&fidelity=60 -> ~1 month hourly prob
  - Yahoo /v8/finance/chart/<ETF>?interval=60m&range=730d                 -> hourly RTH closes

Sign alignment for pooling: each PM-native event has its own economic sign (recession-prob up
<-> equity down; rate-cut-prob up <-> equity up). We flip each market by the sign of its own
contemporaneous catalyst correlation so all markets contribute coherently to the pooled timing
profile (we are testing the TIMING/lead, not the sign).

READ-ONLY research harness, not deployed. Run:
    .venv/Scripts/python.exe -m research.equity_eventstudy
"""

from __future__ import annotations

import asyncio
import warnings

warnings.filterwarnings("ignore")

import httpx
import numpy as np
import pandas as pd
from scipy.stats import t as tdist

from research.equity_leadlag import (HOUR, TOPICS, Mkt, _aligned_changes, _discover,
                                     _equity_hourly, _pm_hourly)

H = 4               # event-window half-width in hours (intraday test)
PM_JUMP_PP = 0.02   # a "catalyst": |dPM| over one hour >= 2 percentage points
ON_JUMP_PP = 0.01   # overnight test: only consider closed windows with |dPM| >= 1pp


# --------------------------------------------------------------------------- grids
def _pm_grid(pm: pd.Series) -> pd.Series:
    """Forward-fill the PM probability onto a complete hourly grid (PM trades 24/7)."""
    if pm.empty:
        return pm
    full = list(range(int(pm.index.min()), int(pm.index.max()) + HOUR, HOUR))
    return pm.reindex(full).ffill()


def _eq_intraday(eq: pd.Series) -> tuple[dict[int, float], list[tuple[int, int, float]]]:
    """Return (intraday log-returns keyed at the bar's hour for CONTIGUOUS bars,
    list of overnight gaps as (prev_close_ts, open_ts, gap_logreturn))."""
    eq = eq.sort_index()
    idx = np.asarray(eq.index, dtype=np.int64)
    px = eq.values
    if len(idx) < 3:
        return {}, []
    gaps = np.diff(idx)
    lr = np.diff(np.log(px))
    intraday = {int(idx[i + 1]): float(lr[i]) for i in range(len(lr)) if gaps[i] == HOUR}
    overnight = [(int(idx[i]), int(idx[i + 1]), float(lr[i]))
                 for i in range(len(lr)) if gaps[i] > 2 * HOUR]
    return intraday, overnight


def _r_p(x: np.ndarray, y: np.ndarray) -> tuple[float, float, int]:
    n = len(x)
    if n < 8 or np.std(x) == 0 or np.std(y) == 0:
        return float("nan"), float("nan"), n
    r = float(np.corrcoef(x, y)[0, 1])
    if abs(r) >= 1.0:
        return r, 0.0, n
    tstat = r * np.sqrt((n - 2) / (1 - r * r))
    p = float(2 * tdist.sf(abs(tstat), n - 2))
    return round(r, 3), round(p, 4), n


# --------------------------------------------------------------------------- economic sign
def _econ_sign(m: Mkt, eq: pd.Series) -> float:
    """Sign of the contemporaneous dPM<->dEQ relationship over the FULL intraday sample.

    Used only to ALIGN markets for pooling. It is estimated from all RTH-contiguous hourly
    changes (study #2's sample), which is largely DISJOINT from the overnight-gap events of
    Test B — so using it to align Test B does not pick the sign from the statistic under test
    (avoids the circularity of in-sample sign-fitting). Defaults to +1 when undetermined.
    """
    dpm, deq, _ = _aligned_changes(m.pm, eq)
    if len(dpm) < 20 or np.std(dpm) == 0 or np.std(deq) == 0:
        return 1.0
    return float(np.sign(np.corrcoef(dpm, deq)[0, 1]) or 1.0)


# --------------------------------------------------------------------------- test A
def _intraday_pairs(m: Mkt, eq: pd.Series) -> dict[int, list[tuple[float, float]]]:
    """RAW catalyst pairs (dPM_t, dEQ_{t+k}) per lag k in [-H,H]. No sign alignment here."""
    grid = _pm_grid(m.pm)
    if grid.empty:
        return {}
    intraday, _ = _eq_intraday(eq)
    if not intraday:
        return {}
    dpm = grid.diff()
    times = np.asarray(grid.index, dtype=np.int64)
    cats = [int(t) for t in times
            if abs(dpm.get(t, np.nan)) >= PM_JUMP_PP and int(t) in intraday]
    if len(cats) < 8:
        return {}
    pairs: dict[int, list[tuple[float, float]]] = {}
    for t in cats:
        for k in range(-H, H + 1):
            deq = intraday.get(t + k * HOUR)
            if deq is not None:
                pairs.setdefault(k, []).append((float(dpm[t]), float(deq)))
    return pairs


# --------------------------------------------------------------------------- test B
def _overnight_pairs(m: Mkt, eq: pd.Series) -> list[tuple[float, float]]:
    """RAW (closed-window dPM, next-open gap-return) for big overnight PM moves. No alignment."""
    grid = _pm_grid(m.pm)
    if grid.empty:
        return []
    _, overnight = _eq_intraday(eq)
    raw: list[tuple[float, float]] = []
    for prev_close_ts, open_ts, gap_ret in overnight:
        p0 = grid.get(prev_close_ts)
        p1 = grid.get(open_ts)
        if p0 is None or p1 is None:
            continue
        dpm = float(p1 - p0)
        if abs(dpm) >= ON_JUMP_PP:
            raw.append((dpm, gap_ret))
    return raw


# --------------------------------------------------------------------------- run
async def run() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        etfs = sorted({sym for t in TOPICS for sym in t["etfs"]})
        eq_series = {sym: await _equity_hourly(client, sym) for sym in etfs}
        for sym in etfs:
            print(f"[equity] {sym} hourly RTH bars = {len(eq_series[sym])}")
        markets: list[Mkt] = []
        for topic in TOPICS:
            found = await _discover(client, topic)
            for mk in found:
                mk.pm = await _pm_hourly(client, mk.yes_token)
                markets.append(mk)
        print(f"[discover] {len(markets)} candidate markets")

    # independent economic sign per (market, proxy), for pooling alignment only
    signs = {(id(m), sym): _econ_sign(m, eq_series.get(sym, pd.Series(dtype=float)))
             for m in markets for sym in m.etfs}

    # ---- Test A: intraday race (pooled, aligned by independent sign) ----
    pooled_A: dict[int, list[tuple[float, float]]] = {}
    contributing_A = 0
    for m in markets:
        for sym in m.etfs:
            pr = _intraday_pairs(m, eq_series.get(sym, pd.Series(dtype=float)))
            if pr:
                contributing_A += 1
                s = signs[(id(m), sym)]
                for k, lst in pr.items():
                    pooled_A.setdefault(k, []).extend((a, s * b) for a, b in lst)

    print("\n" + "#" * 80)
    print(f"TEST A — INTRADAY RACE on PM-catalyst hours (|dPM|>={PM_JUMP_PP:.0%}), both open")
    print(f"         pooled across {contributing_A} market/proxy series, aligned by independent sign")
    print("         corr(dPM_t, dEQ_{t+k}):  k>0 equity reacts AFTER PM (PM leads) | k<0 equity leads")
    print("#" * 80)
    by_lag = {}
    peak = (0, 0.0, 1.0, 0)
    for k in range(-H, H + 1):
        lst = pooled_A.get(k, [])
        if len(lst) < 20:
            continue
        x = np.array([a for a, _ in lst]); y = np.array([b for _, b in lst])
        r, p, n = _r_p(x, y)
        by_lag[k] = (r, p, n)
        flag = " *" if (p == p and p < 0.05) else ""
        print(f"   lag {k:+d}h  r={r:+.3f}  p={p}  n={n}{flag}")
        if r == r and abs(r) > abs(peak[1]):
            peak = (k, r, p, n)
    if by_lag:
        lead = ("PM_leads_equity" if peak[0] > 0 else
                "equity_leads_PM" if peak[0] < 0 else "contemporaneous")
        print(f"   => peak at lag {peak[0]:+d}h  r={peak[1]:+.3f}  p={peak[2]}  ({lead})")
        print("   (lag-0 strength tells you if there is ANY shared move to even time)")

    # ---- Test B: overnight information (per market RAW; pooled aligned by independent sign) ----
    pooled_B: list[tuple[float, float]] = []
    per_market_B = []
    for m in markets:
        for sym in m.etfs:
            pr = _overnight_pairs(m, eq_series.get(sym, pd.Series(dtype=float)))
            if len(pr) >= 8:
                x = np.array([a for a, _ in pr]); y = np.array([b for _, b in pr])
                per_market_B.append((m.title[:46], sym, *_r_p(x, y)))
                s = signs[(id(m), sym)]
                pooled_B.extend((a, s * b) for a, b in pr)

    print("\n" + "#" * 80)
    print(f"TEST B — OVERNIGHT INFORMATION: big closed-window dPM (|dPM|>={ON_JUMP_PP:.0%}) vs next-open gap")
    print("         POSITIVE pooled corr => PM predicts the equity open => tradeable edge")
    print("#" * 80)
    for title, sym, r, p, n in sorted(per_market_B, key=lambda z: z[4], reverse=True):
        flag = " *" if (p == p and p < 0.05) else ""
        print(f"   {title:46} [{sym}]  r={r:+}  p={p}  n={n}{flag}")
    if len(pooled_B) >= 20:
        x = np.array([a for a, _ in pooled_B]); y = np.array([b for _, b in pooled_B])
        r, p, n = _r_p(x, y)
        print("   " + "-" * 72)
        print(f"   POOLED (aligned by independent sign): r={r:+}  p={p}  n={n}  "
              f"{'<-- SIGNIFICANT' if (p == p and p < 0.05) else '(not significant)'}")
    else:
        print(f"   too few overnight catalyst events to pool (n={len(pooled_B)})")

    print("\nNOTE: per-market r is RAW — its SIGN is the economic-sense check (e.g. recession-prob up")
    print("vs SPY down => negative). The pooled r is aligned by an INDEPENDENT sign (full intraday")
    print("sample, disjoint from these overnight events), so a significant pooled r is not circular.")


if __name__ == "__main__":
    asyncio.run(run())
