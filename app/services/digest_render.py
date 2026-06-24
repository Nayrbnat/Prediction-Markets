"""Pure rendering: MarketDigest → (subject, html, text).

No I/O. All formatting helpers are module-level functions.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.digest import (
    DivergenceItem,
    MarketDigest,
    MeetingMatrix,
    MoverItem,
    ThresholdDivergence,
    TrackedMarket,
)

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


def _render_threshold_html(t: ThresholdDivergence) -> str:
    colour = _mover_colour(t.gap)
    return (
        f'<tr>'
        f'<td style="padding:4px 8px;font-weight:bold">{t.underlying} ≥ ${t.strike:,.0f}</td>'
        f'<td style="padding:4px 8px">{t.expiry}</td>'
        f'<td style="padding:4px 8px">{_pct(t.market_prob)}</td>'
        f'<td style="padding:4px 8px">{_pct(t.derivative_prob)}</td>'
        f'<td style="padding:4px 8px;color:{colour};font-weight:bold">'
        f'{_signed_pp(t.gap)}</td>'
        f'<td style="padding:4px 8px;color:#888;font-size:0.9em">{t.market_venue}</td>'
        f'</tr>'
    )


def _prob_cell(v: Decimal, hi: Decimal) -> str:
    """A cut/hold/raise table cell; bold when it's the row's highest probability."""
    bold = "font-weight:bold;" if v == hi else ""
    return f'<td style="padding:4px 8px;{bold}">{_pct(v)}</td>'


def _render_matrix_rows_html(matrices: list[MeetingMatrix]) -> str:
    rows: list[str] = []
    for matrix in matrices:
        for i, r in enumerate(matrix.rows):
            top = "border-top:2px solid #ccc;" if i == 0 else ""
            hi = max(r.cut, r.hold, r.raise_)
            meeting_cell = matrix.meeting if i == 0 else ""
            rows.append(
                f'<tr style="{top}">'
                f'<td style="padding:4px 8px;font-weight:bold">{meeting_cell}</td>'
                f'<td style="padding:4px 8px;color:#555">{r.source}</td>'
                f'{_prob_cell(r.cut, hi)}{_prob_cell(r.hold, hi)}{_prob_cell(r.raise_, hi)}'
                f'</tr>'
            )
    return "\n".join(rows)


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

    # Cut / Hold / Raise matrix per meeting (Polymarket vs Kalshi vs Futures side-by-side).
    if digest.meeting_matrices:
        matrix_rows = _render_matrix_rows_html(digest.meeting_matrices)
        matrix_section = f"""
<h2 style="color:#333;border-bottom:1px solid #ddd;padding-bottom:6px;margin-top:32px">
  Rate-decision probabilities by source
  <span style="font-size:0.75em;color:#888;font-weight:normal">
    — Cut / Hold / Raise per central-bank meeting; futures = rate-futures-implied
  </span>
</h2>
<table style="{table_style}">
  <thead>
    <tr>
      <th style="{th_style}">Meeting</th>
      <th style="{th_style}">Source</th>
      <th style="{th_style}">Cut</th>
      <th style="{th_style}">Hold</th>
      <th style="{th_style}">Raise</th>
    </tr>
  </thead>
  <tbody>
{matrix_rows}
  </tbody>
</table>
"""
    else:
        matrix_section = ""

    # Relative-value section (market vs Fed-funds-futures-implied) — material gaps only.
    material_divs = [d for d in digest.divergences if d.material]
    if material_divs:
        div_rows = "\n".join(_render_divergence_html(d) for d in material_divs)
        divergence_section = f"""
