"""Shared utilities for source clients (Gamma, Kalshi, …).

Kept small — only helpers with identical implementations across two or more
source modules belong here.  Venue-specific parsing stays in its own module.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_iso_datetime(value: object) -> datetime | None:
    """Parse an ISO-8601 datetime string (possibly ending with 'Z') to UTC datetime.

    Returns None for absent, empty, or unparseable values — never fabricated.
    Used by both Gamma (``endDate``) and Kalshi (``close_time``).
    """
    if value is None or value == "":
        return None
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None
