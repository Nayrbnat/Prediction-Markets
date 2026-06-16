"""One async client per source. Typed in, typed out. No business logic, no math,
no SQL. Every client validates external JSON into models/ immediately and raises
``SchemaDriftError`` rather than coercing a drifted payload.

NOTE: endpoint shapes were built against DATA_SOURCES.md; per CLAUDE.md §13 they
must be re-verified against live official docs at build/deploy time.
"""
