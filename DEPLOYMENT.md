# DEPLOYMENT.md

How this system is hosted. **v1 runs entirely on Vercel:** the FastAPI app serves the API, and a
Vercel cron drives the 2-hourly ingester; Postgres is **Neon**, provisioned through the Vercel
Marketplace. This document covers that setup and the Vercel constraints that shape it.

---

## 1. Topology (v1)

Both the serving API and the cron ingester are the **same `app/` package** on one Vercel
deployment. They share Postgres; they differ only in entry point (an HTTP request vs the cron
hitting `/internal/refresh`).

```
                         Vercel (serverless, @vercel/python)
   every 2h (cron)  ─▶  GET /internal/refresh ──┐
                                                 ├─▶  app/  ──▶  Neon Postgres (pooled)
   callers  ─ HTTP ─▶  POST /analyze, /markets/* ┘            observations + change-log
```

Lean v1 ingestion is HTTP only (Gamma + CLOB + Kalshi), so it finishes well inside a Vercel
function timeout — there is **no separate worker or GitHub Actions job in v1**. The heavy
on-chain backfill (v2) is the only thing that won't fit a function; when it arrives it moves to
GitHub Actions writing the same Postgres (§4).

---

## 2. `vercel.json`

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "builds": [{ "src": "app/main.py", "use": "@vercel/python" }],
  "routes": [{ "src": "/(.*)", "dest": "app/main.py" }],
  "crons": [{ "path": "/internal/refresh", "schedule": "0 */2 * * *" }]
}
```

The cron is just an authenticated HTTP request Vercel makes on schedule; the handler checks
`CRON_SECRET`. Crons run only on **production** deployments.

---

## 3. Vercel constraints that shape the design

1. **No persistent filesystem.** Only `/tmp` is writable and it isn't durable. → State lives in
   **Postgres**, never on disk. (This is why there is no SQLite in v1.)
2. **No long-running processes.** Functions are request-scoped. → Fine for v1 (HTTP ingestion).
   The v2 on-chain backfill does not fit and stays off Vercel.
3. **Function timeout.** Generous on current plans, but the ingester must still finish within it
   — keep `INGEST_TOPICS` and `PER_TOPIC_LIMIT` bounded so a refresh stays comfortably under the
   limit. If the watchlist grows large, split it or move ingestion to a worker.
4. **Pooled DB connections required.** Each invocation may be a fresh instance; use Neon's
   **pooled** DSN (pgbouncer) as `DATABASE_URL`, and open the pool at lifespan startup. Never
   open a raw connection per request.
5. **Cron auth + observability.** No built-in retries/alerts — the `/internal/refresh` handler
   guards with `CRON_SECRET`, logs the run with a correlation id, and degrades gracefully so a
   single failed venue doesn't fail the whole run.

---

## 4. Provisioning checklist

- [ ] In the Vercel project, add **Neon Postgres** from the Marketplace; copy its **pooled**
      connection string into the `DATABASE_URL` env var.
- [ ] Set the remaining env vars (venue base URLs, `INGEST_TOPICS`, `HIGH_PRIORITY_TOPICS`,
      thresholds, `LIVE_TTL_SECONDS`, `CRON_SECRET`, `LOG_LEVEL`, `LOG_FORMAT`) per
      `.env.example`.
- [ ] Run the migration once against the database: `python -m app.persistence.migrate` (locally
      with the prod `DATABASE_URL`, or via a one-off job).
- [ ] Deploy; confirm `GET /health` is green (DB round-trip) and `/docs` renders on the
      deployed URL.
- [ ] Confirm the cron fires `/internal/refresh` and a run lands rows in `market_observations`.
- [ ] **(v2 only)** When adding on-chain: create `.github/workflows/ingest.yml` to run the heavy
      Polygon backfill on a schedule into the same Neon database; keep the API on Vercel
      unchanged.

---

## 5. Local development

```bash
cp .env.example .env            # DATABASE_URL can point at a local Postgres or Neon
pip install -e ".[dev]"
python -m app.persistence.migrate
uvicorn app.main:app --reload   # http://localhost:8000/docs
```

The full test suite mocks all network and the repository, so `pytest` needs no database or
network. An optional persistence integration test runs only when `TEST_DATABASE_URL` is set.

---

## 6. Local Docker

Spin up a full local stack (Postgres + migrate + API) with a single command:

```bash
docker compose up --build
```

Services start in dependency order: `db` (with a healthcheck) → `migrate` (one-shot DDL) →
`api`. Once up, open <http://localhost:8000/ui> for the verification frontend, or
<http://localhost:8000/docs> for the OpenAPI explorer.

To rebuild after code changes:

```bash
docker compose up --build api
```

To reset the database volume:

```bash
docker compose down -v
docker compose up --build
```

The default `DATABASE_URL` inside Compose is
`postgresql://postgres:postgres@db:5432/predmarket`. The credentials are local-only
development defaults; change `POSTGRES_PASSWORD` in `docker-compose.yml` and the
corresponding `DATABASE_URL` for any non-throwaway environment.

---

## 7. Migrate to Supabase

Supabase provides managed Postgres and is a straightforward drop-in for the Neon/pgbouncer
pattern already used for Vercel.

1. **Provision** — create a new Supabase project; the database is ready instantly.

2. **Get the pooler connection string** — in the Supabase dashboard, go to
   **Project Settings → Database → Connection pooling**. Copy the **Transaction mode** URL
   (port `6543`). It looks like:
   `postgresql://postgres.[ref]:[password]@aws-[region].pooler.supabase.com:6543/postgres`

   > **Use port 6543, not 5432.** The transaction pooler (Supavisor / pgbouncer) does not
   > support prepared statements. asyncpg is already configured with
   > `statement_cache_size=0` to disable them — this is the fix for pgbouncer transaction mode.

3. **Set `DATABASE_URL`** — add the pooler string as the `DATABASE_URL` environment variable
   in your Vercel project (or `.env` for local runs against Supabase).

4. **Run the migration once** — from your local machine (with the prod DSN in your shell):

   ```bash
   DATABASE_URL="postgresql://postgres.[ref]:..." python -m app.persistence.migrate
   ```

5. **Deploy** — `vercel deploy --prod`. Confirm `GET /health` returns `{"database": true}`
   and `/ui` opens the verification frontend.
