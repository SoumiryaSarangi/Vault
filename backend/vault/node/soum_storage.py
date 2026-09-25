"""On-disk fragments: data/<node>/blobs/<vid[2:4]>/<fid>.blk (magic VLT1 + header_len + header JSON + payload), tmp/, quarantine/, node.json. Atomic write (TECH_STACK §6.3), startup cleanup, index. §3.6, §4.14. Task S2.
Owner: Soum.

    st = Storage(cfg.data_path(pid), pid, safety=lambda: app.state.safety)
    discarded = await st.startup()                 # deletes tmp/*, rebuilds the index from blobs/
    await st.put(header, payload)                  # crash-safe unless safety.durable_writes is off
    header, payload = await st.get(fid)            # raises NotFound, or Corrupt after quarantine

The node app (the only caller) checks X-Vault-Sha256 on receive; storage only verifies on read and scrub.
Blocking file I/O runs in asyncio.to_thread (ARCHITECTURE §11).
"""
import asyncio
import json
import os
import secrets
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from vault.common.config import SafetyCfg
from vault.common.hashing import sha256_hex
from vault.common.models import FragmentHeader, InventoryItem

MAGIC = b"VLT1"
_LEN = struct.Struct(">I")
_PREFIX = len(MAGIC) + _LEN.size


class StorageError(Exception):
    def __init__(self, fid: str, reason: str = ""):
        super().__init__(f"{fid}: {reason}" if reason else fid)
        self.fid = fid
        self.reason = reason


class NotFound(StorageError):
    pass


class Corrupt(StorageError):
    """The fragment failed verification and has been moved to quarantine/."""


@dataclass
class Entry:
    path: Path
    header: FragmentHeader
    size: int           # payload bytes
    file_size: int      # whole .blk
    mtime: float


# ── .blk format (§3.6) ──

def encode_blk(header: FragmentHeader, payload: bytes) -> bytes:
    h = header.model_dump_json().encode()
    return MAGIC + _LEN.pack(len(h)) + h + payload


def decode_blk(blob: bytes) -> tuple[FragmentHeader, bytes]:
    """Raises ValueError if the magic, length or header JSON is unreadable."""
    if len(blob) < _PREFIX or blob[:4] != MAGIC:
        raise ValueError("bad magic")
    (n,) = _LEN.unpack_from(blob, 4)
    if len(blob) < _PREFIX + n:
        raise ValueError("truncated header")
    header = FragmentHeader.model_validate_json(blob[_PREFIX:_PREFIX + n])
    return header, blob[_PREFIX + n:]


def _read_header(path: Path) -> tuple[FragmentHeader, int]:
    """Header and payload size, without reading the payload."""
    with open(path, "rb") as f:
        prefix = f.read(_PREFIX)
        if len(prefix) < _PREFIX or prefix[:4] != MAGIC:
            raise ValueError("bad magic")
        (n,) = _LEN.unpack_from(prefix, 4)
        raw = f.read(n)
        if len(raw) < n:
            raise ValueError("truncated header")
    return FragmentHeader.model_validate_json(raw), path.stat().st_size - _PREFIX - n


# ── writes (TECH_STACK §6.3) ──

