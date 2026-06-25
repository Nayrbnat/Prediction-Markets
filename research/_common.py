"""Shared, dependency-free econometric helpers for the research harnesses in this folder.

Kept self-contained so the equity / merger studies do not depend on research/leadlag.py
(the crypto study, which lives on a separate branch). Research-only; venv deps via
research/requirements.txt; never imported by the deployed app.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import grangercausalitytests

HOUR = 3600


def _xcorr_lead(a: np.ndarray, b: np.ndarray, max_lag: int = 6) -> tuple[int, float]:
    """Peak lag of corr(a_t, b_{t-lag}); lag>0 => b LEADS a. Returns (lag, signed r)."""
    best_lag, best = 0, 0.0
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x, y = a[lag:], b[: len(b) - lag] if lag else b
        else:
            x, y = a[: len(a) + lag], b[-lag:]
        n = min(len(x), len(y))
        if n < 10 or np.std(x[:n]) == 0 or np.std(y[:n]) == 0:
            continue
        c = float(np.corrcoef(x[:n], y[:n])[0, 1])
        if abs(c) > abs(best):
            best, best_lag = c, lag
    return best_lag, best


def _granger_p(df2: pd.DataFrame, cause: str, effect: str, maxlag: int = 4) -> float:
    """Min p-value that `cause` Granger-causes `effect` over lags 1..maxlag (ssr F-test)."""
    data = df2[[effect, cause]].values  # grangercausalitytests: col0 caused BY col1
    try:
        res = grangercausalitytests(data, maxlag=maxlag, verbose=False)
    except Exception:  # noqa: BLE001
        return float("nan")
    return min(res[l][0]["ssr_ftest"][1] for l in res)
