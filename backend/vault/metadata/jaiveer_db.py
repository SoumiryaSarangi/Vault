"""SQLite access: one writer connection behind an asyncio.Lock + one reader (WAL, synchronous=FULL; naive: OFF).
Implements brain_api.Db. Task J4. Owner: Jaiveer.

    db = Database(cfg.data_path("meta", "vault.db"))
    await db.open()
    rows = await db.fetchall("SELECT * FROM buckets")
    await db.write(lambda c: c.execute("INSERT INTO kv VALUES (?, ?)", ("k", "v")))

Rules (ARCHITECTURE §3.5, §11):
- Every change goes through write(fn): BEGIN IMMEDIATE … COMMIT under one asyncio.Lock, ROLLBACK on error.
  That lock is what serializes commits and gives commit_seq its order.
- NEVER call write() (or emit(), which writes the event row) from inside a write fn: the lock is not reentrant.
- Reads use a separate connection (WAL readers see the last committed state and never block the writer).
- synchronous=FULL by default; set_durable(False) switches the writer to OFF (Naive mode, safety.durable_writes).
"""
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence, TypeVar

import aiosqlite
import asyncio

T = TypeVar("T")
SCHEMA_PATH = Path(__file__).with_name("jaiveer_schema.sql")


class Database:
    def __init__(self, path: str | Path, durable: bool = True):
        self.path = str(path)
        self.durable = durable
        self._w: Optional[aiosqlite.Connection] = None
        self._r: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self.created = False          # True when this open() created the schema (first start)

    # ── lifecycle ──
    async def open(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._w = await aiosqlite.connect(self.path, isolation_level=None)
        self._w.row_factory = aiosqlite.Row
        await self._w.execute("PRAGMA journal_mode=WAL")
        await self._w.execute("PRAGMA foreign_keys=ON")
        await self._w.execute("PRAGMA busy_timeout=5000")
        await self._set_sync()
        cur = await self._w.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes'")
        if await cur.fetchone() is None:
            await self._w.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            await self._set_sync()           # the script sets FULL; re-apply the requested level
            self.created = True
        self._r = await aiosqlite.connect(self.path, isolation_level=None)
        self._r.row_factory = aiosqlite.Row
        await self._r.execute("PRAGMA busy_timeout=5000")
        await self._r.execute("PRAGMA query_only=ON")

    async def close(self) -> None:
        for c in (self._r, self._w):
            if c is not None:
                await c.close()
        self._r = self._w = None

    async def _set_sync(self) -> None:
        assert self._w is not None
        await self._w.execute(f"PRAGMA synchronous={'FULL' if self.durable else 'OFF'}")

    async def set_durable(self, durable: bool) -> None:
        """Naive mode (safety.durable_writes off) → synchronous=OFF. Takes effect for the next commit."""
        async with self._lock:
            self.durable = durable
            await self._set_sync()

    # ── brain_api.Db ──
    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        assert self._r is not None, "Database not open"
        cur = await self._r.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict[str, Any]]:
        assert self._r is not None, "Database not open"
        cur = await self._r.execute(sql, params)
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row is not None else None

    async def write(self, fn: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
        """Run fn(conn) inside BEGIN IMMEDIATE … COMMIT under the single writer lock."""
        assert self._w is not None, "Database not open"
        async with self._lock:
            await self._w.execute("BEGIN IMMEDIATE")
            try:
                result = await fn(self._w)
            except BaseException:
                await self._w.execute("ROLLBACK")
                raise
            await self._w.execute("COMMIT")
            return result

    # ── small helpers (use inside write fns, or on their own) ──
    async def kv_get(self, k: str, default: Optional[str] = None) -> Optional[str]:
        row = await self.fetchone("SELECT v FROM kv WHERE k = ?", (k,))
        return row["v"] if row else default


async def kv_set(conn: aiosqlite.Connection, k: str, v: Any) -> None:
    """Inside a write fn: upsert kv[k] (non-strings are JSON-encoded)."""
    val = v if isinstance(v, str) else json.dumps(v)
    await conn.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, val))


async def kv_incr(conn: aiosqlite.Connection, k: str, by: int = 1) -> int:
    """Inside a write fn: atomically increment an integer kv counter and return the new value."""
    cur = await conn.execute("SELECT v FROM kv WHERE k = ?", (k,))
    row = await cur.fetchone()
    await cur.close()
    new = (int(row[0]) if row else 0) + by
    await kv_set(conn, k, str(new))
    return new


async def one(conn: aiosqlite.Connection, sql: str, params: Sequence[Any] = ()) -> Optional[dict[str, Any]]:
    """Inside a write fn: fetch one row as a dict (reads see the transaction's own writes)."""
    cur = await conn.execute(sql, params)
    row = await cur.fetchone()
    await cur.close()
    return dict(row) if row is not None else None


async def all_rows(conn: aiosqlite.Connection, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    await cur.close()
    return [dict(r) for r in rows]


def now() -> float:
    return time.time()
