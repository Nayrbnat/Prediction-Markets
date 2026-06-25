# Trade the *Changes*, Not the Levels — A Research Program on Cross-Venue Probability Innovations

> **Premise (yours, and it's correct):** the *levels* of a derivative-implied probability (risk-neutral, Q)
> and a prediction-market probability (≈ real-world, P) cannot be directly compared — they are separated
> by an unbridged risk premium. But the **changes** can be, because the premium is slow-moving and
> **cancels in first differences.** This note works out *why* that holds, *what the changes tell us*, and
> *what to build*, anchored to the 2025–2026 literature.
>
> Companion to `MONETIZATION_AND_RISK_NEUTRAL_BRIDGE.md` (which covers the levels problem). Written
> 2026-06-24; every empirical figure is cited, not from memory.

---

## 0. TL;DR

1. **Differencing kills the premium.** Write `Q_t = P_t + π_t` (π = risk premium). Then
   `ΔQ_t = ΔP_t + Δπ_t`. Because π_t is **persistent / slow-moving**, `Δπ_t ≈ 0` over short windows, so
   **`ΔQ_t ≈ ΔP_t`.** The derivative's *move* and the prediction market's *move* are both noisy
   estimates of the **same change in the real-world probability** driven by news. *This is the
   methodological unlock — the levels problem disappears.* [[bekaert]] [[credit-rp]]

2. **So the question becomes price discovery: whose change leads?** The standard tools are **Hasbrouck
   (1995) Information Share** and **Gonzalo–Granger (1995) Component Share** + Granger causality.
   [[ng]] [[hasbrouck-bg]]

3. **What the 2026 papers find:**
   - **Polymarket leads Kalshi** in price discovery, especially when liquid; **net order imbalance from
     large trades predicts subsequent returns**, and the venue with the bigger directional large-trade
     flow leads. [[ng]]
   - **Crypto markets lead political prediction markets**; a Granger-causal lead-lag *trading* strategy
     exists, with an LLM filter to suppress spurious correlations. [[llm-leadlag]]
   - **Changes in probabilities carry more predictive information than static levels**; calibration
     improves as the event nears; **crypto is the worst-calibrated domain, economics the best** →
     lead-lag is **domain-specific.** [[calib-dyn]]
   - The PM-vs-*derivative* lead-lag is an **explicitly open research question** — the Polymarket
     microstructure paper says so in as many words. [[poly-micro]] That's the uncrowded frontier your
     data sits on.

4. **The gap itself mean-reverts** (BTC: AR(1) ≈ 0.85, half-life ≈ 4.2h) — so **Δgap is predictable**,
   which is the convergence trade; but it's fast and capacity-limited. [[paper-btc]]

5. **Build:** a cross-venue **innovation monitor** — per event, synchronized `ΔPM_t` vs `ΔDeriv_t`,
   with who-leads-whom measured per domain. The actionable signal: *"the leader just moved and the
   laggard hasn't → the laggard is expected to follow within the measured half-life."* Read-only,
   constitution-compliant, and it **needs no Q→P bridge.** You already persist `market_change_log` —
   the panel exists.

---

## 1. Why changes are comparable even when levels are not

The levels obey `Q_t = P_t + π_t`. The premium `π_t` is the part you can't strip without an assumption
(see the companion note: Ross recovery is empirically dead, parametric tilting needs an assumed γ).

In **first differences**:

```
ΔQ_t = ΔP_t + Δπ_t
```

The empirical fact that rescues you: **the risk premium is highly persistent.** Risk aversion and the
variance/credit risk premia move at low frequency (business-cycle / regime timescales), so over a short
window `Δπ_t` is **second-order** relative to the news-driven `ΔP_t`. Daily-frequency risk-appetite
models confirm π moves slowly enough that high-frequency innovations are dominated by fundamentals, not
premium shifts. [[bekaert]] In credit, the risk-neutral/physical *ratio* ("relative credit risk premium")
is itself the slow-moving object whose level — not its high-frequency change — carries the premium.
[[credit-rp]]

> **Consequence:** `ΔQ_t` and `ΔP_t` are both ≈ the same `Δ(true probability)`. The derivative move and
> the PM move should **co-move** when news hits. **Where they don't co-move, something is up** — and
> that "something" is your signal (§3).

**The honest caveat (must ship with this):** `Δπ_t` is *not* exactly zero — the variance risk premium is
time-varying, and during risk-appetite regime shifts (vol spikes, deleveraging) `Δπ_t` can be large and
can masquerade as information. [[bekaert]] So the differencing argument is strongest **at high frequency,
around discrete news**, and **weakest across long windows or stress regimes.** Flag stress regimes; don't
trust the cancellation through a VIX spike.

---

## 2. The right tool: price discovery on the change series

Once you accept `ΔQ ≈ ΔP`, "compare the changes" is precisely the **price-discovery** question from
microstructure econometrics. Two canonical measures, both built on a VECM of the two cointegrated price
series (the two probabilities for the same event are cointegrated — they must converge at resolution):

- **Hasbrouck (1995) Information Share (IS):** of the variance in the common efficient-probability
  innovation, what fraction originates in each venue? High IS = that venue's changes lead. [[hasbrouck-bg]]
- **Gonzalo–Granger (1995) Component Share (CS):** the permanent-transitory decomposition — which venue's
  price *is* the long-run common factor. [[hasbrouck-bg]]
- **Granger causality** on `ΔPM` vs `ΔDeriv`: does one series' lagged change predict the other's? This is
  exactly the lead-lag trading machinery. [[llm-leadlag]]

This is the first-evidence methodology of Ng, Peng, Tao & Zhou (2026) applied across Polymarket / Kalshi
/ PredictIt / Robinhood. [[ng]] Your contribution is to add the **derivative leg** (CME/Deribit/CBOE) —
the leg the PM literature has *not* yet wired in. [[poly-micro]]

---

## 3. What the changes actually tell you — four distinct signals

### Signal A — Lead-lag: the leader's change forecasts the laggard's change ★
The core monetizable object. Evidence:
- **Polymarket leads Kalshi**, particularly when liquidity/activity is high; the lead flips toward
  whichever venue is currently absorbing the **larger directional large-trade order flow**, and that
  **order imbalance predicts subsequent returns.** [[ng]]
- **Crypto leads political PMs**: lagged crypto returns Granger-cause PM moves; a tradeable lead-lag
  strategy follows, with an **LLM semantic filter** added specifically to kill spurious correlations and
  structural breaks (the classic lead-lag failure mode). [[llm-leadlag]]
- For **your** pairs, the prior is **domain-dependent**: on macro/crypto thresholds the **deep derivative
  book (Deribit/CME/CBOE) almost certainly leads the thinner PM line**; on idiosyncratic/political events
  the PM may lead. You must **measure IS/CS per domain**, not assume.
- **The exploitable pattern:** derivative prob jumps on news, PM hasn't repriced yet → PM is predicted to
  move toward the derivative within the measured adjustment half-life.

### Signal B — Co-move vs. idiosyncratic move: information vs. premium/noise
Decompose each event-window into:
- **Both move, same sign, similar size** → genuine information, high confidence. (Use as a *confirmation*
  filter — the cleanest "this is real news" flag you can produce.)
- **Only the derivative moves** → likely a risk-premium / positioning shock *or* the derivative leading;
  disambiguate with the stress-regime flag (§1 caveat).
- **Only the PM moves** → venue-specific: behavioral (favorite-longshot), a liquidity air-pocket, or the
  PM genuinely leading on event-specific info. The favorite-longshot signature (moves concentrated at low
  probabilities, growing with maturity) tells behavioral from informational. [[paper-btc]]

### Signal C — Mean-reversion of the gap: the convergence trade
The gap (a function of the *difference* in levels, but its *dynamics* are a change-series) is **stationary
and mean-reverting**: BTC contracts show AR(1) ≈ 0.85, **half-life ≈ 4.2 hours**, ADF rejects a unit root
(p = 0.004). [[paper-btc]] So `Δgap` is forecastable and a convergence trade exists — but it's a
**4-hour-horizon, capacity-constrained, must-delta-hedge** trade with net alpha right at the
significance boundary (t ≈ 2.10). As a *signal* (not execution) it's fine; as a *trade* it needs real-time
infra you don't have.

### Signal D — Calibration of the *trajectory* beats the snapshot
Directly supports your instinct: **"changes in probabilities over time contain more predictive information
than static probability levels"**; markets that *update* are better-calibrated than static ones;
calibration improves as the event nears (convergence dynamics); and it is **domain-specific** (crypto
worst, economics best). [[calib-dyn]] → score events by *how their probability has been moving*, and trust
the change-signal more in well-calibrated domains.

---

## 4. Why the changes framing is the better product

1. **It needs no Q→P bridge.** The premium cancels — you sidestep the entire unsolved problem in the
   companion note. That alone makes it more defensible than a levels-based "true probability" claim.
2. **Slower decay than static arb.** Pure cross-venue *level* arb lasts **~2.7s** and is **73% bot-captured**
   with **$100 undeployable 77% of the time.** [[arb-2026]] A *changes/lead-lag* signal operates on the
   adjustment horizon (minutes-to-hours), where the constraint is information, not raw latency — a space a
   read-only analytics product can actually occupy.
3. **It's the open frontier.** The PM-vs-derivative lead-lag is **explicitly unanswered** in the
   microstructure literature. [[poly-micro]] You have the one ingredient that's scarce: a persisted,
   cross-venue, multi-asset **synchronized change panel** (`market_change_log`).
4. **Constitution-compliant.** Read-only signal/analytics; no execution, no custody (§2).

---

## 5. Concrete research/build plan (no Q→P bridge required)

All of this is analysis-layer + the existing history store — pure functions over the change-log.

1. **Synchronize the change series.** Per tracked event, build aligned `ΔPM_t`, `ΔDeriv_t` at a fixed bar
   (start hourly — matches the BTC paper's resolution and your cron cadence). [[paper-btc]]
2. **Measure who leads, per domain.** Fit the VECM; compute **Hasbrouck IS + Gonzalo–Granger CS** and
   run **Granger causality** `ΔDeriv → ΔPM` and `ΔPM → ΔDeriv`. Report per domain (Fed, BTC, ETH, NDX).
   [[ng]] [[hasbrouck-bg]]
3. **Estimate the adjustment half-life of the gap** per domain (AR(1) on the gap). [[paper-btc]]
4. **Emit the innovation signal:** when the *leader* moves > threshold and the *laggard* hasn't, flag
   "laggard expected to move toward leader by ~X% within ~Y hours," tagged with the co-move/idiosyncratic
   classification (Signal B) and the domain calibration weight (Signal D).
5. **Backtest** the signal against the realized subsequent laggard move (information coefficient, hit
   rate) — *as a forecast*, not a trade, so the latency/capacity frictions in §4.2 don't bind.
6. **Stress-regime guard:** suppress / down-weight the `ΔQ≈ΔP` assumption when a vol/risk-appetite spike
   makes `Δπ_t` large. [[bekaert]]

Each step follows the constitution's Definition of Done (§15): pure-math unit tests with hand-checked
values, provenance, graceful degradation, structured logs. Branch + PR, never main.

---

## 6. Honest caveats
- **`Δπ ≠ 0` in stress.** The cancellation is an approximation; it fails through vol regime shifts. Guard it.
- **Lead-lag is unstable.** Spurious lead-lag and structural breaks are the classic failure — the
  literature adds an explicit filter for exactly this. [[llm-leadlag]] Re-estimate IS/CS rolling.
- **Cointegration requires a real common event.** Definition/settlement must match exactly or the two
  series aren't pricing the same thing and the VECM is meaningless (your terminal-only, exact-date
  matching already enforces this).
- **Thin-venue moves can be noise, not leadership.** A move on a 1,300–1,800 bps-spread low-prob PM line
  [[poly-micro]] may be microstructure, not information — keep the thin/stale flags.
- **No execution.** This is a forecasting/analytics product (constitution §2).

---

## Step 2 — EMPIRICAL RESULTS (run 2026-06-25, `research/leadlag.py`)

Step 2 of §5 ("measure who leads, per domain") — **run and verified on live data.**

**Data reality first (the honest blocker).** The persisted Neon store has only **3 daily snapshots**
(2026-06-21..23), **one observation/series/day**, and the only PM+derivative-paired topic is `fed rate
decision`. A ~4.2h-half-life lead-lag is **unmeasurable** on daily data with n≈3 — so step 2 cannot run
on the store. Instead we **backfilled trailing hourly history** from free endpoints (§13-probed live):
Polymarket CLOB `/prices-history?interval=max&fidelity=60` (PM leg) + Deribit `get_tradingview_chart_data`
res=60 for the matched option and `BTC-PERPETUAL`/`ETH-PERPETUAL` spot (derivative leg, BS-inverted to
Φ(d2)). Matchable exact-expiry pairs are the **daily** crypto markets (Fed/Nasdaq have no free intraday
derivative feed), so we **pool** all matched daily pairs into one differenced panel — the BTC paper's design.

**Headline: the derivative LEADS the prediction market by ~1 hour. ΔDeriv Granger-causes ΔPM; the reverse is weak/absent.**

| Metric (pooled, first differences) | **BTC** | **ETH** |
|---|---|---|
| Matched daily pairs / pooled hours | 30 / **1,952** | 37 / **2,294** |
| Cross-corr peak lag (h) | **+1** | **+1** |
| Peak r `corr(ΔPM_t, ΔDeriv_{t−1})` | **0.684** | **0.402** |
| Contemporaneous (lag 0) r | 0.052 | 0.086 |
| **Granger ΔDeriv → ΔPM** (p) | **0.000** | **0.000** |
| Granger ΔPM → ΔDeriv (p) | 0.076 (weak) | 0.0018 (some feedback) |

**Artifact check — is the +1h lead just a timestamp-labeling offset? No.** Using Deribit **spot** as the
common driver (spot and the option share Deribit's clock):

| vs Deribit spot | `Δoption` reacts | `ΔPM` reacts |
|---|---|---|
| BTC | **lag 0** (r=0.471) | **lag +1h** (r=0.497; lag-0 only 0.086) |
| ETH | **lag 0** (r=0.452) | **lag +1h** (r=0.273; lag-0 only 0.058) |

The option tracks BTC/ETH in the *same* hour; the PM tracks the *same* spot move **one hour later**. Since
spot and option share a clock, the PM lagging spot (while the option does not) proves the lag is **real
price-discovery latency**, not a PM-vs-option convention mismatch.

**Per-pair (BTC, 16 pairs ≥48h) — the lead is concentrated where there is information:**
- **Near-the-money strikes**: `$64k`→r=0.93, `$66k`→0.85, `$62k`→0.69, `$60k`→0.61 at lag +1h, each with
  **Granger deriv→pm p≈0.000** and **Gonzalo–Granger component share deriv-dominant (0.6–0.98)** — the
  derivative carries the permanent (common-factor) price. Gap **half-life is sub-hour to ~1.5h**.
- **Deep-OTM strikes** (far above spot, prob≈0): degrade to noise / occasional sign-flips. Expected — the
  favorite–longshot behavioral term dominates and single-option BS inversion is unstable where vega→0. The
  signal is cleanest near the money; **don't trust it in the tails** (carry that as a confidence flag).

**What this means for the thesis.** §3 Signal A is **confirmed on crypto**: the deep derivatives/spot market
leads Polymarket by ~1 hour, and `ΔDeriv` forecasts `ΔPM`. The actionable form — *"Deribit-implied P(>K)
just moved and Polymarket hasn't; expect PM to converge within ~1–2h (gap half-life ≤~1.5h near the
money)"* — is empirically supported. Part of the lag is PM **illiquidity** (prices-history is last-trade-
driven), which is *why* it's exploitable but also means execution must respect PM depth (§4.2).