<h2 style="color:#333;border-bottom:1px solid #ddd;padding-bottom:6px;margin-top:32px">
  Relative value vs rate futures ({digest.divergence_count})
  <span style="font-size:0.75em;color:#888;font-weight:normal">
    — prediction market vs rate-futures-implied; a signal to investigate, not arbitrage
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

    # Threshold relative value (prediction market vs options-implied P(above)).
    material_thresholds = [t for t in digest.threshold_divergences if t.material]
    if material_thresholds:
        thr_rows = "\n".join(_render_threshold_html(t) for t in material_thresholds)
        threshold_section = f"""
<h2 style="color:#333;border-bottom:1px solid #ddd;padding-bottom:6px;margin-top:32px">
  Relative value vs options-implied ({digest.threshold_divergence_count})
  <span style="font-size:0.75em;color:#888;font-weight:normal">
    — prediction market vs risk-neutral P(above); a signal, not arbitrage
  </span>
</h2>
<table style="{table_style}">
  <thead>
    <tr>
      <th style="{th_style}">Threshold</th>
      <th style="{th_style}">Expiry</th>
      <th style="{th_style}">Market</th>
      <th style="{th_style}">Options</th>
      <th style="{th_style}">Gap (mkt−opt)</th>
      <th style="{th_style}">Venue</th>
    </tr>
  </thead>
  <tbody>
{thr_rows}
  </tbody>
</table>
"""
    else:
        threshold_section = ""

    # Other tracked markets (those that don't fit the Fed cut/hold/raise schema).
    if digest.tracked:
        tracked_rows = "\n".join(_render_tracked_html(tm) for tm in digest.tracked)
        tracked_section = f"""
<h2 style="color:#333;border-bottom:1px solid #ddd;padding-bottom:6px;margin-top:32px">
  Other tracked markets ({digest.tracked_count})
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
        tracked_section = ""

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
{matrix_section}
{divergence_section}
{threshold_section}
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

    # Rate-decision probabilities by source (Cut / Hold / Raise)
    if digest.meeting_matrices:
        lines.append("RATE-DECISION PROBABILITIES BY SOURCE (Cut / Hold / Raise)")
        lines.append("-" * 60)
        lines.append(f"  {'Meeting':<9} {'Source':<11} {'Cut':>6} {'Hold':>6} {'Raise':>6}")
        for matrix in digest.meeting_matrices:
            for r in matrix.rows:
                lines.append(
                    f"  {matrix.meeting:<9} {r.source:<11} "
                    f"{_pct(r.cut):>6} {_pct(r.hold):>6} {_pct(r.raise_):>6}"
                )
            lines.append("")  # blank line between meetings

    # Relative value vs Fed funds futures (material gaps only)
    material_divs = [d for d in digest.divergences if d.material]
    if material_divs:
        lines.append(
            f"RELATIVE VALUE vs RATE FUTURES ({digest.divergence_count}) "
            "— prediction market vs rate-futures-implied; a signal, not arbitrage"
        )
        lines.append("-" * 60)
        for d in material_divs:
            lines.append(
                f"  {d.meeting} — {d.outcome}: market {_pct(d.market_prob)} vs "
                f"futures {_pct(d.futures_prob)} ({_signed_pp(d.gap)}) [{d.market_venue}]"
            )
        lines.append("")

    # Threshold relative value (market vs options-implied, material gaps only)
    material_thresholds = [t for t in digest.threshold_divergences if t.material]
    if material_thresholds:
        lines.append(
            f"RELATIVE VALUE vs OPTIONS-IMPLIED ({digest.threshold_divergence_count}) "
            "— prediction market vs risk-neutral P(above); a signal, not arbitrage"
        )
        lines.append("-" * 60)
        for t in material_thresholds:
            lines.append(
                f"  {t.underlying} ≥ ${t.strike:,.0f} ({t.expiry}): "
                f"market {_pct(t.market_prob)} vs options {_pct(t.derivative_prob)} "
                f"({_signed_pp(t.gap)}) [{t.market_venue}]"
            )
        lines.append("")

    # Other tracked markets (non cut/hold/raise)
    if digest.tracked:
        lines.append(f"OTHER TRACKED MARKETS ({digest.tracked_count})")
        lines.append("-" * 60)
        for tm in digest.tracked:
            outcome_str = "  ".join(
                f"{o.outcome}: {_pct(o.probability)}" for o in tm.outcomes
            )
            lines.append(f"  [{tm.venue}] {tm.event_title}")
            lines.append(f"    {outcome_str}")
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
