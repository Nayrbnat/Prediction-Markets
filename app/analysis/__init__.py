"""Pure functions — data in, data out, deterministic, no network, no SQL.

Nothing here imports httpx, asyncpg, or anything from sources/persistence. This is
the most heavily tested layer; every expected value in its tests is hand-checked.
"""
