"""Discovery + per-venue availability. Pairs the cross-venue view the caller sees."""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.domain import MarketRef
from app.models.provenance import Venue
from app.models.responses import VenueAvailability
from app.services.gateway import Gateway

logger = get_logger(__name__)

_SIGNALS: dict[Venue, list[str]] = {
    "polymarket": ["price", "volume", "depth"],  # smart-money tilt is v2
    "kalshi": ["price", "volume", "depth"],
}


def _availability(refs: list[MarketRef], venues: list[Venue]) -> list[VenueAvailability]:
    matched = {ref.venue for ref in refs}
    out: list[VenueAvailability] = []
    for venue in venues:
        is_matched = venue in matched
        out.append(
            VenueAvailability(
                venue=venue,
                matched=is_matched,
                signals=_SIGNALS[venue] if is_matched else [],
                note=None if is_matched else f"no {venue} market matched this topic",
            )
        )
    return out


async def discover(
    gateway: Gateway,
    topic: str,
    *,
    venues: list[Venue] | None,
    limit: int,
) -> tuple[list[MarketRef], list[VenueAvailability]]:
    requested: list[Venue] = venues or ["polymarket", "kalshi"]
    refs = await gateway.discover(topic, venues=requested, limit=limit)
    availability = _availability(refs, requested)
    logger.info("discovery.done", extra={"topic": topic, "markets": len(refs)})
    return refs, availability
