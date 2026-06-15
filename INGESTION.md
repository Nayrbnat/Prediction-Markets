INGESTION.md

The data model, the priority/change/TTL rules, and how to run the ingester. This is the
write-side half of the system; `ARCHITECTURE.md` covers the whole.

> **v1.** Ingestion is **HTTP only** (Polymarket Gamma + CLOB, Kalshi Trade API) and runs on a
> **Vercel cron** every 2 hours. It fits inside a Vercel function's timeout, so no separate
> worker is needed. The on-chain (smart-money) enrichment is **v2** — when it lands, that heavy
> backfill moves to GitHub Actions writing the same Postgres; nothing else changes.

---

## 1. What it does (one paragraph)

Every 2 hours the ingester discovers prediction markets on your configured topics across
**Polymarket** (public Gamma API) and **Kalshi** (public Trade API), turns each outcome's price
into an implied probability (normalised across sibling outcomes), and **upserts the current
state to Postgres**. Most rows are static reference data. A subset you flag **high-priority** is
**tracked**: each run computes how its probability moved and appends a row to a change log, so
you can see how the market's belief **evolved**. Stale, unflagged rows are purged after a
retention window. The same `app/` package serves this data read-only via the API (`POST
/analyze`, `GET /markets/*`). No authentication or wallet is needed to read prices on either
venue.

---

## 2. The data model (what lands in Postgres)

Two tables (full DDL in `app/persistence/schema.sql`). You own the database; the app connects
with `DATABASE_URL` and reads/writes through `PostgresMarketRepository`.

**`market_observations` — current state (upserted each run).** One row per
`(venue, market_key, outcome)`. Key columns:

| Column | Meaning |
|---|---|
| `venue`, `market_key`, `outcome`, `event_title`, `topic`, `category` | identity + classification |
| `probability` | current implied probability, 0..1 (normalised within its event) |
| `previous_probability`, `probability_delta` | **the change, as columns** — value at last run and the move |
| `raw_price`, `volume`, `liquidity`, `confidence` | provenance + a `thin`/`ok` quality flag |
| `priority` (`high`/`normal`), `tracked` | the **flag** that decides retention + history |
| `first_seen_at`, `last_seen_at`, `last_changed_at`, `updated_at` | timestamps; `last_seen_at` drives the TTL |

**`market_change_log` — append-only history (the evolution).** One row each time a tracked
market moves materially: `venue`, `market_key`, `outcome`, `observed_at`, `probability`,
`previous_probability`, `delta`, `raw_price`. This is the time series behind "how has the
market's thinking changed."

---

## 3. Priority, change-tracking, and TTL (the three rules)

- **Everything is uploaded.** All discovered markets are upserted every run (the static layer).
- **Priority decides tracking.** Topics in `HIGH_PRIORITY_TOPICS` are marked `priority='high'`
  and `tracked=true`. The upsert **never downgrades** a row already high — so you (or another
  process) can flag a row in the DB directly and ingestion respects it. Only tracked markets get
  change-log rows, and only when `|delta| >= MATERIAL_CHANGE` (default 1pp).
- **TTL / auto-delete.** Each run purges rows that are **not** high-priority and **not** tracked
  whose `last_seen_at` is older than `RETENTION_DAYS` (default 15). Flagged rows and their
  history are kept regardless; purged rows are re-inserted if rediscovered later.

So cheap static data ages out; the few things you care about are tracked, diffed, logged, and
never silently dropped.

---

## 4. The serving API (what other codebases call)

Thin read handlers over the repository plus the live-analysis path:

- `POST /analyze {topic}` — the headline. Serves the topic's current cross-venue
  probabilities + provenance from Postgres; if the topic is absent or older than
  `LIVE_TTL_SECONDS`, does a **bounded live top-up** fetch and returns that. Always typed
  `TopicAnalysis`, with `llm_synthesis: null` in v1.
- `GET /markets/search?q=&venue=&limit=` — discovered markets matching a query.
- `GET /markets/{venue}/{id}` — one market's current detail.
- `GET /markets/history?venue=&market_key=&outcome=Yes&limit=` — the change-log time series.
- `GET /health` — liveness + a DB round-trip.
- `GET /docs` — auto-generated OpenAPI.
- `GET /internal/refresh` — `CRON_SECRET`-guarded trigger that runs one ingestion pass; this is
  the Vercel cron target.

---

## 5. The pipeline (per run)

`run_ingestion()` reads as named steps (SLAP), each detail one level down:

`discover` (both venues, concurrent, `return_exceptions=True` so one failing venue doesn't sink
the run) → `flag` priority/category from config → read `previous` probabilities → `analyse`
(implied probability + normalise sibling outcomes) → `upsert` current state (change columns
computed atomically) → append `change-log` rows for tracked markets that moved materially →
`purge` stale unflagged rows past the retention window.

---

## 6. Running it

**Local**
```bash
cp .env.example .env                       # fill DATABASE_URL + INGEST_TOPICS
pip install -e ".[dev]"
python -m app.persistence.migrate          # apply schema.sql once
python -m app.ingest                        # one ingestion run
uvicorn app.main:app --reload               # serve the API at :8000/docs
```

**On Vercel (the 2-hourly cron).** The cron in `vercel.json` hits `GET /internal/refresh`
every 2 hours on the production deployment; the handler checks `CRON_SECRET` and runs
`run_ingestion()`. Provision **Neon Postgres** via the Vercel Marketplace and use its **pooled**
DSN as `DATABASE_URL`. See `DEPLOYMENT.md`.

---

## 7. Notes & verification targets

- **Re-verify venue specifics** against `DATA_SOURCES.md`: Gamma discovery shape (`/search` vs
  `/events` + `tag_id`); Kalshi exact price field names; pagination cursors.
- **Discovery quality:** prefer `KALSHI_SERIES_MAP` for Kalshi where a topic maps to a known
  series; the open-markets scan is a bounded fallback.
- **Connection management on serverless:** always use a **pooled** DSN (Neon pooler / pgbouncer)
  — serverless invocations must not each open a raw Postgres connection.
- **v2 on-chain:** when enabled, add block-range checkpointing (resume from last scanned block)
  and run the wallet-scoring/smart-money pass on GitHub Actions, not inside a Vercel function.