**Caveats (binding):** (1) trailing live window of short-dated June-2026 dailies — needs out-of-sample
re-test across regimes before productionizing; (2) crypto only — Fed/Nasdaq have no free intraday
derivative feed, so the lead is **unproven** there; (3) capturing a 1-hour lead live requires **intraday
ingestion** — the daily cron cannot; (4) near-money only. Reproduce: `pip install -r research/requirements.txt`
then `python -m research.leadlag --coin BTC` (research-only deps; kept out of `pyproject`).

## Sources
- [[ng]] Ng, Peng, Tao & Zhou, *Price Discovery and Trading in Modern Prediction Markets*, SSRN 5331995
  (Apr 2026) — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5331995
- [[llm-leadlag]] *LLM as a Risk Manager: LLM Semantic Filtering for Lead-Lag Trading in Prediction
  Markets* (2026) — https://arxiv.org/pdf/2602.07048
- [[calib-dyn]] Le, *Decomposing Crowd Wisdom: Domain-Specific Calibration Dynamics in Prediction
  Markets* (2026) — https://arxiv.org/pdf/2602.19520
- [[poly-micro]] *The Anatomy of a Decentralized Prediction Market: Microstructure Evidence from the
  Polymarket Order Book* (2026) — https://arxiv.org/html/2604.24366v1
