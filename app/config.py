"""The single typed source of all external configuration (pydantic-settings).

Nothing else in the app reads ``os.environ``. CSV/JSON env values are kept as raw
strings and exposed as parsed lists/dicts via properties, so env parsing stays
trivial and robust across pydantic-settings versions.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _dates(value: str) -> list[date]:
    """Parse a CSV of ISO dates into a list, skipping malformed tokens."""
    out: list[date] = []
    for token in _csv(value):
        try:
            out.append(date.fromisoformat(token))
        except ValueError:
            continue
    return out


def _targets(value: str) -> list[tuple[Decimal, str]]:
    """Parse a CSV of ``strike@expirytoken`` into (Decimal strike, token) pairs."""
    out: list[tuple[Decimal, str]] = []
    for token in _csv(value):
        strike_str, sep, expiry = token.partition("@")
        if not sep or not expiry.strip():
            continue
        try:
            out.append((Decimal(strike_str.strip()), expiry.strip()))
        except InvalidOperation:
            continue
    return out


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

    # ---- Crypto price thresholds (Deribit options, relative value) ------
    deribit_base_url: str = "https://www.deribit.com"
    crypto_gap_threshold: Decimal = Decimal("0.05")  # |market − options| (0..1) to flag
    btc_enabled: bool = False
    btc_topics: str = "btc price"  # CSV of topics the BTC source serves
    # CSV of "strike@expirytoken" Deribit targets, e.g. "120000@26JUN26,150000@26DEC26".
    btc_targets: str = ""
    eth_enabled: bool = False
    eth_topics: str = "eth price"
    eth_targets: str = ""

    # ---- ECB rate decision (€STR futures, relative value) --------------
    # ⚠️ NOT production-ready: ESR*.CME is CME's 3-month COMPOUNDED €STR future, not the
    # 1-month-average contract the rate-step math needs (verified 2026-06-24). Keep disabled
    # until a 1-month €STR feed is sourced (or 3-month strip math is built). See source.py.
    ecb_enabled: bool = False
    ecb_topics: str = "ecb rate decision"  # CSV of topics the ECB source serves
    ecb_meeting_horizon: int = 2  # number of upcoming ECB meetings to price
    ecb_meetings: str = ""  # CSV of ISO ECB Governing Council decision dates
    ecb_rates_base_url: str = "https://data-api.ecb.europa.eu"  # ECB SDMX (free) for €STR

    # ---- Equity index thresholds (CBOE options, relative value) --------
    cboe_base_url: str = "https://cdn.cboe.com"  # free public delayed-quotes options
    nasdaq_enabled: bool = False
    nasdaq_topics: str = "nasdaq price"  # CSV of topics the Nasdaq-100 source serves
    nasdaq_targets: str = ""  # optional "strike@YYMMDD" supplements (dynamic is default)

    # ---- Company-bet scan (discovery/listing only, separate 5-day cron) -
    company_scan_enabled: bool = False
    # Kalshi categories to enumerate for company bets (CSV); "Companies" holds the rich set.
    company_kalshi_categories: str = "Companies"
    # Polymarket company-name searches (CSV).
    company_names: str = "Nvidia,Tesla,Apple,Microsoft,Amazon,Google,Meta,OpenAI,SpaceX"
    company_scan_limit: int = 150  # max bets to list (safety cap)

    # ---- Sector watch-list (mosaic tiles -> daily digest movers) --------
    # CSV of Polymarket /public-search topics for the sectors followed bottom-up
    # (e.g. "best ai model,largest company,databricks ipo,palo alto,diageo").
    # Auto-merged into BOTH the ingest set (discovered daily) and the high-priority
    # set (tracked), so any move >= mover_threshold surfaces in the daily digest.
    # Empty by default — purely opt-in, no effect on existing behaviour until set.
    sector_topics: str = ""

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
    def sector_topic_list(self) -> list[str]:
        return _csv(self.sector_topics)

    @property
    def topics(self) -> list[str]:
        # Sector watch-list topics are discovered alongside the configured ingest topics.
        return list(dict.fromkeys(_csv(self.ingest_topics) + self.sector_topic_list))

    @property
    def high_priority(self) -> list[str]:
        # Sector watch-list topics are always tracked so their moves reach the digest.
        return list(dict.fromkeys(_csv(self.high_priority_topics) + self.sector_topic_list))

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
        return _dates(self.fomc_meetings)

    @property
    def ecb_topic_set(self) -> set[str]:
        return set(_csv(self.ecb_topics))

    @property
    def ecb_meeting_dates(self) -> list[date]:
        """Parsed ECB Governing Council decision dates (skips malformed tokens)."""
        return _dates(self.ecb_meetings)

    @property
    def company_kalshi_category_list(self) -> list[str]:
        return _csv(self.company_kalshi_categories)

    @property
    def company_name_list(self) -> list[str]:
        return _csv(self.company_names)

    @property
    def btc_topic_set(self) -> set[str]:
        return set(_csv(self.btc_topics))

    @property
    def eth_topic_set(self) -> set[str]:
        return set(_csv(self.eth_topics))

    @property
    def btc_target_list(self) -> list[tuple[Decimal, str]]:
        """Parsed BTC (strike, expiry-token) Deribit targets (skips malformed tokens)."""
        return _targets(self.btc_targets)

    @property
    def eth_target_list(self) -> list[tuple[Decimal, str]]:
        """Parsed ETH (strike, expiry-token) Deribit targets (skips malformed tokens)."""
        return _targets(self.eth_targets)

    @property
    def nasdaq_topic_set(self) -> set[str]:
        return set(_csv(self.nasdaq_topics))

    @property
    def nasdaq_target_list(self) -> list[tuple[Decimal, str]]:
        """Parsed Nasdaq-100 (strike, CBOE YYMMDD token) targets (skips malformed)."""
        return _targets(self.nasdaq_targets)


@lru_cache
def get_settings() -> Settings:
    """Load settings once and reuse. Inject this everywhere; never read env directly."""
    return Settings()
