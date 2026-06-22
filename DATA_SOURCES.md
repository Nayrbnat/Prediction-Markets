# DATA_SOURCES.md

The verified reference for **how Polymarket and Kalshi actually work** and **exactly how to query them** for: (1) what questions/markets exist, (2) the current market-implied probability of each outcome, and (3) on-chain trade data (Polymarket only). This is the binding source-of-truth the `sources/` layer is built and verified against (`PLANNING.md` Phase 2). Everything here was checked against official docs and working repos; **endpoints and especially contract addresses change — re-verify at build time** (see §7, which documents a contract migration that already broke one common approach).

> **Read this first, the rest follows from it:** for your goal — *read the current predictions and their probabilities* — **no authentication and no wallet are required on either venue.** Discovery and prices come from public REST endpoints. On-chain reading (smart-money) is optional enrichment via a Polygon RPC. Auth/wallets are only ever needed for *trading*, which this project never does.

---

## 0. The two venues are fundamentally different — internalise this

| | **Polymarket** | **Kalshi** |
|---|---|---|
| What it is | Decentralised, non-custodial prediction market | Centralised, CFTC-regulated exchange |
| Where it runs | **Polygon PoS (chain ID 137)** — *not* Ethereum mainnet | **No blockchain.** Ordinary web backend |
| How a "probability" is expressed | Outcome-token price $0.00–$1.00 (ERC-1155) | Contract price in **cents 0–100** |
| Discovery + prices | Gamma REST API (public) + CLOB REST (public) | Trade API REST (public) |
| On-chain trade ledger | **Yes** — `OrderFilled` events on Polygon | **None** — no wallets, no on-chain trades to read |
| Smart-money / wallet analysis | **Possible** (cluster wallets from chain) | **Impossible** (no per-trader identity) |
| Auth for *reading* prices | None | None |
| Auth for *trading* | Wallet (EIP-712) + derived API creds | API key id + RSA private key (signed requests) |

**Why "scrape Ethereum for Polymarket" is not a thing (chain mechanics).** Polygon PoS has two layers: **Bor** (the EVM block producer, ~2s blocks) and **Heimdall** (the validator layer that periodically writes **checkpoints** — a Merkle root of Bor blocks — to Ethereum mainnet roughly every ~30 minutes). Every Polymarket trade event (`OrderFilled`, `PositionsMerged`, `ConditionResolution`) is emitted on **Bor / Polygon**. Ethereum only ever sees the aggregated checkpoint, never the individual trades. So you read Polymarket history from a **Polygon** RPC; there is no per-trade Polymarket data on Ethereum to query. Kalshi, being centralised, has no chain at all.

---

## 1. Polymarket — data model

Two nested objects, the same as in `CLAUDE.md`:

- **Event** = the top-level question (e.g. *"Fed decision in March"*). Contains one or more markets.
- **Market** = a single tradable **binary** outcome inside an event. Maps to a `conditionId`, a `questionID`, a pair of CLOB token IDs (`clobTokenIds` = `[YES_token, NO_token]`), and a market (contract) address.
- **Single-market event** = one binary question. **Multi-market event** = several mutually-exclusive binary markets grouped (e.g. Fed "25 bps" / "50 bps" / "no change", or crude-oil price thresholds) — these are the sibling markets you normalise to sum to 1.0.

**Outcome tokens** are ERC-1155 conditional tokens (Gnosis Conditional Token Framework) on Polygon, priced $0–$1, collateralised 1:1 in **USDC.e**: one YES + one NO always redeems for exactly $1.00. A YES price of $0.34 ⇒ ~34% implied probability.

> ⚠️ A market only has live order-book liquidity if its `enableOrderBook` field is `true`. Always check this before treating a price as tradable; otherwise you may be reading a stale/last price.

---

## 2. Polymarket — endpoints (all public, no auth for reads)

### 2.1 Gamma API — discovery + prices · base `https://gamma-api.polymarket.com`

| Endpoint | Purpose |
|---|---|
| `GET /events` | List events (top-level questions). Primary discovery surface. |
| `GET /markets` | List markets (tradable binaries), incl. `outcomes`, `outcomePrices`, `clobTokenIds`, `conditionId`, `enableOrderBook`, `negRisk`, volume, liquidity. |
| `GET /markets/keyset` | **Keyset (cursor) pagination** over all markets — use this for full backfill; resumable from a saved cursor. |
| `GET /search` | Full-text search across events/markets (the "public-search" surface) — best for a free-text topic. |
| `GET /tags` | Category tags; combine with `tag_id`/`tag_slug` filtering on `/events`. |
| `GET /series` | Grouped collections of related events. |

