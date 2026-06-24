"""Shared infrastructure for vertical market modules.

Holds the seam every market plugs into: the derivative-market registry/protocol,
the reusable rate-step (FedWatch-style) math, the rate-market relative-value
comparator, and the options-density (Breeden-Litzenberger) helper. Each concrete
market under ``app/markets/<name>/`` composes these — it does not reimplement them.
"""
