"""Offline lead-lag study #4 (MERGER / TAKEOVER): for Polymarket acquisition markets whose
TARGET is a public company, does a CHANGE in the PM acquisition-probability LEAD or LAG the
target stock's takeover-premium move?

Why this is a sharper test than study #2/#3 (PM-native macro vs SPY): for a single-name target,
takeover speculation is a LARGE fraction of the stock's idiosyncratic variance — so, unlike the
broad index, the stock and the PM are pricing substantially the SAME thing (deal completion),
the way Deribit and the crypto PM did. High shared variance => the lead-lag is actually resolvable.

IMPORTANT scope note discovered live: Polymarket's "M&A" markets are overwhelmingly acquisition
RUMOR markets ("Will X be acquired before 2027?"), NOT announced deals with public offer terms.
Classic merger-arb (P_close = (price-downside)/(offer-downside)) needs an announced offer, which
mostly does NOT exist here. So this tests the tradeable LEAD-LAG question (who moves first on deal
news), not the textbook spread. The one genuine contested-deal target present is Warner Bros (WBD).

Targets are mapped to REAL tickers (verifiable; no fabricated terms). Data (free, no key):
  - Polymarket CLOB /prices-history?market=<YES>&interval=max&fidelity=60 -> hourly acq-prob
  - Yahoo /v8/finance/chart/<TICKER>?interval=60m&range=730d              -> hourly stock close

Convention: corr(dPM_t, dStock_{t-lag});  lag>0 => stock LEADS the PM | lag<0 => PM LEADS stock.
READ-ONLY research harness. Run:
    .venv/Scripts/python.exe -m research.merger_arb
"""
from __future__ import annotations

import asyncio
import json

import httpx
import numpy as np
import pandas as pd
from scipy.stats import t as tdist

from research._common import _granger_p, _xcorr_lead
from research.equity_leadlag import _aligned_changes, _equity_hourly, _pm_hourly

GAMMA = "https://gamma-api.polymarket.com"

# PM-title substring -> (target ticker, label). Public targets only; tickers are factual lookups.
# "acquired" Yes => takeover premium => stock UP, so we expect a POSITIVE contemporaneous corr.
TARGETS = {
    "nebius": ("NBIS", "Nebius acquired"),
    "viking therapeutics": ("VKTX", "Viking Therapeutics acquired"),
    "gitlab": ("GTLB", "GitLab acquired"),
    "zoom": ("ZM", "Zoom acquired"),
    "paypal": ("PYPL", "PayPal acquired"),
    "snapchat": ("SNAP", "Snap acquired"),
    "bp ": ("BP", "BP acquired"),
    "warner bros": ("WBD", "Warner Bros (target) deal closes"),
}
QUERIES = ["acquired", "acquire", "merger", "warner bros", "paypal", "nebius", "viking",
           "gitlab", "zoom", "snapchat", "BP"]


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


def _target_for(title: str) -> tuple[str, str] | None:
    low = title.lower()
    for key, (tk, label) in TARGETS.items():
        if key in low:
            return tk, label
    return None


async def _discover(client: httpx.AsyncClient) -> list[dict]:
    """Most-liquid binary acquisition market per mapped target (one YES token each)."""
    best: dict[str, dict] = {}
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
                tgt = _target_for(title)
                if not tgt:
                    continue
                toks = _as_list(m.get("clobTokenIds"))
                if len(toks) != 2:
                    continue
                tk, label = tgt
                vol = _num(m.get("volumeNum") or m.get("volume"))
                if tk not in best or vol > best[tk]["vol"]:
                    best[tk] = {"ticker": tk, "label": label, "title": title,
                                "token": str(toks[0]), "vol": vol}
    return list(best.values())


def _r_p(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    n = len(x)
    if n < 8 or np.std(x) == 0 or np.std(y) == 0:
        return float("nan"), float("nan")
    r = float(np.corrcoef(x, y)[0, 1])
    if abs(r) >= 1:
        return r, 0.0
    tstat = r * np.sqrt((n - 2) / (1 - r * r))
    return round(r, 3), round(float(2 * tdist.sf(abs(tstat), n - 2)), 4)


def analyse(d: dict, pm: pd.Series, stock: pd.Series) -> dict:
    out = {"ticker": d["ticker"], "label": d["label"], "vol": round(d["vol"]),
           "pm_now": round(float(pm.iloc[-1]), 3) if len(pm) else None}
    dpm, dst, _ = _aligned_changes(pm, stock)  # dst = stock log-returns, RTH-contiguous
    out["n"] = int(len(dpm))
    if len(dpm) < 40:
        out["skip"] = "too few aligned RTH-hour changes (<40)"
        return out
    r0, p0 = _r_p(dpm, dst)
    out["corr_changes"], out["corr_p"] = r0, p0
    lag, c = _xcorr_lead(dpm, dst, max_lag=6)
    out["peak_lag_h"], out["peak_r"] = lag, round(c, 3)
    out["lead"] = ("stock_leads_pm" if lag > 0 else "pm_leads_stock" if lag < 0 else "contemporaneous")
    d2 = pd.DataFrame({"pm": dpm, "st": dst})
    out["granger_stock_causes_pm_p"] = round(_granger_p(d2, "st", "pm", maxlag=4), 4)
    out["granger_pm_causes_stock_p"] = round(_granger_p(d2, "pm", "st", maxlag=4), 4)
    return out


async def run() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        deals = await _discover(client)
        print(f"[discover] {len(deals)} public-target acquisition markets mapped")
        tickers = sorted({d["ticker"] for d in deals})
        stocks = {tk: await _equity_hourly(client, tk) for tk in tickers}
        for tk in tickers:
            print(f"   stock {tk}: {len(stocks[tk])} hourly RTH bars")
        results = []
        for d in deals:
            pm = await _pm_hourly(client, d["token"])
            print(f"   pm {d['ticker']:5} hours={len(pm):>4}  vol={d['vol']:>12,.0f}  {d['title'][:60]!r}")
            results.append(analyse(d, pm, stocks.get(d["ticker"], pd.Series(dtype=float))))

    print("\n" + "#" * 80)
    print("MERGER/TAKEOVER LEAD-LAG: dPM(acq) vs dStock.  lag>0 stock leads PM | lag<0 PM leads stock")
    print("#" * 80)
    s_lead = pm_lead = 0
    for r in results:
        print("  " + "-" * 76)
        print(f"  {r['label']:32} [{r['ticker']}]  vol~{r['vol']}  pm_now={r['pm_now']}")
        if "skip" in r:
            print(f"     SKIP: {r['skip']} (n={r['n']})")
            continue
        for k in ("n", "corr_changes", "corr_p", "peak_lag_h", "peak_r", "lead",
                  "granger_stock_causes_pm_p", "granger_pm_causes_stock_p"):
            print(f"     {k:26} {r[k]}")
        if r["lead"] == "stock_leads_pm":
            s_lead += 1
        elif r["lead"] == "pm_leads_stock":
            pm_lead += 1
    print("\n" + "=" * 80)
    print(f"VERDICT: stock leads PM = {s_lead} | PM leads stock = {pm_lead}  "
          f"(read corr_p / |r|: only trust where contemporaneous corr is real)")


if __name__ == "__main__":
    asyncio.run(run())
