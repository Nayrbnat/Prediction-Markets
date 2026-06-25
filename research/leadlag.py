"""Offline lead-lag study: do CHANGES in a derivative-implied probability lead or lag
CHANGES in the matched prediction-market probability?  (Step 2 of CHANGES_BASED_SIGNAL_RESEARCH.md)

The persisted store is daily — useless for a ~4.2h-half-life lead-lag. So we BACKFILL trailing
hourly history from free endpoints (verified live 2026-06-24):
  - Polymarket CLOB  /prices-history?market=<YES token>&fidelity=60  -> {t, p}  (prob in [0,1])
  - Deribit get_tradingview_chart_data res=60 for the matched option -> hourly close (in coin)
  - Deribit get_tradingview_chart_data res=60 for <COIN>-PERPETUAL    -> hourly spot (USD)

The derivative leg is converted to a risk-neutral P(S_T>K) per hour via single-option
Black-Scholes inversion (S=spot, r=0, European call) -> Phi(d2)  — the BTC-paper method,
smoother than the 2-strike finite-difference digital used on the live path.

Then, on the aligned hourly series, in FIRST DIFFERENCES (so the slow risk premium cancels):
  - ADF stationarity (levels I(1)?  gap stationary => cointegrated)
  - lagged cross-correlation of d(pm), d(deriv)  -> sign of peak lag = who leads
  - Granger causality both directions
  - Gonzalo-Granger component share (VECM) = who carries the common factor
  - gap AR(1) half-life

This is a research harness, NOT deployed code. It only READS public endpoints. Run:
    .venv/Scripts/python.exe -m research.leadlag           # BTC
    .venv/Scripts/python.exe -m research.leadlag --coin ETH
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
import warnings

warnings.filterwarnings("ignore")
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm
from statsmodels.tsa.stattools import adfuller, grangercausalitytests
from statsmodels.tsa.vector_ar.vecm import VECM

from app.markets._shared.threshold_parse import deribit_token, is_terminal_above, parse_strike

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DERIBIT = "https://www.deribit.com"
YEAR_SECONDS = 365.0 * 24 * 3600
HOUR = 3600

COINS = {
    "BTC": {"aliases": ("bitcoin", "btc"), "perp": "BTC-PERPETUAL"},
    "ETH": {"aliases": ("ethereum", "eth"), "perp": "ETH-PERPETUAL"},
}


# --------------------------------------------------------------------------- discovery
@dataclass
class Pair:
    title: str
    yes_token: str
    strike: float
    expiry: datetime
    instrument: str  # matched Deribit option
    d_strike: float
    d_expiry: datetime


def _as_list(value: object) -> list:
    if isinstance(value, str):
        try:
            v = json.loads(value)
        except json.JSONDecodeError:
            return []
        return v if isinstance(v, list) else []
    return value if isinstance(value, list) else []


async def _gamma_btc_markets(client: httpx.AsyncClient, aliases: tuple[str, ...]) -> list[dict]:
    """Active Polymarket CHILD markets (terminal-above, with a Yes token) via /public-search.

    Mirrors the production source: search events, then walk each event's child markets — the
    child ``question`` carries the strike + direction, and each child has its own clobTokenIds
    even inside a grouped (negRisk) price event. Normalise to flat dicts the matcher expects.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for q in aliases:
        r = await client.get(
            f"{GAMMA}/public-search",
            params={"q": q, "limit_per_type": "100", "events_status": "active"},
        )
        if r.status_code != 200:
            continue
        for event in r.json().get("events", []) or []:
            ev_end = event.get("endDate")
            for m in event.get("markets", []) or []:
                if bool(m.get("closed", False)):
                    continue
                question = m.get("question") or ""
                low = question.lower()
                if not any(a in low for a in aliases) or not is_terminal_above(question):
                    continue
                toks = _as_list(m.get("clobTokenIds"))
                if not toks:
                    continue
                key = str(m.get("conditionId") or m.get("id") or toks[0])
                if key in seen:
                    continue
                seen.add(key)
                out.append({"question": question, "clobTokenIds": toks,
                            "endDate": m.get("endDate") or m.get("endDateIso") or ev_end})
    return out


