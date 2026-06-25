# Can Company-Specific Bets Be Monetized? — Research Dive

**Date:** 2026-06-25
**Status:** research complete (empirical liquidity scan + literature). No productionization.
**Companion to:** `RESEARCH.md`, `CHANGES_BASED_SIGNAL_RESEARCH.md` (the crypto lead-lag result this builds on).
Relates to the existing **company-bet scan** (discovery/listing, PR #11).

---

## 0. Verdict up front

**Mostly NOT feasible — with two narrow, genuinely monetizable exceptions.**

Company bets are a **thin long-tail** of the prediction-market business (the market is ~$10–13B/month but
**90–91% is sports/politics/crypto**). Our live scan confirms the tail is mostly **dead**: ~89% of Kalshi
company markets and ~92% of Polymarket single-name *price* markets have negligible volume. On top of thinness
sits an **insider-trading / legal overlay** unique to company events. So the generic "scan company bets → find
relative-value gaps" idea does **not** clear the bar.

But two niches do clear it, because they are **both liquid and have a clean derivative to compare against**:
1. **M&A / deal-completion markets** — liquid (median ~$87k volume, only ~4% dead) and map directly onto the
   **merger-arbitrage spread** (target stock vs. offer → market-implied P(close)). This is the best candidate.
2. **A small liquid subset of stock-price / market-cap markets** — incl. the high-volume mega-cap "races"
   ("Largest Company", "Best AI model") — comparable to **equity options / live market caps**, where the
   ~1-hour **lead-lag** we proved for crypto should transfer (deep equity market leads the thin PM).

Everything else (CEO words, FDA approvals, layoffs, product launches, lawsuits) is **PM-native**: no derivative
prices it, so there is nothing to run relative value against — only a forecasting/information edge, which for
company events is exactly where the **insider-trading risk** concentrates.

---

## 1. What we measured (live scan, 2026-06-25 — `research/company_liquidity.py`)

Reused the production sources: Kalshi "Companies" category (every series → nested markets) and Polymarket
Gamma searches for 9 mega-caps (Nvidia, Tesla, Apple, Microsoft, Amazon, Google, Meta, OpenAI, SpaceX).
Classified each market and measured its liquidity. (Kalshi volume = contracts, 24h-preferred; Polymarket
volume = USD total; PM counts include per-outcome rows of multi-outcome events, so distinct *events* are fewer.)

### Kalshi — "Companies" category
- **175 series, but only 36 currently active markets** (most series are dormant/event-driven).
- **All 36 are KPI/corporate-event** — **zero** price/market-cap, **zero** M&A.
- Liquidity tiny: **median 24h volume = 66 contracts**, max 36,641; OI median 176; **89% effectively dead** (<1,000).
- Most liquid: "Apple MacBook with cellular before…" (36,641), "Which companies will conduct layoffs this year?",
  "Rippling vs Deel lawsuit".
- **Read:** Kalshi company bets are an illiquid novelty/KPI corner. Not monetizable.

### Polymarket — 9 mega-cap searches (937 market-rows)
| Bucket | n | Median vol | Max vol | Median OI | % dead (<$10k) |
|---|---|---|---|---|---|
| **price / market-cap** | 465 | $191 | $4.08M | $3,411 | **92%** |
| **M&A / deal** | 26 | **$87,017** | $598,596 | $59,455 | **4%** |
| KPI / event | 446 | $8,284 | $23.76M | $12,522 | 54% |

- Most-liquid overall: **"Largest Company end of June" ($23.8M)**, **"Best AI model end of June" ($21.6M)** —
  multi-company mega-cap *races*.
- **Read:** the single-name *price* tail is overwhelmingly dead (~38 of 465 alive), but **M&A markets are
  almost all live**, and a handful of mega-cap narrative/race markets are very deep.

---

## 2. Segment-by-segment feasibility

### A. M&A / deal completion — ★ most feasible
- **Liquid** (median ~$87k, ~96% alive) and has a **clean, low-risk-premium derivative**: the merger-arb
  spread implies `P(close) ≈ (price_now − downside) / (offer − downside)`. Compare to the PM's P(deal closes).
- Free data: target stock price (Yahoo, same client we already use for ZQ/ESR), offer terms are public.
- **Monetization:** (i) as a *signal* — flag when the PM's deal probability diverges from the arb-implied one;
  (ii) lead-lag — equity/options desks price deals fast; the PM likely lags (same pattern as crypto).
- Caveat: deal outcomes are idiosyncratic (regulatory/antitrust); the "premium" is small but jump risk is real.

### B. Stock-price / market-cap thresholds — feasible only for the liquid subset
- The **exact machinery we already built** transfers: equity options → Breeden-Litzenberger / delta digital
  (like Nasdaq via CBOE), or — more robustly — **lead-lag with the underlying stock**, where our crypto result
  (deep market leads thin PM by ~1h, near-the-money) should carry over to single names.
- **But 92% are dead.** The tradeable universe is the ~38 liquid names + the mega-cap **race** markets
  ("Largest Company", "Best AI model"), which are *deterministic functions of observable market caps* near
  resolution → strong convergence/lead-lag candidates (the PM should track the live cap ranking).
- Data friction: single-name options are **less freely available** than crypto (Deribit) or indices; CBOE's
  free delayed-quotes cdn may cover single equities (we use it for `_NDX`) — **needs §13 verification per name**.

### C. KPI / corporate events (CEO, FDA, layoffs, product, lawsuits, earnings-direction) — not relative-value monetizable
- **No derivative prices these** → nothing to compare against; only a forecasting/information edge.
- This is precisely where the **insider-trading overlay** bites: employees trading on non-public info
  (earnings scripts, deal knowledge, FDA decisions) is an active legal concern (MoFo, Skadden, Hunton 2026),
  and outcomes controlled by one person (a CEO's words) are **manipulable**. Prices may be *informative* (a
  small number of skilled/informed traders drive PMs — Yale) but acting on that edge carries real legal/ToS
  risk and is outside the read-only mandate.

---

## 3. Why "scan all company bets for gaps" fails as a general strategy
- **Thin long tail:** ~9 of 10 company markets are effectively dead — no depth to trade, and a single quote is
  microstructure noise, not a signal (carry the existing thin/stale flags).
- **No common derivative for the majority:** most company bets are PM-native events; relative value is undefined.
- **Crowding/latency where it IS liquid:** the deep markets (mega-cap races, big M&A) are exactly where bots and
  desks already operate — the 2.7s arb-window reality applies.
- **Legal overlay:** company-event contracts carry insider-trading/manipulation risk that crypto/index/rate
  markets do not.

So the right framing is **not** "company bets" as a category, but **"the two liquid sub-niches with a clean
derivative"**: M&A completion, and the liquid mega-cap price/race markets.

---

## 4. What would need to be done (if pursued)

**Validate the two niches (cheap, do first)**
- [ ] **M&A:** take the ~25 live deal markets, pull target stock + offer terms, compute arb-implied P(close),
      and measure the PM-vs-arb gap and its lead-lag (does the stock lead the PM, as crypto did?).
- [ ] **Mega-cap races:** for "Largest Company" / "Best AI model", compute the implied ranking from live market
      caps and test whether the PM converges to / lags it (a near-deterministic convergence trade).
- [ ] **Single-name price thresholds:** verify free options data per name (CBOE cdn `…/options/AAPL.json`?),
      then run the crypto lead-lag harness on the ~38 liquid single-name markets.

**Blockers to resolve before any productionization**
- [ ] Single-name options/market-cap data source (free tier, §13-verified) — the main data gap.
- [ ] Liquidity gate: only ingest company markets above a volume/OI floor (most fail it).
- [ ] Legal/ToS review of touching company-event contracts at all (insider-trading exposure); keep strictly
      read-only / signal-only, and consider excluding single-person-controlled outcomes.

**Reuse:** the M&A and price-threshold work is the **same relative-value + lead-lag spine** already built
(`app/markets/_shared`, `research/leadlag.py`) — only the derivative source differs (equity/merger-arb vs.
Deribit/CBOE). No new architecture.

---

## 5. Sources
- Pew Research, *Trading volume on prediction markets has soared* (2026) — sports/politics/crypto = 90–91% of
  volume. https://www.pewresearch.org/short-reads/2026/05/27/trading-volume-on-prediction-markets-has-soared-in-recent-months/
- Bloomberg, *How Polymarket and Kalshi Are Gamifying Truth* (2026) — https://www.bloomberg.com/features/2026-prediction-markets-polymarket-kalshi/
- Morrison Foerster, *Prediction Markets and the Law of Insider Trading* (2026) — https://www.mofo.com/resources/insights/260303-prediction-markets-and-the-law-of-insider
- Skadden, *How to Stay Ahead of the Risk That Your Insiders Could Trade on Prediction Markets* (2026) — https://www.skadden.com/insights/publications/2026/06/the-informed-board/how-to-stay-ahead-of-the-risk
- Hunton, *Public Company Considerations for Prediction Markets* — https://www.hunton.com/insights/legal/public-company-considerations-for-prediction-markets
- Robin Hanson, *Insider Trading and Prediction Markets* — https://mason.gmu.edu/~rhanson/insiderbet.pdf
- Yale Insights, *Wisdom of the Few? Prediction Markets Are Driven by a Small Number of Skilled Traders* — https://insights.som.yale.edu/insights/wisdom-of-the-few-prediction-markets-are-driven-by-small-number-of-skilled-traders
- NY Fed Staff Report 761, *Merger Options and Risk Arbitrage* — https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr761.pdf
- *Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets* (2025) — https://arxiv.org/abs/2508.03474
- QuantPedia, *Systematic Edges in Prediction Markets* — https://quantpedia.com/systematic-edges-in-prediction-markets/
- Live scan: `research/company_liquidity.py` (Kalshi Companies category + Polymarket mega-cap searches, 2026-06-25).
