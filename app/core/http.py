"""Factory for the shared async httpx client. Source clients receive a client
rather than constructing their own, so timeouts/headers/pooling are consistent.
"""

from __future__ import annotations

import httpx

from app import __version__

_USER_AGENT = f"prediction-market-api/{__version__}"


def make_client(base_url: str = "", timeout: float = 15.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout),
        headers={"user-agent": _USER_AGENT, "accept": "application/json"},
    )