async def _deribit_options(client: httpx.AsyncClient, coin: str) -> list[dict]:
    r = await client.get(
        f"{DERIBIT}/api/v2/public/get_instruments",
        params={"currency": coin, "kind": "option", "expired": "false"},
    )
    return [i for i in r.json().get("result", []) if i.get("option_type") == "call"]


def _yes_token(m: dict) -> str | None:
    raw = m.get("clobTokenIds")
    ids = json.loads(raw) if isinstance(raw, str) else raw
    return ids[0] if ids else None


def _match(markets: list[dict], options: list[dict], coin: str) -> list[Pair]:
    """Match each PM market to the Deribit call with the SAME expiry date and nearest strike."""
    # index deribit calls by expiry token -> [(strike, name, expiry_dt)]
    by_tok: dict[str, list[tuple[float, str, datetime]]] = {}
    for o in options:
        ts = o.get("expiration_timestamp")
        strike = o.get("strike")
        name = o.get("instrument_name")
        if not (ts and strike and name):
            continue
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        by_tok.setdefault(deribit_token(dt), []).append((float(strike), name, dt))

    pairs: list[Pair] = []
    for m in markets:
        end = m.get("endDate")
        token = _yes_token(m)
        strike = parse_strike(m.get("question") or "")
        if not (end and token and strike):
            continue
        try:
            exp = datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
        # Deribit options expire 08:00 UTC; PM close time differs — allow the exact UTC
        # date and ±1 day, then pick the option expiry closest to the PM close.
        cands = []
        for delta_days in (0, -1, 1):
            cands += by_tok.get(deribit_token(exp.fromtimestamp(exp.timestamp() + delta_days * 86400, tz=timezone.utc)), [])
        if not cands:
            continue  # no Deribit option expiring within ±1 day -> skip, never fudge
        # nearest expiry first, then nearest strike handled below
        cands.sort(key=lambda c: abs(c[2].timestamp() - exp.timestamp()))
        best_exp_ts = cands[0][2].date()
        cands = [c for c in cands if c[2].date() == best_exp_ts]
        k = float(strike)
        d_strike, name, d_exp = min(cands, key=lambda c: abs(c[0] - k))
        # require the nearest listed strike within 10% of the PM strike
        if abs(d_strike - k) / k > 0.10:
            continue
        pairs.append(Pair(m.get("question") or "", token, k, exp, name, d_strike, d_exp))
    return pairs


