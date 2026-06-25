# Research Summary — Monetizing the PM↔Derivative Signal

**Date:** 2026-06-25
**Author:** research session (Claude Code)
**Status:** research complete for crypto lead-lag; productionization not started.

This is the executive summary. Full detail and citations live in two companion notes:
- `MONETIZATION_AND_RISK_NEUTRAL_BRIDGE.md` — the levels problem (risk-neutral vs real-world).
- `CHANGES_BASED_SIGNAL_RESEARCH.md` — the changes/lead-lag program + full Step-2 results.

---

## 1. The question

The system compares, for the same event, a **prediction-market** probability (Polymarket/Kalshi) against
a **derivative-implied** probability (CME / Deribit / CBOE). Can that comparison be monetized — and has the
gap between **risk-neutral** (derivative) and **real-world** (true) probabilities actually been bridged?

## 2. Finding 1 — the *levels* can't be compared cleanly (and that's the literature's verdict, not ours)

- A price-implied probability is **risk-neutral (Q)**; the true **real-world (P)** probability differs by a
  **risk premium**. The wedge is small for front-end rates, medium for crypto, large for equity downside/vol.
- **No method bridges Q→P for free.** The one that claimed to — **Ross's Recovery Theorem (2015)** — is
  **empirically rejected** (Jackwerth–Menner 2020; Borovička et al. show it implicitly assumes away the
  permanent pricing-kernel component). What works (exponential/pricing-kernel tilting, or historical
  calibration) needs an extra assumption injected.
- **Consequence:** presenting the raw level gap as "mispricing" is wrong — it's mostly risk premium +
  behavioral bias. This matches the constitution's "decision-support, never arbitrage" stance.

## 3. Finding 2 — the *changes* CAN be compared (the reframing that unlocks everything)

If `Q = P + premium` and the premium is slow-moving, then in **first differences the premium cancels**:
`ΔQ ≈ ΔP`. So the derivative's *move* and the PM's *move* are comparable estimates of the same news-driven
change. The question becomes **price discovery: whose change leads?** — measured with Granger causality,
cross-correlation, and Gonzalo–Granger / Hasbrouck information shares. This needs **no Q→P bridge.**

## 4. KEY RESULT — Step 2 run on live data (2026-06-25)

The persisted store is **daily** (3 snapshots, Fed-only pairing) — useless for a sub-daily lead-lag. So we
**backfilled trailing hourly history** from free endpoints (Polymarket `/prices-history`; Deribit option
chart, Black-Scholes-inverted to Φ(d2); Deribit spot) and pooled all matched daily crypto pairs.
Reproduce: `pip install -r research/requirements.txt && python -m research.leadlag --coin BTC`.

**The derivative leads the prediction market by ~1 hour. ΔDeriv Granger-causes ΔPM; the reverse is weak.**

| Pooled, first differences | **BTC** | **ETH** |
|---|---|---|
| Matched daily pairs / pooled hours | 30 / 1,952 | 37 / 2,294 |
| Peak cross-corr `corr(ΔPM_t, ΔDeriv_{t−1})` | **0.684 @ lag +1h** | **0.402 @ lag +1h** |
| Contemporaneous (lag 0) | 0.05 | 0.09 |
| **Granger ΔDeriv → ΔPM** | **p ≈ 0.000** | **p ≈ 0.000** |
| Granger ΔPM → ΔDeriv | 0.076 (weak) | 0.0018 (minor) |

**Artifact-proofed.** A 1h lead with ~0 contemporaneous correlation could be a timestamp mismatch. Using
Deribit **spot** as the common driver (spot and option share one clock): `Δoption` reacts to spot at **lag 0**
(BTC r=0.47, ETH r=0.45) while `ΔPM` reacts at **lag +1h** (BTC r=0.50, ETH r=0.27; lag-0 ≈ 0.06–0.09). The
PM lags spot while the option does not → **real price-discovery latency, not a labeling offset.** Both coins agree.

**Where it holds:** strongest **near the money** — BTC `$64k` r=0.93, `$66k` 0.85; Granger deriv→pm p≈0.000;
Gonzalo–Granger component share **deriv-dominant (0.6–0.98)**; **gap half-life sub-hour to ~1.5h**. It
**degrades to noise deep-OTM** (favorite-longshot behavior + unstable BS inversion where prob≈0) — don't
trust the tails.

