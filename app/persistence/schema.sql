-- Schema for the prediction-market store. Applied by `python -m app.persistence.migrate`.
-- Never run as DDL on a request/ingestion hot path.
--
-- DESTRUCTIVE CLEAN-RECREATE: drops ALL tables (incl. the current Tier-2 ones)
-- and recreates them from scratch, so tightened column types / schema changes
-- always take effect on re-migrate (CREATE ... IF NOT EXISTS would otherwise
-- silently skip an existing table). Pre-launch there is no prod data to preserve.
-- Real forward/down migrations are the deferred Alembic tier (v2) — until then,
-- a re-migrate wipes the store.

DROP VIEW IF EXISTS market_movers;
DROP MATERIALIZED VIEW IF EXISTS market_latest;
DROP TABLE IF EXISTS market_observations, market_change_log,
                     market_snapshots, market_topics, ingestion_runs CASCADE;

-- ---------------------------------------------------------------------------
-- ingestion_runs — one row per daily batch run
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id        BIGSERIAL   PRIMARY KEY,
    snapshot_date DATE        NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT        NOT NULL DEFAULT 'running',
    topics        INT,
    rows_written  INT
);

-- ---------------------------------------------------------------------------
-- market_snapshots — append-only fact table (one row per date/venue/key/outcome)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_date    DATE          NOT NULL,
    venue            TEXT          NOT NULL,
    market_key       TEXT          NOT NULL,
    outcome          TEXT          NOT NULL,
    event_title      TEXT          NOT NULL,
    probability      NUMERIC(9,6)  NOT NULL,
    raw_price        NUMERIC(12,6) NOT NULL,
    volume_24h       NUMERIC(20,4),
    volume_total     NUMERIC(20,4),
    liquidity        NUMERIC(20,4),
    close_date       TIMESTAMPTZ,
    best_bid         NUMERIC(12,6),
    best_ask         NUMERIC(12,6),
    spread           NUMERIC(12,6),
    last_trade_price NUMERIC(12,6),
    open_interest    NUMERIC(20,4),
    confidence       TEXT          NOT NULL DEFAULT 'ok',
    observed_at      TIMESTAMPTZ   NOT NULL,
    ingested_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    run_id           BIGINT,
    PRIMARY KEY (snapshot_date, venue, market_key, outcome)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_series
    ON market_snapshots (venue, market_key, outcome, snapshot_date DESC);

CREATE INDEX IF NOT EXISTS idx_snapshots_date
    ON market_snapshots (snapshot_date);

-- ---------------------------------------------------------------------------
-- market_topics — M2M bridge: one row per (venue, market_key, topic) pairing
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_topics (
    venue         TEXT        NOT NULL,
    market_key    TEXT        NOT NULL,
    topic         TEXT        NOT NULL,
    category      TEXT,
    priority      TEXT        NOT NULL DEFAULT 'normal',
    tracked       BOOLEAN     NOT NULL DEFAULT FALSE,
    event_title   TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (venue, market_key, topic)
);

CREATE INDEX IF NOT EXISTS idx_topics_topic ON market_topics (topic);

-- ---------------------------------------------------------------------------
-- market_latest — materialized view: most-recent snapshot per series
-- A UNIQUE INDEX is required for REFRESH CONCURRENTLY.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS market_latest AS
SELECT DISTINCT ON (venue, market_key, outcome)
    venue, market_key, outcome, event_title,
    probability, raw_price, volume_24h, volume_total, liquidity,
    close_date, best_bid, best_ask, spread, last_trade_price, open_interest,
    confidence, snapshot_date, observed_at
FROM market_snapshots
ORDER BY venue, market_key, outcome, snapshot_date DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_latest_pk
    ON market_latest (venue, market_key, outcome);