**Common query parameters** (events/markets): `limit`, `offset`, `order` (`volume24hr`, `startDate`, `endDate`), `ascending` (default `false`), `tag_id`, `tag_slug`, `active` (unresolved only), `closed` (resolved only), `archived`.

**Topic → markets, ranked by activity** (the discovery call):
```
GET https://gamma-api.polymarket.com/events?active=true&closed=false&order=volume24hr&ascending=false&limit=100
```
Or free-text: `GET /search?q=<topic>` → take matched events → pull their markets.

**Reading the probability** — each market's `outcomes` and `outcomePrices` are 1:1 arrays:
```json
{ "outcomes": ["Yes", "No"], "outcomePrices": ["0.20", "0.80"] }
// index 0 "Yes" → 0.20  (20% implied)   |   index 1 "No" → 0.80 (80%)
```
`outcomePrices[0]` is the quick read. For the precise tradable price, use the CLOB order-book mid (§2.2). Prices are strings → parse with `Decimal`, never `float`.

```python
import httpx, decimal
async def discover(topic: str) -> list[dict]:
    async with httpx.AsyncClient(base_url="https://gamma-api.polymarket.com", timeout=15) as c:
        r = await c.get("/search", params={"q": topic})
        r.raise_for_status()
        return r.json()  # validate into MarketRef in app/models, do not pass raw dicts inward
```

### 2.2 CLOB API — order books + precise price · base `https://clob.polymarket.com`

Public for reads (no auth). The `token_id` comes from Gamma's `clobTokenIds` (YES = first, NO = second).

| Endpoint | Returns |
|---|---|
| `GET /book?token_id=<id>` | Full order book: `bids`, `asks`, `tick_size`. Mid of best bid/ask = implied probability. |
| `GET /price?token_id=<id>&side=buy\|sell` | Best price for a side. |
| `GET /midpoint?token_id=<id>` | Mid-price directly. |
| `GET /tick-size?token_id=<id>` | Market tick size. |
| `GET /prices-history?market=<token_id>&interval=...` | Historical price time series. |

```python
async def order_book(token_id: str) -> dict:
    async with httpx.AsyncClient(base_url="https://clob.polymarket.com", timeout=15) as c:
        r = await c.get("/book", params={"token_id": token_id})
        r.raise_for_status()
        return r.json()  # {"bids":[{"price":"0.62","size":"500"}...], "asks":[...], "tick_size":"0.01"}
```
`implied_probability` (in `app/analysis/`) takes this book and returns the mid + a thin-market flag when the spread is wide or depth is thin. **No network in the analysis layer** — the source returns the book, analysis computes.

### 2.3 Data API — wallet activity · base `https://data-api.polymarket.com`
`GET /positions?user=<wallet>` and `GET /trades?user=<wallet>` give per-wallet holdings and trade history. Useful for enriching wallet scores once you have wallet addresses from the chain (§3).

### 2.4 WebSocket — live updates (optional, for low-latency)
`wss://ws-subscriptions-clob.polymarket.com/ws/market` streams book snapshots, tick updates, and last-trade price; subscribe with `{ "type": "market", "assets_ids": ["<token_id>"] }`. For a 2-hourly batch ingester you do **not** need this; REST polling is simpler and sufficient. Reach for WS only if you later want near-real-time updates.

---

## 3. Polymarket — on-chain (smart-money), the *optional* enrichment layer · **(v2)**

> **Deferred to v2.** v1 does not read on-chain data. This section is the reference for when the
> smart-money layer is built; the heavy backfill will run on GitHub Actions (not a Vercel
> function) into the same Postgres. See `ARCHITECTURE.md` §9.

This is where the "extract from blockchain" requirement lives. It is Polymarket-only and not needed for plain probabilities.

**What to read:** the **`OrderFilled`** event on the **CTF Exchange** contract on Polygon — the definitive on-chain record of a filled trade (maker, taker, token id, size, price, side). Cluster fills by wallet → score wallets by realised P&L on resolved markets → quality-weighted net flow = smart-money tilt. (`PositionsMerged` and `ConditionResolution` are also emitted and useful for P&L/resolution.)

**Two ways to get it:**

1. **Direct JSON-RPC log reading (recommended, robust).** Use `web3.py` against a Polygon RPC, scan `OrderFilled` logs over a block range, resumable from the last scanned block. This is what the current reference retriever does (see §6). Public Polygon RPCs are rate-limited and time out on backfill — use a free **Alchemy/QuickNode** key (or paid for serious volume).
   ```python
   from web3 import Web3
   w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url))  # from config, never hardcoded
   exchange = w3.eth.contract(address=settings.ctf_exchange_address, abi=CTF_EXCHANGE_ABI)
   logs = exchange.events.OrderFilled().get_logs(fromBlock=last_block, toBlock="latest")
   ```
