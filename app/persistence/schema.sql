-- Schema for the prediction-market store. Applied by `python -m app.persistence.migrate`.
-- Never run as DDL on a request/ingestion hot path.
-- Pre-launch: clean-slate replacement of v1 tables (no data migration needed).

DROP VIEW IF EXISTS market_movers;
DROP MATERIALIZED VIEW IF EXISTS market_latest;
DROP TABLE IF EXISTS market_observations, market_change_log CASCADE;

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
    snapshot_date DATE        NOT NULL,
    venue         TEXT        NOT NULL,
    market_key    TEXT        NOT NULL,
    outcome       TEXT        NOT NULL,
    event_title   TEXT        NOT NULL,
    probability   NUMERIC     NOT NULL,
    raw_price     NUMERIC     NOT NULL,
    volume        NUMERIC,
    liquidity     NUMERIC,
    confidence    TEXT        NOT NULL DEFAULT 'ok',
    observed_at   TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    run_id        BIGINT,
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
    probability, raw_price, volume, liquidity, confidence,
    snapshot_date, observed_at
FROM market_snapshots
ORDER BY venue, market_key, outcome, snapshot_date DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_latest_pk
    ON market_latest (venue, market_key, outcome);
