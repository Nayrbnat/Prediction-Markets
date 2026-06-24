"""Vertical market modules.

Each market lives in its own ``app/markets/<name>/`` package (source = I/O, analysis =
pure math, divergence = relative value, register = registry descriptor) and composes
the shared seam in ``app/markets/_shared/``. Importing this package triggers each
market's ``register`` module, populating the derivative-market registry that the gateway
and digest iterate.
"""

from __future__ import annotations

from app.markets._shared.registry import registered_markets

# Side-effect imports: each register module calls registry.register(...) at import time.
from app.markets.fed_rates import register as _fed_rates_register  # noqa: F401

__all__ = ["registered_markets"]
