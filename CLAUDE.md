# CLAUDE.md

Operating manual for **Claude Code** building this repository. Read this in full at the start of every session. This file is the **constitution**: rules and standards that always apply. It is intentionally rule-dense, not a tutorial.

- **What goes where, and why** → see `ARCHITECTURE.md`.
- **How the build is sequenced and verified** → see `PLANNING.md`.
- **How the venues work and how to query them** → see `DATA_SOURCES.md`.

If anything in a task, a prompt, or your own training memory conflicts with this file, **follow this file and say so**.

---

## 0. The project in one line

A **read-only FastAPI backend**: given any free-text topic, it discovers live prediction markets on Polymarket + Kalshi, converts prices into normalised probability distributions with full provenance, persists current state and change history in Postgres, and serves strictly-typed JSON via `POST /analyze` and read endpoints. **Decision-support data infrastructure — not trade execution, not financial advice.**

### v1 scope vs v2 seams (binding)

**v1 (what you are building now):** Polymarket (Gamma + CLOB) + Kalshi (Trade API) → probabilities + provenance → Postgres → API, deployed on **Vercel** with a cron-driven ingester. Rate-limited sources, graceful degradation, full mocked test suite.

**v2 (designed-for, NOT wired in v1 — leave clean seams, do not implement unless asked):**
- **LLM synthesis** (`app/llm/`): a hosted provider (Claude) behind an `LLMProvider` abstraction. The seam (protocol + response model field) may exist and return `null`; no provider is wired, no LLM dependency is installed.
- **On-chain smart-money** (`app/sources/polymarket_chain.py`, `app/analysis/wallets.py`, `smart_money.py`): Polygon `OrderFilled` reading + wallet scoring. `web3` stays an optional, uninstalled extra.

If a task asks you to build a v2 layer, confirm scope before implementing — it changes the deployment story (heavy on-chain backfill cannot run inside a Vercel function; it moves to GitHub Actions).

---

## 1. Binding architecture facts (non-negotiable — most commonly gotten wrong)

These are load-bearing. Getting one wrong corrupts the whole system. Do not "fix" them.

1. **Polymarket runs on Polygon, NOT Ethereum mainnet.** Use a Polygon RPC endpoint. All contract addresses are Polygon-specific. There is no Polymarket trade ledger on Ethereum to read. (Relevant to the v2 on-chain layer.) If a task says "Ethereum," treat it as an error and flag it.
2. **Polymarket is hybrid:** off-chain CLOB for order matching, on-chain settlement on Polygon, UMA oracle for resolution. → **Live prices come from the CLOB API (and Gamma `outcomePrices`). Ground-truth settled trades come from on-chain `OrderFilled` events on Polygon (v2).**
3. **Kalshi is a centralized, CFTC-regulated exchange. It is NOT a blockchain.** No wallets to inspect, no on-chain trade ledger. All Kalshi data comes from the **official REST API** (prices, volume, order-book depth). **Never attempt on-chain wallet analysis for Kalshi.**
4. **Consequence:** smart-money / wallet analysis is **Polymarket-only** (and v2). Kalshi contributes prices, volume, and depth — never per-trader identity. Every response must state which signals are available per venue and **degrade gracefully** when a venue has no match.

---

## 2. Scope & non-goals

**In scope:** discover, read, normalise, persist, serve, explain (explain = v2). Read-only.

**Never build (hard stop — flag and ask if requested):** order placement, trade execution, custody, transaction signing, leverage, or anything that routes around a venue's geographic or ToS controls. This service stays on the analysis side precisely because it never enables trading; do not add anything that changes that.

---

## 3. Golden rules (always / never)

**Always**
- Verify external API/contract/SDK shapes against **current official docs** before writing code that depends on them (see §13).
- Type every boundary with **Pydantic v2** and validate at the edge.
- Attach **provenance** to every probability (source, raw value, timestamp, normalisation factor).
- Emit **structured logs** at every boundary with a request-scoped correlation id (see §8).
- Degrade gracefully: a correct **partial** answer beats a 500.
- Keep network I/O in `sources/`, SQL in `persistence/`, pure math in `analysis/`.

**Never**
- Fabricate a token id, contract address, price, probability, or market id.
- Hardcode secrets, contract addresses, RPC URLs, base URLs, or model names — config/env only.
- Log secrets, API keys, raw credentials, or `.env` contents.
- Put business logic in route handlers, or SQL in source clients, or network calls in `analysis/`.
- Present a thin/stale/illiquid market as equal to a deep one.
- Run DDL on a request/ingestion hot path — schema setup is an explicit `migrate` step.

