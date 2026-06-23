"""Pure rendering: MarketDigest → (subject, html, text).

No I/O. All formatting helpers are module-level functions.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.digest import DivergenceItem, MarketDigest, MoverItem, TrackedMarket

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _pct(prob: Decimal) -> str:
    """Format a 0..1 probability as a percentage string, e.g. 0.7354 → '73.5%'."""
    return f"{float(prob) * 100:.1f}%"


def _signed_pp(delta: Decimal) -> str:
    """Format a probability delta as signed pp, e.g. 0.13 → '+13.0pp', -0.07 → '-7.0pp'."""
    pp = float(delta) * 100
    sign = "+" if pp >= 0 else ""
    return f"{sign}{pp:.1f}pp"


def _mover_colour(delta: Decimal) -> str:
    return "#2e7d32" if delta >= 0 else "#c62828"  # green / red


# ---------------------------------------------------------------------------
# HTML sections
# ---------------------------------------------------------------------------

def _render_mover_html(m: MoverItem) -> str:
    colour = _mover_colour(m.delta)
    close = f" · closes {m.close_date.strftime('%Y-%m-%d')}" if m.close_date else ""
    return (
        f'<tr>'
        f'<td style="padding:4px 8px;font-weight:bold">{m.event_title}</td>'
        f'<td style="padding:4px 8px">{m.outcome}</td>'
        f'<td style="padding:4px 8px">{_pct(m.previous)} → {_pct(m.current)}</td>'
        f'<td style="padding:4px 8px;color:{colour};font-weight:bold">'
        f'{_signed_pp(m.delta)}</td>'
        f'<td style="padding:4px 8px;color:#888;font-size:0.9em">'
        f'{m.venue}{close}</td>'
        f'</tr>'
    )


def _render_divergence_html(d: DivergenceItem) -> str:
    colour = _mover_colour(d.gap)
    return (
        f'<tr>'
        f'<td style="padding:4px 8px;font-weight:bold">{d.meeting}</td>'
        f'<td style="padding:4px 8px">{d.outcome}</td>'
        f'<td style="padding:4px 8px">{_pct(d.market_prob)}</td>'
        f'<td style="padding:4px 8px">{_pct(d.futures_prob)}</td>'
        f'<td style="padding:4px 8px;color:{colour};font-weight:bold">'
        f'{_signed_pp(d.gap)}</td>'
        f'<td style="padding:4px 8px;color:#888;font-size:0.9em">{d.market_venue}</td>'
        f'</tr>'
    )


def _render_tracked_html(tm: TrackedMarket) -> str:
    outcomes_html = "".join(
        f'<span style="margin-right:12px">'
        f'<strong>{o.outcome}</strong> {_pct(o.probability)}'
        f'</span>'
        for o in tm.outcomes
    )
    return (
        f'<tr>'
        f'<td style="padding:4px 8px;font-weight:bold">{tm.event_title}</td>'
        f'<td style="padding:4px 8px;color:#555;font-size:0.85em">{tm.venue}</td>'
        f'<td style="padding:4px 8px">{outcomes_html}</td>'
        f'</tr>'
    )


def _render_html(digest: MarketDigest, subject: str) -> str:
    style = (
        "font-family:sans-serif;max-width:800px;margin:0 auto;"
        "color:#222;background:#fff;padding:24px"
    )
    table_style = (
        "border-collapse:collapse;width:100%;"
        "border:1px solid #e0e0e0;border-radius:4px"
    )
    th_style = (
        "padding:6px 8px;background:#f5f5f5;border-bottom:2px solid #ddd;"
        "text-align:left;font-size:0.85em;color:#555"
    )

    # Movers section
    if digest.movers:
        mover_rows = "\n".join(_render_mover_html(m) for m in digest.movers)
        movers_section = f"""
<h2 style="color:#333;border-bottom:1px solid #ddd;padding-bottom:6px">
  Sharp movers ({digest.mover_count})
  <span style="font-size:0.75em;color:#888;font-weight:normal">
    — threshold {_signed_pp(digest.mover_threshold)} day-over-day, tracked markets only
  </span>
</h2>
<table style="{table_style}">
  <thead>
    <tr>
      <th style="{th_style}">Market</th>
      <th style="{th_style}">Outcome</th>
      <th style="{th_style}">Previous → Current</th>
      <th style="{th_style}">Move</th>
      <th style="{th_style}">Venue</th>
    </tr>
  </thead>
  <tbody>
{mover_rows}
  </tbody>
</table>
"""
    else:
        movers_section = (
            f'<h2 style="color:#333;border-bottom:1px solid #ddd;padding-bottom:6px">'
            f'Sharp movers (0)</h2>'
            f'<p style="color:#888">No tracked outcomes moved ≥ '
            f'{_pct(digest.mover_threshold)} today.</p>'
        )

    # Relative-value section (market vs Fed-funds-futures-implied) — material gaps only.
    material_divs = [d for d in digest.divergences if d.material]
    if material_divs:
        div_rows = "\n".join(_render_divergence_html(d) for d in material_divs)
        divergence_section = f"""