2. **Subgraph (GraphQL).** Polymarket publishes open-source subgraphs (Orders/order book, Positions, Activity, Open Interest, PnL), historically hosted on **Goldsky** and also on **The Graph Network** (100k free queries/month). Query with `gql`/`requests`. **Caveat — see §7:** the free Goldsky path was degraded by the April-2026 contract migration; verify a subgraph is current before depending on it.

> For a 2-hourly cron whose job is "what changed in the predictions," **on-chain enrichment is the part to make optional and degrade gracefully.** Probabilities (Gamma + CLOB) are cheap and reliable; full on-chain backfill is heavy. Omit the tilt when RPC is unavailable rather than blocking the snapshot — exactly the degradation rule in `CLAUDE.md` §9.

---

## 4. Kalshi — data model + endpoints (public reads, no auth)

**Data model (three levels):** **Series** (recurring template, e.g. *"Highest temperature in NYC today"*) → **Event** (a specific occurrence) → **Market** (a binary outcome with yes/no, current prices, volume, settlement rules).

**Base URL:** `https://api.elections.kalshi.com/trade-api/v2`
> Despite the `elections` subdomain, this production Trade API serves **all** Kalshi markets — economics, climate, tech, weather, sports, etc. (Some older docs reference `external-api.kalshi.com/trade-api/v2`; treat `api.elections.kalshi.com` as canonical and verify at build time.)

| Endpoint | Purpose |
|---|---|
| `GET /markets` | List/discover markets. Params: `limit` (1–1000, default 100), `cursor` (pagination), `event_ticker`, `series_ticker`, `status` (`open`/`closed`/`settled`/`determined`/`initialized`, comma-sep; note `open` filters but the response says `active`), `tickers`, `min_close_ts`, `max_close_ts`. |
| `GET /markets/{ticker}` | One market's detail. |
| `GET /events` , `GET /events/{event_ticker}` | List / fetch events. |
| `GET /series` , `GET /series/{series_ticker}` | Series list / one series (incl. category, settlement sources, volume). |
| `GET /markets/{ticker}/orderbook` | Order-book top (yes/no bids & asks). |
| `GET /markets/trades` | Recent trades (ticker, price, qty, timestamp). |
| `GET /series/{ticker}/markets/{ticker}/candlesticks` | Historical OHLC time series. |

**Pagination is cursor-based:** read `cursor` from the response, pass it back; empty cursor = done.

**Reading the probability.** ⚠️ **Verified live 2026-06-16 — the field names changed.** The legacy cent fields (`yes_bid`/`yes_ask`/`no_bid`/`no_ask`/`last_price`/`volume`) **no longer exist**. The current `/markets` response returns **dollar-denominated string** prices already in 0..1 probability units, plus fixed-point string sizes/volumes:

| Field | Meaning |
|---|---|
| `yes_bid_dollars`, `yes_ask_dollars`, `no_bid_dollars`, `no_ask_dollars` | best quotes, **dollar strings 0.0000–1.0000** (= probability directly, no /100) |
| `last_price_dollars`, `previous_price_dollars` | last / previous trade price (dollar string) |
| `volume_fp`, `volume_24h_fp`, `open_interest_fp`, `*_size_fp` | fixed-point **strings** (e.g. `"5000"`) |
| `liquidity_dollars`, `notional_value_dollars` | dollar strings |
| `ticker`, `event_ticker`, `title`, `status` | identity/classification (`status` returns `active` for open markets) |

So the yes probability is the mid `(Decimal(yes_bid_dollars) + Decimal(yes_ask_dollars)) / 2` — **no division by 100**. Parse every price string with `Decimal`. Response is `{"cursor": ..., "markets": [...]}`.

```python
async def kalshi_markets(series_ticker: str) -> list[dict]:
    base = "https://api.elections.kalshi.com/trade-api/v2"
    async with httpx.AsyncClient(base_url=base, timeout=15) as c:
        r = await c.get("/markets", params={"series_ticker": series_ticker, "status": "open"})
        r.raise_for_status()
        # each market: ticker, title, yes_bid_dollars, yes_ask_dollars, last_price_dollars,
        #              volume_24h_fp, status, ...
        return r.json()["markets"]
```

**Auth (only for trading/portfolio/WebSocket, which we never do):** an API key id plus an **RSA private key (PEM)**; requests are signed. Market-data reads above need none of this. Kalshi tokens for the authenticated paths expire ~every 30 minutes — irrelevant to read-only ingestion.

