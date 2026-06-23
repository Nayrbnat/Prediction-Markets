"""The single typed source of all external configuration (pydantic-settings).

Nothing else in the app reads ``os.environ``. CSV/JSON env values are kept as raw
strings and exposed as parsed lists/dicts via properties, so env parsing stays
trivial and robust across pydantic-settings versions.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Database -------------------------------------------------------
    database_url: str = ""

    # ---- What to ingest (CSV / JSON kept as strings, parsed via props) --
    ingest_topics: str = ""
    high_priority_topics: str = ""
    category_map: str = "{}"
    kalshi_series_map: str = "{}"
    kalshi_category_map: str = "{}"
    per_topic_limit: int = 50

    # ---- Behaviour thresholds ------------------------------------------
    retention_days: int = 15
    material_change: Decimal = Decimal("0.01")
    thin_volume: Decimal = Decimal("1000")
    thin_spread: Decimal = Decimal("0.05")
    live_ttl_seconds: int = 900

    # ---- Venue endpoints ------------------------------------------------
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"
    kalshi_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"

    # ---- Cron + logging -------------------------------------------------
    cron_secret: str = "change-me"
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "console"

    # ---- Daily digest ---------------------------------------------------
    mover_threshold: Decimal = Decimal("0.10")
    digest_enabled: bool = False
    digest_from: str = ""
    digest_to: str = ""  # comma-separated recipient list

    # ---- CME Fed Funds futures (relative-value vs prediction markets) ---
    cme_enabled: bool = False
    cme_topics: str = "fed rate decision"  # CSV of topics the CME source serves
    cme_meeting_horizon: int = 2  # number of upcoming FOMC meetings to price
    fomc_meetings: str = ""  # CSV of ISO FOMC announcement dates (e.g. 2026-07-29)
    rv_gap_threshold: Decimal = Decimal("0.05")  # |market − futures| pp to flag a divergence
    yahoo_chart_base_url: str = "https://query1.finance.yahoo.com"
    nyfed_rates_base_url: str = "https://markets.newyorkfed.org"

    # ---- SMTP (leave blank to use ConsoleEmailSender) -------------------
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""

    # ---- v2 only (unused in v1) ----------------------------------------
    polygon_rpc_url: str | None = None
    ctf_exchange_address: str | None = None

    # ---- Parsed views ---------------------------------------------------
    @property
    def topics(self) -> list[str]:
        return _csv(self.ingest_topics)

    @property
    def high_priority(self) -> list[str]:
        return _csv(self.high_priority_topics)

    @property
    def categories(self) -> dict[str, str]:
        return json.loads(self.category_map or "{}")

    @property
    def kalshi_series(self) -> dict[str, object]:
        return json.loads(self.kalshi_series_map or "{}")

    @property
    def kalshi_categories(self) -> dict[str, str]:
        return json.loads(self.kalshi_category_map or "{}")

    @property
    def digest_recipients(self) -> list[str]:
        """Parsed list of digest email recipients from the CSV ``digest_to`` field."""
        return _csv(self.digest_to)

    @property
    def cme_topic_set(self) -> set[str]:
        """Topics the CME Fed Funds source should serve."""
        return set(_csv(self.cme_topics))

    @property
    def fomc_meeting_dates(self) -> list[date]:
        """Parsed FOMC announcement dates (skips malformed tokens)."""
        out: list[date] = []
        for token in _csv(self.fomc_meetings):
            try:
                out.append(date.fromisoformat(token))
            except ValueError:
                continue
        return out


@lru_cache
def get_settings() -> Settings:
    """Load settings once and reuse. Inject this everywhere; never read env directly."""
    return Settings()