**Interpretation:** the deep, fast crypto derivatives/spot market leads Polymarket by ~1 hour. The
actionable form — *"Deribit-implied P(>K) just moved and Polymarket hasn't → expect PM to converge within
~1–2h"* — is empirically supported. Part of the lag is PM **illiquidity**, which is why it's exploitable but
means execution must respect PM depth.

## 5. Monetization read

- **Trade the convergence directly:** real edge, but 2026 Polymarket arb windows last ~2.7s, ~73% bot-
  captured, books too thin to deploy $100 in 77% of episodes. The daily, read-only system is on the wrong
  side of that race; trading also conflicts with the read-only mandate (§2).
- **Sell the raw divergence feed:** legitimate but least-differentiated and decays via crowding.
- **★ Sell the de-biased / lead-lag signal:** the defensible product — a cross-venue **innovation monitor**
  ("leader moved, laggard hasn't → laggard follows within the half-life") plus calibration-adjusted
  real-world probabilities. Read-only, constitution-compliant, and built on history we already persist.

---

## 6. What still needs to be done

**Validation**
- [ ] **Out-of-sample re-test** on a different window — confirm the ~1h lead is stable, not a June-2026 quirk.
- [ ] **Regime test** — does the lead widen / the `Δπ≈0` assumption break during vol/risk-appetite spikes?
- [ ] **Mid vs last-trade** — Polymarket `prices-history` is last-trade-driven; quantify how much of the lag
      is illiquidity vs genuine slow information incorporation (affects whether it's tradeable net of depth).

**Coverage gaps (data-blocked today)**
- [ ] **Fed & Nasdaq lead-lag** — no free *intraday* derivative feed (CME ZQ daily-only; CBOE snapshot-only),
      so the lead is **unproven** outside crypto. Needs a paid/added intraday source or going-forward capture.
- [ ] **Polymarket vs Kalshi** lead-lag on the same crypto event (both have free intraday history) as a
      cross-PM anchor (replicates Ng et al. on our own data).

**Productionization (only after validation)**
- [ ] **Intraday ingestion** — the daily cron can't capture a 1-hour lead; add multi-stamp/day snapshots for
      the crypto pairs (schema PK currently `(snapshot_date, …)` is daily — needs an intraday key).
- [ ] **Live signal emission** — compute ΔDeriv vs ΔPM per tracked event and flag "derivative moved, PM
      hasn't; expected convergence ~Xpp within ~1–2h," with the near-money-only confidence flag.
- [ ] **Calibration layer** — map both legs to realized frequencies (domain-specific) so output is a
      de-biased real-world probability with a confidence interval and a gap decomposition.

**Engineering**
- [ ] Promote `research/leadlag.py` to a tested `app/analysis/` module if/when productionized (pure-math unit
      tests, mocked I/O); keep numpy/scipy/statsmodels **out of the deployed bundle** (research-only deps).
- [ ] Branch + PR per change; never push to main.

**Paused (per user):** visualization (divergence-over-time, calibration curve, gap-decomposition waterfall).

---

## 7. References (for revisiting this question)

**Most directly on point — prediction markets vs. derivatives / cross-venue price discovery**
- *Do Prediction Markets Match Option Prices? Bitcoin Threshold Evidence from Binance and Polymarket* (2026) —
  the closest study to our exact setup (PM thresholds vs. option-implied probs; ~6pp gap, 4.2h half-life,
  marginal post-cost alpha). https://arxiv.org/html/2606.19517
- Ng, Peng, Tao & Zhou, *Price Discovery and Trading in Modern Prediction Markets*, SSRN 5331995 (Apr 2026) —
  Polymarket leads Kalshi; large-trade order imbalance predicts returns; the leader carries price discovery.
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5331995
- *LLM as a Risk Manager: LLM Semantic Filtering for Lead-Lag Trading in Prediction Markets* (2026) — crypto
  Granger-causes political PMs; lead-lag trading with a filter for spurious correlation.
  https://arxiv.org/pdf/2602.07048
- *The Anatomy of a Decentralized Prediction Market: Microstructure Evidence from the Polymarket Order Book*
  (2026) — leaves the PM-vs-derivative lead-lag explicitly open; spreads/depth/maker stats.
  https://arxiv.org/html/2604.24366v1
- *Decomposing Crowd Wisdom: Domain-Specific Calibration Dynamics in Prediction Markets* (2026) — changes carry
  more info than levels; crypto worst-calibrated, economics best. https://arxiv.org/pdf/2602.19520
- *Market Efficiency in Prediction Markets — A Comparison with Derivatives* — https://www.aifinconf.org/file/2025/7-1.pdf
- *Political Shocks and Price Discovery in Prediction Markets: 2024 U.S. Election* (2026) — https://arxiv.org/html/2603.03152
- QuantPedia, *Systematic Edges in Prediction Markets* — https://quantpedia.com/systematic-edges-in-prediction-markets/

**Risk-neutral ↔ real-world conversion / the Recovery Theorem debate**
- Ross, *The Recovery Theorem*, Journal of Finance (2015) — https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12092
  (open PDF: https://bpb-us-w2.wpmucdn.com/u.osu.edu/dist/7/36891/files/2017/07/Ross2015-27fd9du.pdf)
- Jackwerth & Menner, *Does the Ross Recovery Theorem work empirically?*, JFE (2020) — the empirical rejection
  (and the Borovička et al. critique discussion). https://www.sciencedirect.com/science/article/pii/S0304405X20300763
- *Estimation of option-implied risk-neutral into real-world density using a calibration function* —
  https://www.researchgate.net/publication/316550510
- *Estimating real-world probabilities: A forward-looking behavioral framework* — https://arxiv.org/pdf/2012.09041
- Bank of England WP (1997), *Implied risk-neutral PDFs from option prices* —
  https://www.bankofengland.co.uk/working-paper/1997/implied-risk-neutral-probability-density-functions-from-option-prices
- *Option-based Equity Risk Premiums* — https://arxiv.org/pdf/1910.14522
- *The relationship between risk-neutral and actual default probabilities: the credit risk premium* —
  https://www.tandfonline.com/doi/full/10.1080/00036846.2016.1150953

**Risk premium is time-varying (why ΔQ≈ΔP can break in stress) + variance risk premium**
- Bekaert, Engstrom & Xu, *The Time Variation in Risk Appetite and Uncertainty*, Management Science (2022) —
  https://pubsonline.informs.org/doi/10.1287/mnsc.2021.4068
- Bollerslev, Tauchen & Zhou, *Expected Stock Returns and Variance Risk Premia* (Fed FEDS 2007 / RFS 2009) —
  https://www.federalreserve.gov/pubs/feds/2007/200711/200711pap.pdf
- *The Option Trader's Guide to the Variance Risk Premium* (the 13%-implied vs 4%-realized drawdown example) —
  https://www.predictingalpha.com/the-option-traders-guide-to-the-variance-risk-premium/

**Price-discovery methodology (the tools used in Step 2)**
- Hasbrouck (1995) Information Share & Gonzalo–Granger (1995) Component Share — background via
  *Price Discovery in Cryptocurrency Markets* (2025): https://arxiv.org/pdf/2506.08718

**Polymarket trading frictions / arbitrage reality (2026)**
- *Beyond Simple Arbitrage: 4 Polymarket Strategies Bots Actually Profit From in 2026* —
  https://medium.com/illumination/beyond-simple-arbitrage-4-polymarket-strategies-bots-actually-profit-from-in-2026-ddacc92c5b4f
- *Arbitrage Analysis in Polymarket NBA Markets* (2026) — https://arxiv.org/html/2605.00864v1
- Polymarket Fees docs — https://docs.polymarket.com/trading/fees

**Data sources used by the Step-2 harness (free, no key)**
- Polymarket CLOB price history — `GET https://clob.polymarket.com/prices-history?market=<tokenId>&interval=max&fidelity=60`
- Polymarket Gamma search — `GET https://gamma-api.polymarket.com/public-search?q=bitcoin&events_status=active`
- Deribit option/instrument + hourly chart — `https://www.deribit.com/api/v2/public/get_instruments` and
  `…/get_tradingview_chart_data?instrument_name=<opt|BTC-PERPETUAL>&resolution=60` (docs: https://docs.deribit.com)
