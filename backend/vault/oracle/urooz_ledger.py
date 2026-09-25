"""Append-only ledger: oracle_runs/<run_id>/ledger.jsonl of models.LedgerEntry, flush + fsync every 100 ms. §5. Task U1.
Owner: Urooz.
"""
import asyncio
import json
import os
from pathlib import Path

from vault.common.models import LedgerEntry


class Ledger:
    """Append-only JSONL ledger flushed + fsynced every 100 ms.

    Usage::

        ledger = Ledger(run_dir)
        await ledger.open()
        ledger.append(entry)          # non-blocking; queued for flush
        entries = ledger.entries()    # snapshot of all entries so far
        await ledger.close()

    The file is created at ``<run_dir>/ledger.jsonl``.
    """

    FLUSH_INTERVAL_S = 0.1   # 100 ms

    def __init__(self, run_dir: Path) -> None:
        self._path = run_dir / "ledger.jsonl"
        self._entries: list[LedgerEntry] = []
        self._buf: list[str] = []
        self._fh: "os.IO[str] | None" = None   # type: ignore[name-defined]
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._lock = asyncio.Lock()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def open(self) -> None:
        """Create run directory and open (or resume) the ledger file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a", encoding="utf-8")  # append mode  # noqa: SIM115
        self._task = asyncio.create_task(self._flush_loop(), name="ledger-flush")

    async def close(self) -> None:
        """Flush remaining lines, stop background task, close file."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._flush()
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # ── public API ────────────────────────────────────────────────────────────

    def append(self, entry: LedgerEntry) -> None:
        """Record one operation. Thread-safe (asyncio single-threaded)."""
        self._entries.append(entry)
        self._buf.append(entry.model_dump_json())

    def entries(self) -> list[LedgerEntry]:
        """Return a snapshot of all recorded entries (in append order)."""
        return list(self._entries)

    # ── internals ────────────────────────────────────────────────────────────

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.FLUSH_INTERVAL_S)
            await self._flush()

    async def _flush(self) -> None:
        if not self._buf or self._fh is None:
            return
        async with self._lock:
            lines, self._buf = self._buf, []
        text = "\n".join(lines) + "\n"
        self._fh.write(text)
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass  # directory fsync skipped on Windows (TECH_STACK §6.3)


def load_ledger(run_dir: Path) -> list[LedgerEntry]:
    """Read a completed ledger file back into memory (for the checker)."""
    path = run_dir / "ledger.jsonl"
    entries: list[LedgerEntry] = []
    if not path.exists():
        return entries
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(LedgerEntry(**json.loads(line)))
    return entries