- [[paper-btc]] *Do Prediction Markets Match Option Prices? Bitcoin Threshold Evidence from Binance and
  Polymarket* (2026) — https://arxiv.org/html/2606.19517
- [[arb-2026]] *Beyond Simple Arbitrage: 4 Polymarket Strategies Bots Actually Profit From in 2026* —
  https://medium.com/illumination/beyond-simple-arbitrage-4-polymarket-strategies-bots-actually-profit-from-in-2026-ddacc92c5b4f
- [[bekaert]] Bekaert, Engstrom & Xu, *The Time Variation in Risk Appetite and Uncertainty*, Management
  Science (2022) — https://pubsonline.informs.org/doi/10.1287/mnsc.2021.4068
- [[credit-rp]] *The relationship between risk-neutral and actual default probabilities: the credit risk
  premium*, Applied Economics — https://www.tandfonline.com/doi/full/10.1080/00036846.2016.1150953
- [[hasbrouck-bg]] Price-discovery background — Hasbrouck (1995) Information Share; Gonzalo & Granger
  (1995) Component Share; *Price Discovery in Cryptocurrency Markets* — https://arxiv.org/pdf/2506.08718
- [[political-pd]] *Political Shocks and Price Discovery in Prediction Markets: Evidence from the 2024
  U.S. Presidential Election* (2026) — https://arxiv.org/html/2603.03152
