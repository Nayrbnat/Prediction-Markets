"""Company-bet scan: discover specific-company prediction markets and list them.

Discovery/listing only (no relative-value, no DB). Two sources:
  - Kalshi: enumerate the configured categories' series (the "Companies" category holds the
    rich single-company set — CEO/M&A/product/KPI bets), one ``/series`` call per category.
  - Polymarket: search each configured company name via Gamma.

Classifies each bet coarsely (stock-price vs KPI/corporate-event), dedupes, caps, and
renders a plain listing. Run on its own cron so the user can see what company bets exist.
"""

from __future__ import annotations

from datetime import date

import httpx

from app.config import Settings
from app.core.errors import RateLimitError, SchemaDriftError, SourceError
from app.core.http import fetch_json
from app.core.logging import get_logger
from app.core.rate_limit import AsyncRateLimiter
from app.models.company import BetKind, CompanyBet, CompanyScan
from app.sources import polymarket_gamma

logger = get_logger(__name__)
_limiter = AsyncRateLimiter(rate_per_sec=8.0)

# A bet is classified "price" if its title hints at a stock-price / valuation level;
# otherwise it's a KPI or corporate-event bet (the bulk of Kalshi's company series).
_PRICE_HINTS = ("$", "market cap", "valuation", "most valuable", "share price", "stock price")


def _classify(title: str) -> BetKind:
    low = title.lower()
    return "price" if any(h in low for h in _PRICE_HINTS) else "kpi-or-event"


async def _kalshi_company_series(client: httpx.AsyncClient, category: str) -> list[CompanyBet]:
    payload = await fetch_json(
        client, "/series", venue="kalshi", limiter=_limiter, params={"category": category}
    )
    out: list[CompanyBet] = []
    series = payload.get("series") if isinstance(payload, dict) else None
    for s in series or []:
        if not isinstance(s, dict):
            continue
        ticker, title = s.get("ticker"), s.get("title")
        if ticker and title:
            out.append(
                CompanyBet(
                    venue="kalshi", source_key=str(ticker),
                    title=str(title), kind=_classify(str(title)),
                )
            )
    return out


async def _polymarket_company(client: httpx.AsyncClient, name: str) -> list[CompanyBet]:
    refs = await polymarket_gamma.discover(client, name, limit=20)
    out: list[CompanyBet] = []
    for r in refs:
        out.append(
            CompanyBet(
                venue="polymarket", source_key=r.market_key,
                title=r.event_title, kind=_classify(r.event_title), close_date=r.close_date,
            )
        )
    return out


async def scan(
    gamma_client: httpx.AsyncClient, kalshi_client: httpx.AsyncClient, *,
    settings: Settings, generated_for: date,
) -> CompanyScan:
    """Discover company bets on Kalshi + Polymarket and return a deduped, capped listing."""
    bets: list[CompanyBet] = []

    for category in settings.company_kalshi_category_list:
        try:
            bets.extend(await _kalshi_company_series(kalshi_client, category))
        except (SourceError, RateLimitError, SchemaDriftError) as exc:
            logger.warning(
                "company_scan.kalshi_failed", extra={"category": category, "error": str(exc)}
            )

    for name in settings.company_name_list:
        try:
            bets.extend(await _polymarket_company(gamma_client, name))
        except (SourceError, RateLimitError, SchemaDriftError) as exc:
            logger.warning(
                "company_scan.polymarket_failed", extra={"name": name, "error": str(exc)}
            )

    # Dedupe by (venue, normalised title) to collapse duplicate series (e.g. APPLEUS +
    # KXAPPLEUS both "Apple DOJ lawsuit").
    uniq: dict[tuple[str, str], CompanyBet] = {}
    for b in bets:
        uniq.setdefault((b.venue, " ".join(b.title.lower().split())), b)

    # Cap PER VENUE so a large Kalshi category can't starve the Polymarket listing.
    limit = settings.company_scan_limit
    by_title = sorted(uniq.values(), key=lambda b: b.title.lower())
    kalshi = [b for b in by_title if b.venue == "kalshi"]
    pm = [b for b in by_title if b.venue == "polymarket"]
    truncated = len(kalshi) > limit or len(pm) > limit
    deduped = kalshi[:limit] + pm[:limit]
    kalshi_n = min(len(kalshi), limit)
    pm_n = min(len(pm), limit)

    logger.info(
        "company_scan.done",
        extra={"count": len(deduped), "kalshi": kalshi_n,
               "polymarket": pm_n, "truncated": truncated},
    )
    return CompanyScan(
        generated_for=generated_for, bets=deduped, count=len(deduped),
        kalshi_count=kalshi_n, polymarket_count=pm_n, truncated=truncated,
    )


def render_company_scan(result: CompanyScan) -> tuple[str, str, str]:
    """Render a CompanyScan to (subject, html, text). PURE — no I/O."""
    subject = f"Company bets — {result.count} available — {result.generated_for}"

    def _rows_text(venue: str) -> list[str]:
        items = [b for b in result.bets if b.venue == venue]
        lines = [f"  {venue.upper()} ({len(items)})", "  " + "-" * 50]
        for b in items:
            tag = "[$]" if b.kind == "price" else "[ ]"
            lines.append(f"  {tag} {b.title}  ({b.source_key})")
        return lines

    text_lines = [subject, "=" * len(subject), ""]
    for venue in ("kalshi", "polymarket"):
        text_lines += _rows_text(venue) + [""]
    if result.truncated:
        text_lines.append("(list truncated at the configured cap)")
    text_lines.append("Discovery only — these are available company bets, not signals.")
    text = "\n".join(text_lines)

    def _rows_html(venue: str) -> str:
        items = [b for b in result.bets if b.venue == venue]
        rows = "".join(
            f'<tr><td style="padding:3px 8px;color:#888">'
            f'{"$" if b.kind == "price" else ""}</td>'
            f'<td style="padding:3px 8px">{b.title}</td>'
            f'<td style="padding:3px 8px;color:#aaa;font-size:0.85em">{b.source_key}</td></tr>'
            for b in items
        )
        return (
            f'<h2 style="color:#333">{venue.title()} ({len(items)})</h2>'
            f'<table style="border-collapse:collapse;width:100%">{rows}</table>'
        )

    html = (
        f'<div style="font-family:sans-serif;max-width:800px;margin:0 auto;color:#222">'
        f'<h1 style="color:#1a237e">{subject}</h1>'
        f'{_rows_html("kalshi")}{_rows_html("polymarket")}'
        f'<p style="color:#aaa;font-size:0.8em;margin-top:24px">'
        f'Discovery only — available company bets, not signals.</p></div>'
    )
    return subject, html, text