def atomic_write(tmp_path: Path, final_path: Path, data: bytes) -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())                # bytes are on disk
    os.replace(tmp_path, final_path)        # atomic rename (also on Windows)
    if os.name != "nt":                     # make the rename itself durable
        fd = os.open(final_path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def direct_write(final_path: Path, data: bytes) -> None:
    """Naive mode (safety.durable_writes off): no temp file, no fsync, no rename."""
    final_path.parent.mkdir(parents=True, exist_ok=True)
    with open(final_path, "wb") as f:
        f.write(data)


class Storage:
    def __init__(self, root: str | Path, node_id: str, safety: Callable[[], SafetyCfg] = SafetyCfg):
        self.root = Path(root)
        self.node_id = node_id
        self.safety = safety
        self.blobs = self.root / "blobs"
        self.tmp = self.root / "tmp"
        self.quarantine_dir = self.root / "quarantine"
        self.node_json = self.root / "node.json"
        self._index: dict[str, Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ── paths and locks ──
    def blob_path(self, header: FragmentHeader) -> Path:
        return self.blobs / header.version_id[2:4] / f"{header.fid}.blk"

    def lock(self, fid: str) -> asyncio.Lock:
        """Per-fid lock so a concurrent pull and PUT of the same fid can't interleave (§11)."""
        return self._locks.setdefault(fid, asyncio.Lock())

    # ── startup (§4.14 steps 1–2) ──
    async def startup(self) -> int:
        """Delete tmp/* and rebuild the index from blobs/. Returns how many tmp files were discarded."""
        discarded, index = await asyncio.to_thread(self._startup_sync)
        self._index = index
        return discarded

    def _startup_sync(self) -> tuple[int, dict[str, Entry]]:
        for d in (self.blobs, self.tmp, self.quarantine_dir):
            d.mkdir(parents=True, exist_ok=True)
        discarded = 0
        for p in self.tmp.iterdir():
            if p.is_file():
                p.unlink(missing_ok=True)
                discarded += 1
        for p in self.root.glob("node.json.*.tmp"):     # an interrupted write_node_json()
            p.unlink(missing_ok=True)
        index: dict[str, Entry] = {}
        for p in self.blobs.glob("*/*.blk"):
            try:
                header, size = _read_header(p)
            except (OSError, ValueError):
                # Unreadable header (e.g. a torn naive-mode write): set it aside, the fragment is gone.
                os.replace(p, self.quarantine_dir / p.name)
                continue
            st = p.stat()
            index[header.fid] = Entry(p, header, size, st.st_size, st.st_mtime)
        return discarded, index

    # ── fragments ──
    async def put(self, header: FragmentHeader, payload: bytes) -> Entry:
        """Store a fragment. Durable (tmp + fsync + rename) unless safety.durable_writes is off."""
        final = self.blob_path(header)
        data = encode_blk(header, payload)
        async with self.lock(header.fid):
            if self.safety().durable_writes:
                tmp = self.tmp / f"{header.fid}.{secrets.token_hex(4)}.part"
                await asyncio.to_thread(atomic_write, tmp, final, data)
            else:
                await asyncio.to_thread(direct_write, final, data)
            e = Entry(final, header, len(payload), len(data), time.time())
            self._index[header.fid] = e
        return e

    async def get(self, fid: str) -> tuple[FragmentHeader, bytes]:
        """Header and payload. With safety.verify_on_read on, a damaged fragment is quarantined and
        Corrupt is raised; with it off the bytes are returned unchecked."""
        e = self._index.get(fid)
        if e is None:
            raise NotFound(fid)
        try:
            blob = await asyncio.to_thread(e.path.read_bytes)
        except FileNotFoundError:
            self._index.pop(fid, None)
            raise NotFound(fid, "file gone from disk") from None
        try:
            header, payload = decode_blk(blob)
        except ValueError as err:
            await self.quarantine(fid)
            raise Corrupt(fid, str(err)) from None
        if self.safety().verify_on_read and sha256_hex(payload) != header.frag_sha256:
            await self.quarantine(fid)
            raise Corrupt(fid, "sha256 mismatch")
        return header, payload

    async def verify(self, fid: str) -> bool:
        """Scrub one fragment regardless of verify_on_read: True if intact, else quarantined → False."""
        e = self._index.get(fid)
        if e is None:
            raise NotFound(fid)
        try:
            blob = await asyncio.to_thread(e.path.read_bytes)
        except FileNotFoundError:
            self._index.pop(fid, None)
            raise NotFound(fid, "file gone from disk") from None
        try:
            header, payload = decode_blk(blob)
            ok = sha256_hex(payload) == header.frag_sha256
        except ValueError:
            ok = False
        if not ok:
            await self.quarantine(fid)
        return ok

    def has(self, fid: str) -> bool:
        return fid in self._index

    def entry(self, fid: str) -> Optional[Entry]:
        return self._index.get(fid)

    async def delete(self, fid: str) -> bool:
        async with self.lock(fid):
            e = self._index.pop(fid, None)
            if e is None:
                return False
            await asyncio.to_thread(e.path.unlink, missing_ok=True)
        return True

    async def quarantine(self, fid: str) -> bool:
        """Move a fragment to quarantine/ and forget it (scrub, read, or metadata's request)."""
        async with self.lock(fid):
            e = self._index.pop(fid, None)
            if e is None:
                return False
            try:
                await asyncio.to_thread(os.replace, e.path, self.quarantine_dir / f"{fid}.blk")
            except FileNotFoundError:
                pass
        return True

    # ── views ──
    def inventory(self) -> list[InventoryItem]:
        """Every indexed fragment. sha256 is the header's expected hash: damaged bytes are found by
        read and scrub, which quarantine them, so they drop out of the inventory (→ missing)."""
        return [InventoryItem(fid=fid, sha256=e.header.frag_sha256, size=e.size, mtime=e.mtime)
                for fid, e in self._index.items()]

    def scrub_order(self, recent_first_s: float) -> list[str]:
        """fids to scrub: modified in the last recent_first_s first (newest first), then the rest."""
        cutoff = time.time() - recent_first_s
        recent = sorted((e for e in self._index.values() if e.mtime >= cutoff), key=lambda e: -e.mtime)
        rest = sorted((e for e in self._index.values() if e.mtime < cutoff), key=lambda e: str(e.path))
        return [e.header.fid for e in recent + rest]

    def disk_used(self) -> int:
        return sum(e.file_size for e in self._index.values())

    def count(self) -> int:
        return len(self._index)

    # ── node.json {node_id, epoch, lease_expiry} ──
    async def read_node_json(self) -> dict[str, Any]:
        """{} if missing or unreadable."""
        def _read() -> dict[str, Any]:
            try:
                return json.loads(self.node_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        return await asyncio.to_thread(_read)

    async def write_node_json(self, data: dict[str, Any]) -> None:
        """Always atomic: the lease expiry must survive a crash intact."""
        blob = json.dumps({"node_id": self.node_id, **data}).encode()
        tmp = self.root / f"node.json.{secrets.token_hex(4)}.tmp"
        await asyncio.to_thread(atomic_write, tmp, self.node_json, blob)
