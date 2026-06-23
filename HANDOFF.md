# HANDOFF.md

Practical implementation/handoff doc — what exists, how it works, the data contract, how to
integrate it, and what's left. Written so another engineer or AI agent can continue without
re-deriving the system. Companion docs: `ARCHITECTURE.md` (structure), `CLAUDE.md` (rules),
`DATA_SOURCES.md` (venue APIs), `DEPLOYMENT.md`, `INGESTION.md`.

---

## 1. Status & what was achieved

**v1 is built, live-verified, and runnable via Docker.** A **read-only** FastAPI backend: given a
free-text topic it discovers live prediction markets on **Polymarket + Kalshi**, converts prices
into normalised probability distributions with provenance, persists state + change history in
**Postgres**, and serves strictly-typed JSON (plus a `/ui` page). LLM synthesis and on-chain
smart-money are **designed-for v2 seams, not implemented**.

Build trail (git, oldest→newest):

| Commit | What landed |
|---|---|
| `7d6781a` | Docs reconciled to one v1 scope (Vercel + Postgres; LLM/on-chain → v2) |
| `32f0878` | Scaffold + core (logging/correlation-id, errors, http, rate-limit, db pool) + config + models + `/health` |
| `77c44ab` | Pure analysis layer (probability, distribution, changes) — hand-checked tests |
| `c7a096a` | Async source clients (Gamma, CLOB, Kalshi) — respx-mocked |
| `f04c4a1` | Persistence (`MarketRepository` ABC + asyncpg impl + `schema.sql` + migrate) |
| `a7f138f` | Services + API (`/analyze`, `/markets/*`, `/internal/refresh`) + e2e |
| `cf6bc7d` | Live API verification; fixed Kalshi field drift (dollar-string prices) |
| `e8b5db2` | **Event-level distributions** + Kalshi navigational discovery |
| `b5892ce` | Python 3.12, Dockerized Postgres (Supabase-ready), `/ui` frontend |

**Proven live:** `POST /analyze "fed rate decision"` returns ~17 normalised distributions across
both venues; a real ingestion cycle landed 85 rows (21 Polymarket, 65 Kalshi) in Postgres; the
gated asyncpg integration test passes; full `docker compose up` serves `/health`, `/analyze`,
`/ui`. Gate: `ruff` + `mypy` clean, 56 tests pass (1 skipped = the DB integration test, runs only
when `TEST_DATABASE_URL` is set).

---

## 2. How it works — architecture & data flow

**Layering (dependencies point inward; enforced by review):**
```
api → services → {sources, analysis, persistence} → models
core (logging, errors, http, rate_limit, db) is cross-cutting; config is the only env reader
```
- `analysis/` is **pure** (no network/SQL imports). `persistence/` is the **only** module touching
  Postgres (behind the `MarketRepository` ABC). `sources/` is the only network I/O. `models/` is
  the single source of truth (Pydantic v2, `Decimal` for prices, `Literal` venues, UTC datetimes).

**The Event→Markets rule (core correctness point).** Both venues model data as **Event → Markets**
(a question containing one or more outcome-markets). Discovery returns **one `MarketRef` per
event**:
- **Mutually-exclusive multi-outcome** (Polymarket `negRisk=true`; Kalshi `mutually_exclusive=true`)
  → one distribution whose outcomes are the sibling markets (e.g. "No change" / "+25bps" / …),
  normalised to sum to 1.
- **Single binary / non-exclusive** → Yes/No (or one binary ref per child).

### Flow A — `POST /analyze` (request-time)
`app/api/analyze.py` → `services/analysis_service.analyze()`:
1. Read the store for the topic (`persistence/repository.read_topic`).
2. If fresh (within `LIVE_TTL_SECONDS`) → assemble `TopicAnalysis` from stored rows. Else **bounded
   live top-up**: `discovery_service.discover()` → `gateway` fans out to `sources/*` with
   `asyncio.gather(return_exceptions=True)` → `analysis.implied_probability` /
   `analysis.normalise_distribution` (via `services/pricing.py`) → assemble.
3. Graceful degradation, each logged at WARNING: no Kalshi match → Polymarket-only + note; live
   empty but store has data → serve stale; one market fails → drop it, keep the rest.