---

## 5. Reconciliation, normalisation, and provenance (where the layers meet)

- **Pair the same real-world event across venues** by matching topic/title/close-date so the caller sees cross-venue divergence (itself a signal). Both expose volume/liquidity for ranking; drop dead/thin markets.
- **Normalise** sibling outcomes within one event to sum to 1.0 (raw rarely does, due to spread/fees); **store raw + normalised + factor** (`EventDistribution`).
- **Provenance on every probability:** venue, endpoint, raw value (string→`Decimal`), timestamp, and the normalisation factor. "Why 62%?" must trace to a snapshot.
- **Per-venue capability matrix in the response:** Polymarket = price + volume + depth + on-chain tilt; Kalshi = price + volume + depth, tilt `null`. Degrade gracefully when a venue has no match.

---

## 6. Working reference repositories (study these, don't reinvent)

- **`Polymarket/agents`** (Python) — official; shows Gamma usage (`gamma-api.polymarket.com/markets` + `/events`) and Pydantic parsing of market objects. Good model for the source layer.
- **`warproxxx/poly_data`** (Python, updated within the last week) — a **Polymarket data retriever** doing almost exactly the ingestion half of this project: fetches all markets via Gamma keyset (`/markets/keyset`, resumable cursor), reads `OrderFilled` from the CTF Exchange **V2** contract on Polygon via direct JSON-RPC (resumable by block), and joins events with metadata into labelled trades (price, USD, BUY/SELL). Its README documents the April-2026 migration (§7).
- **`Polymarket/clob-client`** (TS) / **py-clob-client** (Python) — official CLOB SDKs; useful for exact request/response shapes even though we only read.
- **`Polymarket/polymarket-subgraph`** — the open-source subgraph manifests (entities/fields) if you go the GraphQL route.
- **Kalshi**: official SDKs (`kalshi-typescript`, Python) mirror the REST endpoints in §4.

---

## 7. ⚠️ Recent changes & caveats — *this is exactly why addresses live in config and get re-verified*

- **Polymarket migrated to a new set of CTF Exchange contracts on 2026-04-28** and stopped supporting the old subgraph indexer. The previously common "free Goldsky subgraph + GraphQL polling" path **no longer returns complete data**. New work reads `OrderFilled` **directly on-chain** from the current exchange. **Consequence:** the on-chain contract address is **not** a constant you can hardcode from memory — confirm the live address on PolygonScan / official docs before building `polymarket_chain.py`.
- **Addresses observed during this research (Polygon, chain ID 137) — treat as starting points to verify, not gospel:**

  | Contract | Address (verify) |
  |---|---|
  | CTF Exchange **V2** (current; emits `OrderFilled`) | `0xE111180000d2663C0091e4f400237545B87B996B` |
  | CTF Exchange (older / V1) | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
  | NegRisk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` |
  | Conditional Tokens (ERC-1155, collateral/positions) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
  | USDC.e (collateral) | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |
  | UMA Optimistic Oracle (resolution) | `0xCB1822859cEF82Cd2Eb4E6276C7916e692995130` |
  | UMA CTF Adapter | `0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74` |

- **Public Polygon RPCs are rate-limited and unreliable for backfill.** Use an Alchemy/QuickNode key (free tier is fine to start). The RPC URL is a config secret.
- **There is no Polymarket testnet** — it's Polygon mainnet only. Irrelevant to read-only ingestion, but means any future write work uses real funds.
- **Kalshi base URL / field names**: confirm `api.elections.kalshi.com/trade-api/v2` and the exact price field names against the live `Get Market` response; Kalshi's status filter accepts `open` but returns `active`.
- **Both venues' rate limits and ToS apply.** Cache immutable history permanently, live prices with a short TTL, back off on 429s (`CLAUDE.md` §10).

---

## 8. How this maps onto the architecture

- `sources/polymarket_gamma.py` → §2.1 · `sources/polymarket_clob.py` → §2.2 · `sources/polymarket_chain.py` → §3 (Polygon RPC, addresses from config) · `sources/kalshi.py` → §4.
- `analysis/` consumes the typed payloads from §2–§4 and computes probabilities/distributions/tilt with **no network**.
- The **2-hourly ingestion** (Vercel cron → `GET /internal/refresh`) calls discovery → prices for both venues (cheap, always on) → analyse/normalise → upsert to Postgres + append change-log. On-chain enrichment is **v2** and runs on GitHub Actions when added.
- `config.py` holds every base URL (and, in v2, the Polygon RPC URL and contract addresses) — **nothing in §7 is hardcoded in code.**
