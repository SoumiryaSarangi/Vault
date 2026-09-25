"""SQLite access: one writer connection behind an asyncio.Lock + one reader (WAL, synchronous=FULL; naive: OFF). Implements brain_api.Db. Task J4.
Owner: Jaiveer.
"""
