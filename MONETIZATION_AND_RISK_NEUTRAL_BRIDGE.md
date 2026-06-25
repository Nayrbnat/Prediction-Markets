# Monetizing the PM↔Derivative Signal — and the Risk-Neutral vs. Real-World Problem

> **Deliverable / status:** Deep-research note (no code changes). Grounds the central question
> behind this entire system: the derivative side produces a **risk-neutral (Q)** probability;
> the prediction-market side produces something **closer to the real-world (P)** probability —
> *but not equal to it.* Whether the gap between them is harvestable money or just a premium you
> can't capture is the whole ballgame. Sources are listed inline and at the bottom.
>
> Written 2026-06-24. Every empirical figure below is from a cited source, not memory.

---

## 0. TL;DR (read this if nothing else)

1. **The bridge is not bridged.** You cannot turn a risk-neutral probability into a real-world
   probability without injecting an extra ingredient — either an assumed pricing kernel / risk-aversion
   parameter, or a historical calibration curve. The one theory that claimed to do it from option
   prices *alone* (Ross's Recovery Theorem, 2015) is **elegant but empirically rejected.**

2. **Your gap is a sum, not a signal.** For any event,
   `PM_price − derivative_RN_prob = risk_premium + PM_behavioral_bias + liquidity/mispricing + definition_mismatch`.
   Today the system surfaces the *total*. The total is not tradeable. Only the **residual after
   stripping the first two terms** is.

3. **The closest published study to your exact strategy** (Polymarket BTC thresholds vs. Binance
   option-implied probs, June 2026) finds a mean gap of **~6 percentage points**, **largest at low
   probabilities**, **growing with maturity** — i.e. the *favorite–longshot* signature, not a risk
   premium — that **mean-reverts with a ~4.2-hour half-life** and is **barely profitable after costs**
   (net alpha t = 2.10, p ≈ 0.053). [[paper-btc]]

4. **Direct arbitrage is mostly gone.** 2026 Polymarket arb windows last **~2.7 seconds**, **73%
   captured by sub-100ms bots**, and order books are so thin a **$100 budget can't be fully deployed
   77% of the time.** [[arb-2026]] Your daily-cadence, read-only system is structurally on the wrong
   side of that race.

5. **Therefore the realistic monetization is not "trade the gap" — it's "sell the de-biased number."**
   The defensible, buildable product is the **calibration/recovery layer you don't have yet**: convert
   *both* sides to estimated real-world probabilities with confidence intervals, and decompose the gap.
   That is precisely the thing the literature says is unsolved, and you already persist the history
   needed to build it.

---

## 1. The core finance: why Q ≠ P

Every probability in this system comes from a price. Prices embed **risk preferences**, so the
probability implied by a price is the **risk-neutral measure Q**, not the **physical/real-world
measure P**. They are linked by the **pricing kernel** (stochastic discount factor, SDF):

```
dQ/dP = m_T / E[m_T]        (the Radon–Nikodym derivative = the normalized pricing kernel)

P(event) = Q(event) · (1 / pricing-kernel tilt over that event's payoff region)
```

- For an event that pays off in **bad states of the world** (recessions, crashes, big rate cuts),
  investors *overpay* for the insurance, so **Q > P**. The raw price overstates the true probability.
- For an event that pays off in **good states**, **Q < P**.
- The size of the wedge is the **risk premium for that event.** It is small for front-end interest
  rates, **medium for crypto, large for equity-index downside and volatility.** This is exactly the
  per-item "risk-prem cleanliness" column in `RELATIVE_VALUE_OPPORTUNITIES.md` — that column *is* the
  size of the unbridged gap.

> **This is why the constitution (§2, and the caveats in the opportunities note) insists the gap is
> "decision-support, never arbitrage."** A persistent gap is the *expected* state of the world, not a
> free lunch. The research confirms this is the right framing.

**Empirical anchor — the variance risk premium.** The cleanest proof that Q ≠ P in practice:
historically, options implied a **~13% chance of a 10% drawdown** when the realized frequency was
**~4%.** [[vrp]] Option-implied IV systematically exceeds realized vol because demand for insurance
exceeds its actuarial cost. If you read that 13% as a real-world probability, you are wrong by ~3×.

---

## 2. Has anyone bridged Q → P? Three approaches, none clean

### 2.1 Parametric pricing-kernel tilting (the practical workhorse)
Assume a functional form for the SDF (typically CRRA / power utility) and **exponentially tilt** the
risk-neutral density by a single parameter — the coefficient of relative risk aversion (γ):

```
f_P(x) ∝ f_Q(x) · e^{ -γ · (something monotone in the state) }
```

You can even back γ *out* of the market: the option-implied relative risk aversion is recoverable from
the **difference between the risk-neutral and physical variance** (i.e. the variance risk premium gives
you γ, then γ gives you the whole tilt). [[calib]] [[rnd-bok]]

- **Pro:** simple, one parameter, directly usable on a Breeden–Litzenberger density (which you already
  compute for crypto). Produces a genuine real-world density and a term structure of the equity risk
  premium. [[erp]]
- **Con:** you *assumed* the kernel shape. Different γ → different "real-world" probability. It's a
  recalibration, not a discovery. Garbage-in if γ is wrong.

### 2.2 Ross Recovery Theorem (2015) — the bold claim, and why it fails
Ross (2015) claimed you can recover **P from option prices alone**, no preference assumption, by
exploiting Markov structure to factor state prices into (pricing kernel) × (real-world probabilities).
[[ross]] If true, this would *literally bridge the gap* with no extra input — it would be the holy grail
for this exact product.

**It does not work empirically:**
- **Borovička, Hansen & Scheinkman (2016):** Ross's recovery implicitly sets the **permanent
  (martingale) component of the SDF to 1.** That assumption is the catch — it's equivalent to assuming
  away long-run risk pricing. [[borovicka]]
- **Jackwerth & Menner (2020), *J. Financial Economics*, "Does the Ross Recovery Theorem work
  empirically?":** No. The recovered "physical" distributions **fail to predict realized returns** and
  the cross-moment restrictions the theorem implies are **rejected in the data.** [[jackwerth]]
- **Bakshi et al.:** the permanent component is **time-varying**, directly violating Ross's constant-=1
  assumption. [[jackwerth]]

> **Bottom line: the one method that would have bridged the gap for free is empirically dead.** Anyone
> who tells you they "converted option prices into true probabilities" with no other input is, knowingly
> or not, smuggling in the Ross assumption that the literature has already falsified.

### 2.3 Statistical / historical recalibration (atheoretical, robust)
Forget the kernel. Just learn the **empirical map from implied probability → realized frequency**:
bucket historical predictions, measure how often each bucket actually happened, fit the **calibration
curve**, and apply it going forward. This is exactly how Kalshi's own explainer frames "reading"
prices, and how the calibration-dynamics literature treats both venues. [[kalshi-read]] [[calib-dyn]]

- **Pro:** no utility assumption; directly corrects *both* the risk premium *and* behavioral bias in
  one empirical step; gives honest confidence intervals.
- **Con:** needs history and stationarity; event types differ (a Fed-cut calibration ≠ a BTC-threshold
  calibration → must be **domain-specific**, which the literature confirms). [[calib-dyn]]

**This third path is the one you are uniquely positioned to build, because you already persist the
change-log of every observation.**

---

## 3. The twist that makes *your* setup special: the PM side is already near-P

Here is the insight that separates your system from a generic options-desk view. You are not comparing
a risk-neutral number to *nothing* — you compare it to a **prediction-market** number, and prediction
markets are widely argued to aggregate beliefs into something **close to the real-world probability**
(small risk premium because capital is only briefly locked, no big systematic hedging demand).

So your gap is structured:

```
PM_price            ≈  P(event)  +  PM_bias        (favorite–longshot, fees, capital cost)
derivative_RN_prob  =  Q(event)                    (risk-neutral)

GAP = PM_price − derivative_RN_prob
    = [P − Q]            ← the risk premium / pricing-kernel wedge  (NOT yours to harvest)
    + PM_bias            ← behavioral distortion in the PM          (real but small & noisy)
    + liquidity/mispricing + definition_mismatch   ← the only genuinely tradeable residual
```

**The published evidence on exactly this comparison** (Polymarket BTC thresholds vs. Binance
option-implied, June 2026) is remarkably clean: [[paper-btc]]
- Mean gap **5.6–6.3 pp** (t up to 8.2, p ≈ 10⁻¹⁵ — the gap is *real*, not noise).
- Gap is **largest at low option-implied probabilities** (coef −0.398, p<0.001) and **grows with time
  to expiry** (p<0.001) → this is the **favorite–longshot / speculative-demand signature**, i.e. it's
  dominated by `PM_bias`, *not* the risk premium. The authors explicitly call it **"speculative
  mispricing rather than risk adjustment"** and caution it is *"a deviation from the risk-neutral
  benchmark rather than a statement about physical probabilities."*
- It **mean-reverts**, AR(1) ≈ 0.85, **half-life ≈ 4.2 hours.**
- A delta-hedged convergence trade is **net-positive but marginal:** 16 trades, 69% win rate, net
  alpha 0.067 with **t = 2.10, p ≈ 0.053** (95% CI grazes zero).

Read that last line carefully: the *best published version of your strategy* is **right on the edge of
statistical significance after costs, and only by delta-hedging the option leg in real time.**

---

## 4. Can you actually monetize it? Four honest paths

### Path A — Trade the convergence yourself (relative value). **Low feasibility as built.**
The gap mean-reverts (4.2h), so in principle you fade the PM toward the derivative. But:
- **Latency:** 2026 Polymarket arb windows ≈ **2.7s**, **73% captured by <100ms bots** on dedicated
  Polygon RPC nodes. [[arb-2026]] A daily cron cannot compete.
- **Depth:** **$100 couldn't be fully deployed in 77% of episodes**; risk-free extraction is "confined
  to retail scale." [[arb-2026]] [[arb-nba]]
- **Costs:** Polymarket taker fees peak at **~1.8% at 50/50** (crypto markets up to ~1.8%). [[fees]]
- **You must hedge the option leg** to isolate the gap → needs an options/futures account, margin, and
  real-time execution — a different system from the read-only ingester you have.
- **Verdict:** a real edge exists but it is small, fast, capacity-constrained, and requires a
  ground-up real-time + execution rebuild plus capital. Not what you have; not cheap to get.

### Path B — Sell the divergence feed as an alpha-data product. **Medium feasibility.**
You already emit a clean, provenance-tagged, cross-venue, multi-asset divergence series — structurally
the same shape as commercial alpha-signal data businesses. [[spglobal]] [[bbg]]
- **Pro:** plays to your strengths (coverage, provenance, honest flags), stays **read-only** (no §2
  conflict).
- **Con:** **alpha decay / crowding** — as more consumers act on the same signal the gap closes; you'd
  compete on latency and breadth, where you're weak. Raw divergence is also the *least* differentiated
  thing to sell because anyone with an options feed can compute it.

### Path C — Build the de-biasing / recovery layer and sell *that*. **High feasibility, most defensible. ★**
This is the recommendation. Instead of shipping the raw gap, ship the **estimated real-world
probability with the gap decomposed.** Concretely, on top of the existing history:
1. **Calibrate each side** against realized outcomes per domain (Fed, BTC, ETH, NDX) → empirical
   calibration curves (§2.3). Turns both the RN derivative prob and the raw PM prob into **realized-
   frequency-adjusted P estimates with confidence intervals.**
2. **Decompose the gap** into `{risk premium (P−Q), PM behavioral bias, residual}` using the calibration
   curves + a tilt estimate (§2.1) for the risk-premium piece.
3. **Surface only the residual** as the actionable signal, with the premium and bias shown as *context,
   not edge.*
- **Why it's defensible:** §2 shows this is the genuinely unsolved part. Ross is dead, parametric tilting
  needs an assumption you can pin with *your own* historical calibration, and **nobody has cleanly
  productized a per-event, cross-venue, de-biased real-world probability with a gap decomposition.**
- **Why you can build it:** you already persist `market_change_log` — the historical observation series
  is the only scarce input. This is analysis-layer work (pure functions + a calibration store), squarely
  inside the constitution.

### Path D — Market-making on the PM venue. **Out of scope (and against §2).**
Cited for completeness: providing liquidity earns ~0.2% of volume, 78–85% win rate. [[arb-2026]] This is
trade execution / custody — **explicitly forbidden by the constitution (§2).** Not a path here.

---

## 5. On "the visualization"

The system today has **no frontend** — it's a read-only API plus a daily/5-day email digest. If the
question is *how to visualize this strategy so the edge is legible*, the three views that actually matter
map 1:1 onto §3–4:

1. **Divergence-over-time with mean-reversion overlay** — PM prob vs. derivative RN prob per tracked
   event, shaded gap, and the convergence (the 4.2h-half-life story made visible).
2. **Calibration curve** — predicted probability (x) vs. realized frequency (y), one line per venue per
   domain, with the 45° line. *This is the single most credibility-building chart for a data product* —
   it's the empirical proof of whether your numbers are real-world-true.
3. **Gap-decomposition waterfall** — for a given event: `PM → (−risk premium) → (−PM bias) → residual`,
   so a viewer sees instantly how much of the headline gap is actually actionable.

I did **not** build any of these — I want to confirm what you mean by "the visualization here" before
spending effort: a web dashboard, charts attached to the daily email, or something else.

---

## 6. Honest caveats (carry these with any monetization claim)
- **Risk-neutral ≠ real-world** is the whole point of this note; never present a Q number as a P number.
- **Definition/settlement mismatch** silently corrupts the gap — PM resolution source/timestamp must
  match the derivative's underlying exactly (the system already enforces terminal-only, exact-date).
- **Liquidity asymmetry** — a thin PM line vs. a deep options book means the *PM* may be wrong, not the
  derivative; keep the existing thin/stale flags.
- **Alpha decay** — any sold signal crowds and closes.
- **No execution** — Paths A and D involve trading; the constitution restricts this service to the
  analysis side (§2). Path C keeps you compliant *and* differentiated.

---

## Sources
- [[paper-btc]] *Do Prediction Markets Match Option Prices? Bitcoin Threshold Evidence from Binance and
  Polymarket* (2026) — https://arxiv.org/html/2606.19517
- [[arb-2026]] *Beyond Simple Arbitrage: 4 Polymarket Strategies Bots Actually Profit From in 2026* —
  https://medium.com/illumination/beyond-simple-arbitrage-4-polymarket-strategies-bots-actually-profit-from-in-2026-ddacc92c5b4f
- [[arb-nba]] *Arbitrage Analysis in Polymarket NBA Markets* (2026) — https://arxiv.org/html/2605.00864v1
- [[fees]] *Polymarket Fees* — https://docs.polymarket.com/trading/fees
- [[vrp]] *The Option Trader's Guide to the Variance Risk Premium* — https://www.predictingalpha.com/the-option-traders-guide-to-the-variance-risk-premium/
- [[ross]] Ross, *The Recovery Theorem*, J. Finance 2015 — https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12092
- [[borovicka]] Borovička, Hansen & Scheinkman (2016), via discussion in the recovery literature —
  https://www.sciencedirect.com/science/article/pii/S0304405X20300763
- [[jackwerth]] Jackwerth & Menner, *Does the Ross Recovery Theorem work empirically?*, JFE 2020 —
  https://www.sciencedirect.com/science/article/pii/S0304405X20300763
- [[calib]] *Estimation of option-implied risk-neutral into real-world density using a calibration
  function* — https://www.researchgate.net/publication/316550510
- [[rnd-bok]] Bank of England WP, *Implied risk-neutral PDFs from option prices* —
  https://www.bankofengland.co.uk/working-paper/1997/implied-risk-neutral-probability-density-functions-from-option-prices
- [[erp]] *Option-based Equity Risk Premiums* — https://arxiv.org/pdf/1910.14522
- [[kalshi-read]] *How to translate Kalshi market prices into real-world odds* —
  https://news.kalshi.com/p/how-to-read-probabilities
- [[calib-dyn]] *Decomposing Crowd Wisdom: Domain-Specific Calibration Dynamics in Prediction Markets* —
  https://arxiv.org/pdf/2602.19520
- [[spglobal]] S&P Global, *Quantitative Equity Data: Alpha Signals* —
  https://www.spglobal.com/market-intelligence/en/solutions/products/quantitative-equity-data-alpha-signals
- [[bbg]] Bloomberg, *Maximizing alpha: data, technology & AI in quant investing* —
  https://www.bloomberg.com/professional/insights/data/maximizing-alpha-harnessing-data-technology-ai-in-quant-investing/