# --------------------------------------------------------------------------- backfill
async def _pm_history(client: httpx.AsyncClient, token: str, start: int, end: int) -> pd.Series:
    # NB: startTs/endTs 400s on these tokens (verified live); interval=max&fidelity=60 returns
    # the market's full hourly life, which we then intersect with the option window.
    r = await client.get(
        f"{CLOB}/prices-history",
        params={"market": token, "interval": "max", "fidelity": 60},
    )
    pts = r.json().get("history", []) if r.status_code == 200 else []
    if not pts:
        return pd.Series(dtype=float)
    idx = [int(p["t"]) // HOUR * HOUR for p in pts]
    return pd.Series([float(p["p"]) for p in pts], index=idx).groupby(level=0).last()


async def _deribit_chart(client: httpx.AsyncClient, instrument: str, start: int, end: int) -> pd.Series:
    r = await client.get(
        f"{DERIBIT}/api/v2/public/get_tradingview_chart_data",
        params={"instrument_name": instrument, "resolution": "60",
                "start_timestamp": start * 1000, "end_timestamp": end * 1000},
    )
    res = r.json().get("result", {})
    if res.get("status") != "ok" or not res.get("ticks"):
        return pd.Series(dtype=float)
    idx = [int(t) // 1000 // HOUR * HOUR for t in res["ticks"]]
    return pd.Series([float(c) for c in res["close"]], index=idx).groupby(level=0).last()


def _bs_call(s: float, k: float, t: float, sigma: float) -> float:
    if t <= 0 or sigma <= 0:
        return max(s - k, 0.0)
    d1 = (math.log(s / k) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    return s * norm.cdf(d1) - k * norm.cdf(d2)


def _prob_above(s: float, k: float, t: float, price_usd: float) -> float | None:
    """Invert BS for IV given the USD call price, return risk-neutral Phi(d2)=P(S_T>K)."""
    if t <= 0 or s <= 0 or price_usd <= 0:
        return None
    intrinsic, upper = max(s - k, 0.0), s
    if not (intrinsic + 1e-6 < price_usd < upper - 1e-9):
        return None  # outside no-arb bounds -> unreliable
    try:
        sigma = brentq(lambda v: _bs_call(s, k, t, v) - price_usd, 1e-4, 10.0, maxiter=100)
    except (ValueError, RuntimeError):
        return None
    d2 = (math.log(s / k) - 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    return float(norm.cdf(d2))


async def build_frame(client: httpx.AsyncClient, pair: Pair, spot: pd.Series, *, lookback_days: int) -> pd.DataFrame:
    now = int(time.time())
    start = now - lookback_days * 24 * 3600
    pm = await _pm_history(client, pair.yes_token, start, now)
    opt = await _deribit_chart(client, pair.instrument, start, now)
    if pm.empty or opt.empty:
        return pd.DataFrame()
    df = pd.DataFrame({"pm": pm, "opt_coin": opt, "spot": spot}).dropna()
    if df.empty:
        return df
    exp_s = pair.d_expiry.timestamp()
    probs = []
    for ts, row in df.iterrows():
        t = (exp_s - ts) / YEAR_SECONDS
        usd = row["opt_coin"] * row["spot"]
        probs.append(_prob_above(row["spot"], pair.d_strike, t, usd))
    df["deriv"] = probs
    return df.dropna()[["pm", "deriv", "spot"]]


# --------------------------------------------------------------------------- econometrics
def _half_life(gap: np.ndarray) -> float | None:
    g0, g1 = gap[:-1], gap[1:]
    if len(g0) < 10 or g0.std() == 0:
        return None
    phi = np.polyfit(g0 - g0.mean(), g1 - g1.mean(), 1)[0]
    return float(math.log(0.5) / math.log(phi)) if 0 < phi < 1 else None


def _xcorr_lead(dpm: np.ndarray, dde: np.ndarray, max_lag: int = 6) -> tuple[int, float]:
    """Peak lag of corr(dpm_t, dde_{t-lag}); lag>0 => derivative LEADS pm."""
    best_lag, best = 0, 0.0
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a, b = dpm[lag:], dde[: len(dde) - lag] if lag else dde
        else:
            a, b = dpm[: len(dpm) + lag], dde[-lag:]
        n = min(len(a), len(b))
        if n < 10:
            continue
        c = np.corrcoef(a[:n], b[:n])[0, 1]
        if abs(c) > abs(best):
            best, best_lag = c, lag
    return best_lag, best


def _granger_p(df2: pd.DataFrame, cause: str, effect: str, maxlag: int = 4) -> float:
    """Min p-value that `cause` Granger-causes `effect` over lags 1..maxlag."""
    data = df2[[effect, cause]].values  # grangercausalitytests: col0 caused BY col1
    try:
        res = grangercausalitytests(data, maxlag=maxlag, verbose=False)
    except Exception:  # noqa: BLE001
        return float("nan")
    return min(res[l][0]["ssr_ftest"][1] for l in res)


def _gg_component_share(levels: pd.DataFrame) -> tuple[float, float] | None:
    """Gonzalo-Granger common-factor weights (share of permanent component) for [pm, deriv]."""
    try:
        v = VECM(levels.values, k_ar_diff=2, coint_rank=1, deterministic="ci").fit()
        alpha = v.alpha.flatten()  # error-correction loadings
    except Exception:  # noqa: BLE001
        return None
    # GG weights are orthogonal to alpha: gamma_perp prop to (alpha_2, -alpha_1)
    g = np.array([alpha[1], -alpha[0]])
    s = np.abs(g).sum()
    if s == 0:
        return None
    w = np.abs(g) / s
    return float(w[0]), float(w[1])  # (pm share, deriv share)


def _pooled_xcorr(diffs: list[tuple[np.ndarray, np.ndarray]], max_lag: int = 6) -> dict:
    """corr(dpm_t, dderiv_{t-lag}) pooled across pairs (lag>0 => derivative LEADS pm)."""
    out = {}
    best_lag, best = 0, 0.0
    for lag in range(-max_lag, max_lag + 1):
        xs, ys = [], []
        for dpm, dde in diffs:
            if lag >= 0:
                a, b = dpm[lag:], dde[: len(dde) - lag] if lag else dde
            else:
                a, b = dpm[: len(dpm) + lag], dde[-lag:]
            n = min(len(a), len(b))
            if n > 0:
                xs.append(a[:n]); ys.append(b[:n])
        if not xs:
            continue
        a, b = np.concatenate(xs), np.concatenate(ys)
        if len(a) < 20 or a.std() == 0 or b.std() == 0:
            continue
        c = float(np.corrcoef(a, b)[0, 1])
        out[lag] = round(c, 3)
        if abs(c) > abs(best):
            best, best_lag = c, lag
    return {"by_lag": out, "peak_lag_h": best_lag, "peak_r": round(best, 3),
            "lead": "deriv_leads_pm" if best_lag > 0 else ("pm_leads_deriv" if best_lag < 0 else "contemporaneous")}


def _pooled_granger(diffs: list[tuple[np.ndarray, np.ndarray]], *, lags: int = 3) -> dict:
    """Boundary-respecting pooled Granger F-test, both directions, on differenced series.

    For effect e and cause c: unrestricted regresses e_t on {e_{t-1..L}, c_{t-1..L}},
    restricted drops the c block. Rows are built WITHIN each pair so no lag crosses a
    pair boundary, then stacked. Returns the F-test p-value that c improves e.
    """
    def _design(target: int, other: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        Y, Xr, Xu = [], [], []
        for d in diffs:
            e, c = d[target], d[other]
            n = len(e)
            for t in range(lags, n):
                ylag = [e[t - k] for k in range(1, lags + 1)]
                clag = [c[t - k] for k in range(1, lags + 1)]
                Y.append(e[t]); Xr.append([1.0, *ylag]); Xu.append([1.0, *ylag, *clag])
        return np.array(Y), np.array(Xr), np.array(Xu)

    def _ftest(target: int, other: int) -> float:
        Y, Xr, Xu = _design(target, other)
        if len(Y) < lags * 4 + 10:
            return float("nan")
        rss_r = float(((Y - Xr @ np.linalg.lstsq(Xr, Y, rcond=None)[0]) ** 2).sum())
        rss_u = float(((Y - Xu @ np.linalg.lstsq(Xu, Y, rcond=None)[0]) ** 2).sum())
        n, ku, q = len(Y), Xu.shape[1], lags
        if rss_u <= 0 or n - ku <= 0:
            return float("nan")
        from scipy.stats import f as fdist
        fstat = ((rss_r - rss_u) / q) / (rss_u / (n - ku))
        return float(fdist.sf(fstat, q, n - ku))

    # index 0 = pm, 1 = deriv
    return {"deriv_causes_pm_p": round(_ftest(0, 1), 4),
            "pm_causes_deriv_p": round(_ftest(1, 0), 4)}


def analyse(df: pd.DataFrame, title: str) -> dict:
    out: dict = {"title": title, "n": len(df)}
    if len(df) < 48:
        out["skip"] = "too few aligned hours (<48)"
        return out
    pm, de = df["pm"].values, df["deriv"].values
    gap = pm - de
    out["mean_gap_pp"] = round(float(gap.mean()) * 100, 2)
    out["corr_levels"] = round(float(np.corrcoef(pm, de)[0, 1]), 3)
    out["adf_gap_p"] = round(float(adfuller(gap, autolag="AIC")[1]), 4)
    out["gap_half_life_h"] = _half_life(gap)
    dpm, dde = np.diff(pm), np.diff(de)
    out["corr_changes"] = round(float(np.corrcoef(dpm, dde)[0, 1]), 3)
    lag, c = _xcorr_lead(dpm, dde)
    out["xcorr_peak_lag_h"] = lag
    out["xcorr_peak_r"] = round(c, 3)
    out["lead"] = "deriv_leads_pm" if lag > 0 else ("pm_leads_deriv" if lag < 0 else "contemporaneous")
    d2 = pd.DataFrame({"pm": dpm, "deriv": dde})
    out["granger_deriv_causes_pm_p"] = round(_granger_p(d2, "deriv", "pm"), 4)
    out["granger_pm_causes_deriv_p"] = round(_granger_p(d2, "pm", "deriv"), 4)
    gg = _gg_component_share(df[["pm", "deriv"]])
    if gg:
        out["gg_share_pm"], out["gg_share_deriv"] = round(gg[0], 3), round(gg[1], 3)
    return out


# --------------------------------------------------------------------------- main
async def run(coin: str, lookback_days: int) -> None:
    meta = COINS[coin]
    async with httpx.AsyncClient(timeout=30) as client:
        markets = await _gamma_btc_markets(client, meta["aliases"])
        options = await _deribit_options(client, coin)
        print(f"[{coin}] PM terminal-above markets={len(markets)}  Deribit live calls={len(options)}")
        pairs = _match(markets, options, coin)
        print(f"[{coin}] matched PM<->Deribit pairs (same expiry, strike within 10%)={len(pairs)}")
        for p in pairs:
            print(f"   - {p.title[:70]!r}  K=${p.strike:,.0f}~{p.d_strike:,.0f}  exp={p.expiry.date()} -> {p.instrument}")
        if not pairs:
            print(f"[{coin}] no matchable live pairs — nothing to analyse.")
            return

        now = int(time.time())
        start = now - lookback_days * 24 * 3600
        spot = await _deribit_chart(client, meta["perp"], start, now)
        print(f"[{coin}] spot hourly bars={len(spot)}")

        results, diffs, hours, spot_diffs = [], [], [], []
        for p in pairs:
            df = await build_frame(client, p, spot, lookback_days=lookback_days)
            if len(df) >= 12:  # enough to difference & contribute to the pool
                dpm, dde = np.diff(df["pm"].values), np.diff(df["deriv"].values)
                dsp = np.diff(np.log(df["spot"].values))
                diffs.append((dpm, dde))
                spot_diffs.append((dpm, dsp, dde))
                hours.append(len(df))
            if len(df) >= 48:  # rich enough for a standalone per-pair read
                results.append(analyse(df, p.title))

    print("\n" + "#" * 78)
    print(f"[{coin}] pairs contributing to pool={len(diffs)}  total pooled hours={sum(hours)}")
    if diffs:
        print(f"[{coin}] POOLED lead-lag (all matched daily pairs, changes/first-differences):")
        xc = _pooled_xcorr(diffs)
        gr = _pooled_granger(diffs)
        print(f"   cross-corr by lag (h): {xc['by_lag']}")
        print(f"   peak |r| at lag={xc['peak_lag_h']}h  r={xc['peak_r']}  => {xc['lead']}")
        print(f"   Granger deriv_leads_pm p={gr['deriv_causes_pm_p']}   pm_leads_deriv p={gr['pm_causes_deriv_p']}")
        # ARTIFACT CHECK: use Deribit SPOT (shares the option's timestamp convention) as the
        # common driver. If d(option) tracks d(spot) at lag 0 but d(PM) tracks d(spot) at
        # lag +1, the PM genuinely lags the market — not a PM-vs-option labeling offset.
        xc_pm_spot = _pooled_xcorr([(d[0], d[1]) for d in spot_diffs])   # dpm vs dspot
        xc_de_spot = _pooled_xcorr([(d[2], d[1]) for d in spot_diffs])   # dderiv vs dspot
        print("   [artifact check vs Deribit spot driver]")
        print(f"     d(PM)    vs d(spot): peak lag={xc_pm_spot['peak_lag_h']}h r={xc_pm_spot['peak_r']}  by_lag={xc_pm_spot['by_lag']}")
        print(f"     d(deriv) vs d(spot): peak lag={xc_de_spot['peak_lag_h']}h r={xc_de_spot['peak_r']}  by_lag={xc_de_spot['by_lag']}")
    print(f"\n[{coin}] standalone pairs (>=48h aligned)={len(results)}")
    for r in results:
        print("  " + "-" * 70)
        print(f"  {r['title'][:68]}")
        for k, v in r.items():
            if k != "title":
                print(f"     {k:28} {v}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", default="BTC", choices=list(COINS))
    ap.add_argument("--lookback-days", type=int, default=21)
    args = ap.parse_args()
    asyncio.run(run(args.coin, args.lookback_days))


if __name__ == "__main__":
    main()
