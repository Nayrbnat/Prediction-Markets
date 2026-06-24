"""Fed-specific view of the shared rate-step math.

The FedWatch calculation is the generic ``app/markets/_shared/rate_step.py``; this
module re-exports it under Fed-domain names so the source layer and tests read in
Fed terms. No Fed-specific math lives here — the only thing special about the Fed is
the ZQ contract + EFFR, handled in ``source.py``.
"""

from __future__ import annotations

from app.markets._shared.rate_step import RateStepResult as FedFundsResult
from app.markets._shared.rate_step import distribution_from_delta, implied_average_rate
from app.markets._shared.rate_step import rate_step_distribution as fed_funds_distribution

__all__ = [
    "FedFundsResult",
    "distribution_from_delta",
    "fed_funds_distribution",
    "implied_average_rate",
]
