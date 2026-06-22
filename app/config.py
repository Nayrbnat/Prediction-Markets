"""The single typed source of all external configuration (pydantic-settings).

Nothing else in the app reads ``os.environ``. CSV/JSON env values are kept as raw
strings and exposed as parsed lists/dicts via properties, so env parsing stays
trivial and robust across pydantic-settings versions.
"""

from __future__ import annotations

import json
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


@lru_cache
def get_settings() -> Settings:
    """Load settings once and reuse. Inject this everywhere; never read env directly."""
    return Settings()