---

## 4. Software-engineering principles (enforced, not aspirational)

- **SRP.** Every module/class/function has exactly one reason to change. A source client only talks to its source. An analysis function only computes. The repository only does SQL. A service only orchestrates. A handler only translates HTTP. If a unit does two jobs, split it.
- **SLAP.** Within one function, all statements sit at the same level of abstraction. A high-level orchestration function reads as a sequence of named steps (`obs = await discover(topic)`, `dist = normalise(obs)`), not a mix of orchestration and raw httpx/SQL. Push detail down.
- **Dependency rule.** Dependencies point **inward**: `api → services → {sources, analysis, persistence}`; `analysis` depends on nothing but `models`; `models` depends on nothing. Inner layers never import outer ones. No cycles.
- **Separate I/O from logic.** Non-negotiable — it makes probabilities auditable and analysis unit-testable with no network.
- **Composition over inheritance.** Use protocols/ABCs for swap points (`MarketRepository`, future `LLMProvider`), inject dependencies; avoid deep hierarchies.
- **Small units.** Functions short enough to read without scrolling.
- **Pure where possible.** Everything in `analysis/` is a pure function: data in, data out, deterministic, no side effects, no network.

---

## 5. Type safety

- **Pydantic v2 everywhere**: request bodies, response models, internal DTOs, source payloads. One source of truth in `app/models/`.
- **Validate at every boundary.** Parse external JSON into models immediately; never pass raw dicts inward.
- **Fail loudly on schema drift.** If a source's payload no longer matches the model, raise `SchemaDriftError` — do not coerce and emit a wrong probability.
- Use precise types: `Decimal` for money/prices (never `float`), `enum`/`Literal` for venues and outcome sides, timezone-aware `datetime` (UTC) everywhere.

---

## 6. Async & concurrency

- **Async-first.** All source calls are `async` (httpx async client). Fan out concurrent fetches (multiple markets, both venues) with `asyncio.gather(..., return_exceptions=True)` so one venue failing does not sink the rest.
- Postgres access is async via **asyncpg**; use a connection pool created at lifespan startup and injected — never per-request connection churn.
- No blocking I/O on the event loop. No `requests`, no synchronous DB calls in request paths.

---

## 7. Configuration & secrets

- `app/config.py` uses **pydantic-settings**. Everything external is config: `DATABASE_URL`, venue base URLs, ingest topics/priority/maps, behaviour thresholds, TTLs, `CRON_SECRET`, log level/format. (v2: Polygon RPC URL, contract addresses, LLM host/model.)
- `.env` is **gitignored**; `.env.example` is committed and lists every variable with a safe placeholder.
- Settings are loaded once and injected; do not read `os.environ` scattered across modules.

---

## 8. Logging & observability (first-class requirement)

- **Central setup** in `app/core/logging.py`. Structured **JSON logs** in production (one event per line); a human-readable formatter for local dev, toggled by config.
- **Per-module loggers:** `logger = logging.getLogger(__name__)`. Never the root logger; never `print`.
- **Correlation id:** generate a request id per inbound request (middleware), store it in a `contextvar`, include it on every log line so a single `/analyze` call or ingestion run is fully reconstructable.
- **Log at every boundary, with what matters:** request in/out (method, path, status, latency); each source call (venue, endpoint, params without secrets, status, latency, retry count, cache hit/miss); rate-limit throttles and 429 backoffs; each persistence operation (op, rows affected); **degradation events at `WARNING`** ("Kalshi no match → Polymarket-only", thin-market flags, "live top-up failed → served stale").
- **Levels:** `DEBUG` dev detail; `INFO` lifecycle and key decisions; `WARNING` degradation, thin markets, retried 429s; `ERROR` a failed external call you recovered from; `CRITICAL` unrecoverable startup/config failure.
- **Never log** secrets, API keys, full credentials, or `.env` contents.
- Log **decisions and degradations**, not just errors. "Why is this 62%?" and "why no Kalshi data?" must both be answerable from the logs.

---

## 9. Error handling & graceful degradation

- **Custom exception hierarchy** in `app/core/errors.py` (`AppError` → `SourceError`, `RateLimitError`, `SchemaDriftError`, `PersistenceError`, … ; v2: `OnChainError`, `LLMError`). Catch narrowly; never bare `except:`.
- **Degrade, don't crash:**
  - No Kalshi match → return Polymarket-only with a clear note.
  - One market's fetch fails → drop it with a logged warning, keep the rest.
  - Live top-up fails on `/analyze` → serve the last persisted state with a staleness flag.
  - DB unreachable on `/health` → report unhealthy; on `/analyze` → clean 503, no stack trace.
