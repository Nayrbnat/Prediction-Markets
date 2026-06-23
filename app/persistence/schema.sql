-- Schema for the prediction-market store. Applied by `python -m app.persistence.migrate`.
-- Never run as DDL on a request/ingestion hot path.

CREATE TABLE IF NOT EXISTS market_observations (
    venue                 TEXT        NOT NULL,
    market_key            TEXT        NOT NULL,
    outcome               TEXT        NOT NULL,
    event_title           TEXT        NOT NULL,
    topic                 TEXT,
    category              TEXT,
    probability           NUMERIC     NOT NULL,
    previous_probability  NUMERIC,
    probability_delta     NUMERIC,
    raw_price             NUMERIC     NOT NULL,
    volume                NUMERIC,
    liquidity             NUMERIC,
    confidence            TEXT        NOT NULL DEFAULT 'ok',
    priority              TEXT        NOT NULL DEFAULT 'normal',
    tracked               BOOLEAN     NOT NULL DEFAULT FALSE,
    first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_changed_at       TIMESTAMPTZ,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (venue, market_key, outcome)
);

CREATE INDEX IF NOT EXISTS idx_obs_topic      ON market_observations (topic);
CREATE INDEX IF NOT EXISTS idx_obs_tracked    ON market_observations (tracked) WHERE tracked;
CREATE INDEX IF NOT EXISTS idx_obs_last_seen  ON market_observations (last_seen_at);
CREATE INDEX IF NOT EXISTS idx_obs_event_title ON market_observations (event_title);

-- Partial expression index for the top-movers query (ORDER BY abs(probability_delta) DESC WHERE tracked).
CREATE INDEX IF NOT EXISTS idx_obs_tracked_delta
    ON market_observations (abs(probability_delta) DESC)
    WHERE tracked;

CREATE TABLE IF NOT EXISTS market_change_log (
    id                    BIGSERIAL   PRIMARY KEY,
    venue                 TEXT        NOT NULL,
    market_key            TEXT        NOT NULL,
    outcome               TEXT        NOT NULL,
    observed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    probability           NUMERIC     NOT NULL,
    previous_probability  NUMERIC,
    delta                 NUMERIC,
    raw_price             NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_changelog_market
    ON market_change_log (venue, market_key, outcome, observed_at DESC);