### Flow B — Ingestion cron (`GET /internal/refresh` or `python -m app.ingest`)
`services/ingestion_service.run_ingestion()`: `discover → flag priority → analyse/normalise →
upsert → append change-log for material moves (≥ MATERIAL_CHANGE) → purge stale untracked rows
(> RETENTION_DAYS)`. On Vercel this is the `CRON_SECRET`-guarded endpoint; locally it's the CLI.

**Venue facts that shape the source code** (verified live; re-verify per `CLAUDE.md` §13):
- **Polymarket** runs on Polygon. Discovery via Gamma `/public-search`; quick price =
  `outcomePrices`; precise price = CLOB `/book` mid (binary markets only). Multi-outcome label =
  `groupItemTitle`.
- **Kalshi** is centralized REST, **no blockchain, no keyword search**. Prices are
  **dollar-denominated strings already in 0..1** (`yes_bid_dollars` etc. — NOT cents). Outcome
  label = `yes_sub_title` (`title` is deprecated). Discovery is navigational: topic →
  `KALSHI_SERIES_MAP` (explicit) or `KALSHI_CATEGORY_MAP` → `/series?category=` keyword-match →
  `/events?with_nested_markets=true`.

---

## 3. The data contract (`POST /analyze` → `TopicAnalysis`)

Defined in `app/models/responses.py` + `app/models/domain.py`. Trimmed example:

```jsonc
{
  "topic": "fed rate decision",
  "generated_at": "2026-06-22T16:00:00Z",
  "stale": false,                       // true = served from store without a live refresh
  "markets": [ /* MarketRef[] */ ],
  "distributions": [
    {
      "venue": "polymarket",            // "polymarket" | "kalshi"
      "event_title": "Fed Decision in July?",
      "market_key": "0x…",              // conditionId (PM) / event_ticker (Kalshi)
      "outcomes": [
        {
          "outcome": "No change",
          "probability": "0.7354",      // Decimal string, normalised (sums to ~1 across outcomes)
          "raw_price": "0.7354",
          "provenance": { "venue": "polymarket", "endpoint": "/book",
                          "raw_value": "0.7354", "observed_at": "…",
                          "normalisation_factor": "1.00" },
          "confidence": { "level": "ok", "reasons": [] }   // "ok" | "thin" | "stale"
        }
      ],
      "raw_sum": "1.00", "factor": "1.00", "normalised": true
    }
  ],
  "venue_availability": [
    { "venue": "polymarket", "matched": true,  "signals": ["price","volume","depth"], "note": null },
    { "venue": "kalshi",     "matched": true,  "signals": ["price","volume","depth"], "note": null }
  ],
  "notes": [],                          // degradation/caveat strings
  "llm_synthesis": null,                // v2 seam — always null in v1
  "disclaimer": "Decision-support data only. … Not financial advice."
}
```

**Endpoints** (OpenAPI at `/docs`):

| Method | Path | Purpose |
|---|---|---|
| POST | `/analyze` | Topic → cross-venue distributions (headline) |
| GET | `/markets/search?q=&venue=&limit=` | Live discovery of matching markets (`MarketRef[]`) |
| GET | `/markets/{venue}/{market_key}` | One stored market's detail (`MarketDetail`) |
| GET | `/markets/history?venue=&market_key=&outcome=&limit=` | Change-log time series (`HistoryPoint[]`) |
| GET | `/health` | Liveness + DB round-trip (`{status, database, version}`) |
| GET | `/internal/refresh` | `CRON_SECRET`-guarded ingestion trigger (`RefreshResult`) |
| GET | `/ui`, `/` | Verification frontend (`/` redirects to `/ui/`) |
| GET | `/docs` | Auto OpenAPI |

Money/probabilities serialise as **Decimal strings** — parse accordingly downstream.

---

## 4. How information is presented

- **Typed JSON API** — every boundary is a Pydantic model; OpenAPI/Swagger renders at `/docs`.
- **Verification frontend** — `frontend/index.html` (self-contained vanilla HTML/CSS/JS, no build,
  no CDN). Served by FastAPI via `StaticFiles` at `/ui` (dir resolved from `__file__` in
  `app/main.py`). Topic box → `POST /analyze` → each distribution rendered as a card with outcomes
  as sorted, labeled probability bars, plus venue-availability badges, notes, and the disclaimer.

---

## 5. Integrating into a larger codebase

It's a self-contained, **read-only** package (`app/`). No trading, custody, or signing — keep it
that way. Three integration modes:

1. **Mount the API.** `from app.main import app` (a FastAPI instance) and mount/host it, or import
   the routers (`app/api/{analyze,markets,health,internal}.py`) into a parent FastAPI app. Lifespan
   in `app/main.py` opens the asyncpg pool and the source gateway on `app.state`.
2. **Call the service layer directly (no HTTP).** Build `AnalyzeRequest(topic=…)` and call
   `await analysis_service.analyze(req, repo=…, gateway=HttpGateway(settings), settings=…)`. Returns
   a `TopicAnalysis`. Same for `ingestion_service.run_ingestion(...)`.
3. **Swap implementations at the seams.** Persistence is behind `MarketRepository`
   (`app/persistence/repository.py`) — implement it for another store and inject. Sources sit behind
   the `Gateway` protocol (`app/services/gateway.py`) — easy to fake (see `tests/fakes.py`) or add a
   venue. LLM is the `app/llm` seam (v2).

**Config & infra:** everything external is env-driven via `app/config.py` (pydantic-settings) —
`DATABASE_URL`, venue base URLs, `INGEST_TOPICS`, `KALSHI_SERIES_MAP`/`KALSHI_CATEGORY_MAP`,
thresholds (`MATERIAL_CHANGE`, `THIN_*`, `RETENTION_DAYS`, `LIVE_TTL_SECONDS`), `CRON_SECRET`,
`LOG_*`. Postgres must be a **pooled DSN** on serverless; `create_pool` sets
`statement_cache_size=0` so a **Supabase** transaction-pooler URL works unchanged. Logs are
structured JSON with a per-request correlation id (`app/core/logging.py`).

---

## 6. Run it

```bash
# Local (Python 3.12 venv managed by uv; deps in pyproject [project] + [dev])
uv venv --python 3.12 .venv && uv pip install -e ".[dev]" asyncpg
./.venv/Scripts/python.exe -m ruff check app tests
./.venv/Scripts/python.exe -m mypy app
./.venv/Scripts/python.exe -m pytest -q                       # 56 passed, 1 skipped

# Full stack in Docker (db + migrate + api), then open http://localhost:8000/ui
docker compose up --build
#   host 5432 busy? ->  DB_PORT=5434 docker compose up --build

# Manual DB ops (DSN points at the compose db)
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/predmarket python -m app.persistence.migrate
DATABASE_URL=… INGEST_TOPICS="fed rate decision" KALSHI_SERIES_MAP='{"fed rate decision":"KXFEDDECISION"}' python -m app.ingest

# The DB integration test runs only when a real DB is provided:
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/predmarket pytest tests/persistence/test_postgres_integration.py
```

---

## 7. State & next steps (for the next agent)

**Done ✅** — both venues discovered live with correct per-outcome probabilities; event-level
normalised distributions; provenance + confidence + per-venue availability; Postgres persistence
+ change-log proven on a real DB; Docker stack; `/ui`; full mocked test suite green on 3.12.

**Not done ❌ / next:**
- **Cloud deploy (the immediate next step):** Vercel + Neon. `vercel.json` exists (cron →
  `/internal/refresh`); provision Neon, set env vars (use the **pooled** DSN), run `migrate` once.
  See `DEPLOYMENT.md`.
- **v2 LLM synthesis:** seam is `app/llm/` + the `llm_synthesis` response field (currently `null`).
  Add an `LLMProvider` (hosted Claude); it must emit **no** numbers/IDs/advice. Re-add `app.llm` to
  `pyproject` packages when built.
- **v2 on-chain smart-money:** Polymarket-only; `web3` optional extra; heavy `OrderFilled` backfill
  runs on **GitHub Actions** (not a Vercel function) into the same Postgres.

**Known gotchas:**
- **Re-verify venue API shapes before trusting them** (`CLAUDE.md` §13). They drift — the Kalshi
  cents→dollar-string change already bit us once.
- **Kalshi has no search.** A topic returns nothing unless it maps via `KALSHI_SERIES_MAP`
  (reliable) or `KALSHI_CATEGORY_MAP` (fuzzy title keyword-match — improve if precision matters).
- **Local DB port:** this machine has Postgres on 5432/5433 → use `DB_PORT=5434`. Committed default
  is 5432.
- **Decimal strings** in JSON — don't parse as float blindly downstream.

Project decisions/history persist in agent memory (`v1-architecture-decision`,
`agent-team-workflow`). Build sequence + gates are in `PLANNING.md`.
