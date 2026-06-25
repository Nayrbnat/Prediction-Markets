"""Config tests for the sector watch-list merge into topics / high-priority."""

from __future__ import annotations

from app.config import Settings


def test_sector_topics_default_empty_no_effect() -> None:
    s = Settings(database_url="", ingest_topics="fed,btc", high_priority_topics="fed")
    assert s.sector_topic_list == []
    assert s.topics == ["fed", "btc"]
    assert s.high_priority == ["fed"]


def test_sector_topics_merge_into_ingest_and_high_priority() -> None:
    s = Settings(
        database_url="",
        ingest_topics="fed,btc",
        high_priority_topics="fed",
        sector_topics="best ai model, largest company",
    )
    # Discovered alongside ingest topics...
    assert s.topics == ["fed", "btc", "best ai model", "largest company"]
    # ...and always tracked (appear in high_priority -> tracked -> digest).
    assert s.high_priority == ["fed", "best ai model", "largest company"]


def test_sector_topics_dedup_against_existing() -> None:
    s = Settings(
        database_url="",
        ingest_topics="fed,btc",
        high_priority_topics="fed",
        sector_topics="btc, fed, palo alto",  # btc/fed already present
    )
    assert s.topics == ["fed", "btc", "palo alto"]
    # "btc" is a sector topic, so it becomes high-priority/tracked even though it
    # was not in HIGH_PRIORITY_TOPICS — sector topics are always tracked by design.
    assert s.high_priority == ["fed", "btc", "palo alto"]