<h2 style="color:#333;border-bottom:1px solid #ddd;padding-bottom:6px;margin-top:32px">
  Relative value vs Fed funds futures ({digest.divergence_count})
  <span style="font-size:0.75em;color:#888;font-weight:normal">
    — prediction market vs ZQ-implied; a signal to investigate, not arbitrage
  </span>
</h2>
<table style="{table_style}">
  <thead>
    <tr>
      <th style="{th_style}">Meeting</th>
      <th style="{th_style}">Outcome</th>
      <th style="{th_style}">Market</th>
      <th style="{th_style}">Futures</th>
      <th style="{th_style}">Gap (mkt−fut)</th>
      <th style="{th_style}">Venue</th>
    </tr>
  </thead>
  <tbody>
{div_rows}
  </tbody>
</table>
"""
    else:
        divergence_section = ""

    # Tracked markets section
    if digest.tracked:
        tracked_rows = "\n".join(_render_tracked_html(tm) for tm in digest.tracked)
        tracked_section = f"""
<h2 style="color:#333;border-bottom:1px solid #ddd;padding-bottom:6px;margin-top:32px">
  Tracked markets ({digest.tracked_count})
</h2>
<table style="{table_style}">
  <thead>
    <tr>
      <th style="{th_style}">Market</th>
      <th style="{th_style}">Venue</th>
      <th style="{th_style}">Probabilities</th>
    </tr>
  </thead>
  <tbody>
{tracked_rows}
  </tbody>
</table>
"""
    else:
        tracked_section = (
            '<h2 style="color:#333;border-bottom:1px solid #ddd;padding-bottom:6px;'
            'margin-top:32px">Tracked markets (0)</h2>'
            '<p style="color:#888">No tracked markets found.</p>'
        )

    footer = (
        f'<p style="margin-top:32px;font-size:0.8em;color:#aaa">'
        f'Generated for {digest.generated_for} · '
        f'Decision-support data only. Not financial advice.</p>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>{subject}</title></head>
<body>
<div style="{style}">
  <h1 style="color:#1a237e;margin-bottom:4px">{subject}</h1>
  <p style="color:#888;font-size:0.9em;margin-top:0">
    Daily prediction-market digest · {digest.generated_for}
  </p>
{movers_section}
{divergence_section}
{tracked_section}
{footer}
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Plain-text mirror
# ---------------------------------------------------------------------------

def _render_text(digest: MarketDigest, subject: str) -> str:
    lines: list[str] = [
        subject,
        "=" * len(subject),
        f"Generated for: {digest.generated_for}",
        "",
    ]

    # Sharp movers
    lines.append(
        f"SHARP MOVERS ({digest.mover_count}) "
        f"— threshold {_pct(digest.mover_threshold)} day-over-day, tracked markets only"
    )
    lines.append("-" * 60)
    if digest.movers:
        for m in digest.movers:
            close = f" (closes {m.close_date.strftime('%Y-%m-%d')})" if m.close_date else ""
            lines.append(
                f"  [{m.venue}] {m.event_title} — {m.outcome}: "
                f"{_pct(m.previous)} → {_pct(m.current)} ({_signed_pp(m.delta)}){close}"
            )
    else:
        lines.append(f"  No tracked outcomes moved ≥ {_pct(digest.mover_threshold)} today.")
    lines.append("")

    # Relative value vs Fed funds futures (material gaps only)
    material_divs = [d for d in digest.divergences if d.material]
    if material_divs:
        lines.append(
            f"RELATIVE VALUE vs FED FUNDS FUTURES ({digest.divergence_count}) "
            "— prediction market vs ZQ-implied; a signal, not arbitrage"
        )
        lines.append("-" * 60)
        for d in material_divs:
            lines.append(
                f"  {d.meeting} — {d.outcome}: market {_pct(d.market_prob)} vs "
                f"futures {_pct(d.futures_prob)} ({_signed_pp(d.gap)}) [{d.market_venue}]"
            )
        lines.append("")

    # Tracked markets
    lines.append(f"TRACKED MARKETS ({digest.tracked_count})")
    lines.append("-" * 60)
    if digest.tracked:
        for tm in digest.tracked:
            outcome_str = "  ".join(
                f"{o.outcome}: {_pct(o.probability)}" for o in tm.outcomes
            )
            lines.append(f"  [{tm.venue}] {tm.event_title}")
            lines.append(f"    {outcome_str}")
    else:
        lines.append("  No tracked markets found.")
    lines.append("")
    lines.append("Decision-support data only. Not financial advice.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_digest(digest: MarketDigest) -> tuple[str, str, str]:
    """Render a MarketDigest to (subject, html, text). PURE — no I/O."""
    subject = (
        f"Prediction markets — {digest.mover_count} sharp move(s)"
        f" — {digest.generated_for}"
    )
    html = _render_html(digest, subject)
    text = _render_text(digest, subject)
    return subject, html, text