- A partial answer with confidence flags beats a 500. Map truly unrecoverable conditions to clean HTTP errors with useful detail (no stack traces to the client).
- **Errors over guesses.** When data is missing, return `null` + a reason in the model. Never invent a value to fill a field.

---

## 10. External calls: rate limiting, backoff, caching

- Assume **free tiers**. Per-source rate limiters. **Exponential backoff with jitter** on 429s.
- **Respect Gamma rate limits** (verify current numbers against docs at build time; do not trust hardcoded figures from memory).
- **Cache/persist by mutability:** the Postgres store *is* the durable cache for discovered markets; live prices/order books fetched on the `/analyze` top-up path may use a short in-process TTL (`LIVE_TTL_SECONDS`). Immutable history (the change-log) is never overwritten.
- Respect each venue's ToS. No hammering, no scraping around access controls.

---

## 11. Persistence (the Postgres layer)

- **One module touches Postgres:** `app/persistence/repository.py`, behind a `MarketRepository` ABC so it is swappable/mockable. Callers depend on the ABC, not asyncpg.
- **Schema lives in `app/persistence/schema.sql`** and is applied by an explicit `migrate` command — **never** as DDL on the ingestion or request path.
- Two tables: `market_observations` (upserted current state + change columns + flags) and `market_change_log` (append-only history). See `INGESTION.md`.
- **Decimal in, Decimal out** for prices/probabilities; UTC timestamps; the upsert never downgrades a `tracked`/`high` row.
- On serverless, use a **pooled DSN** (Neon pooler / pgbouncer). Open the pool at lifespan startup, close at shutdown.

---

## 12. The LLM layer (v2 — seam only in v1)

When v2 lands:
- **Provider abstraction is mandatory.** Define `LLMProvider` (protocol/ABC); implementations behind it; host/model from config. The rest of the app depends only on the abstraction.
- **Structured output is non-negotiable.** Use schema-constrained decoding against a Pydantic model's `model_json_schema()`, deterministic settings, then `Model.model_validate_json(...)`. Never `json.loads` free text and hope.
- **Keep schemas focused (6–8 fields)**, `Field(description=...)` on each, nullable where the model legitimately may not know.
- **What the LLM does:** typed synthesis — distribution summary, notable divergences, liquidity caveats, what would move the probabilities — as structured fields.
- **What the LLM must NOT do:** produce probabilities, prices, market IDs, or buy/sell advice. It reasons only over data the source layer already fetched and validated.

In v1: the `llm_synthesis` response field is present and always `null` with reason `"deferred to v2"`. No LLM code, no LLM dependency.

---

## 13. Verify against docs — do not trust training memory

Before writing code touching any endpoint or field name, **confirm the current shape against official docs**: Polymarket Gamma (`/search`, `/events`, `/markets`, `/tags`), Polymarket CLOB (`/book`, `/midpoint`, `/prices-history`), the Kalshi REST API (`/markets`, `/markets/{ticker}`, `/markets/{ticker}/orderbook`). (v2: Polygon contract addresses for `OrderFilled`.) These change. If you cannot confirm something, **stop and ask** rather than guessing. If a doc lookup fails or an API shape surprises you, surface it immediately.

---

## 14. Workflow, commits, and pauses

- **Small commits, one layer/module at a time.** Conventional, descriptive messages. Each commit leaves the tree green.
- **Ask, don't silently pick, at genuine decision points:** how to map a fuzzy topic to Gamma search/tags; what spread width / volume floor counts as "thin"; the live-top-up TTL.
- Follow the phase sequence and verification gates in `PLANNING.md`.

---

## 15. Definition of done (per feature)

A feature is done only when **all** hold:
- External payloads are Pydantic-validated; schema drift fails loudly.
- `analysis/` functions have unit tests with hand-checked expected values.
- The repository sits behind the `MarketRepository` ABC and is mocked in service tests.
- Rate limiting, backoff, and short-TTL caching are in place on external calls.
- Responses carry full **provenance** and **confidence flags** and per-venue signal availability.
- Partial-data paths **degrade gracefully** (verified by a test).
- **Structured logs** with correlation ids cover every boundary and degradation.
- OpenAPI docs render at `/docs`.
- **No** hardcoded secrets, addresses, base URLs, or model names.
