"""ECB rates market: euro-area ECB Governing Council decision probabilities implied by
1-month €STR futures, compared against Polymarket/Kalshi. Persisted under the ``estr`` venue.

The rate-step math is the shared ``app/markets/_shared/rate_step`` (already tested by the Fed
variant), so there is no ``analysis.py`` here — only Fed-mirrored I/O and divergence binding.
"""
