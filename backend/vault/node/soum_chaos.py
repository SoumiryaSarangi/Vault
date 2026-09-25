"""Node-only chaos: /_chaos/corrupt (bitflip|zero|truncate|delete), /_chaos/disk_full; hook into netsim on_clear/add_state. §7.3. Task S6.
Owner: Soum.

    POST /_chaos/corrupt   {"count":10} or {"fids":[…]}, "mode":"bitflip|zero|truncate|delete"  → CorruptResult
    POST /_chaos/disk_full {"on":true}                                                        → ChaosState

Damage goes into the payload only; the header stays intact, so the fragment still looks valid until
someone verifies it (silent corruption). The in-memory index is deliberately NOT told: finding the damage
is the job of read verification and the scrubber. `delete` removes the whole .blk file (→ missing).
/_chaos/clear also clears disk_full; GET /_chaos shows {"disk_full": bool} in node_faults.
"""
import asyncio
import logging
import os
import random
import struct
from pathlib import Path

from fastapi import APIRouter

from vault.common.models import ChaosState, CorruptRequest, CorruptResult, DiskFullRequest

log = logging.getLogger("node.chaos")


def payload_offset(path: Path) -> int:
    """Byte offset where the payload starts in a .blk (magic 4 + len 4 + header)."""
    with open(path, "rb") as f:
        prefix = f.read(8)
    return 8 + struct.unpack(">I", prefix[4:8])[0]


def damage(path: Path, mode: str, rng: random.Random) -> bool:
    """Damage one .blk in place. False if it can't be damaged (gone, or an empty payload)."""
    if not path.exists():
        return False
    if mode == "delete":
        path.unlink(missing_ok=True)
        return True
    start = payload_offset(path)
    size = path.stat().st_size - start
    if size <= 0:
        return False
    with open(path, "r+b") as f:
        if mode == "bitflip":
            pos = start + rng.randrange(size)
            f.seek(pos)
            b = f.read(1)[0]
            f.seek(pos)
            f.write(bytes([b ^ (1 << rng.randrange(8))]))
        elif mode == "zero":
            f.seek(start)
            f.write(b"\0" * size)
        elif mode == "truncate":
            f.truncate(start + size // 2)
        f.flush()
        os.fsync(f.fileno())
    return True


def chaos_router(node) -> APIRouter:
    r = APIRouter(prefix="/_chaos", tags=["chaos"])
    ns = node.app_state.netsim if node.app_state is not None else None
    if ns is not None:
        ns.on_clear(lambda: setattr(node, "disk_full", False))
        ns.add_state(lambda: {"disk_full": node.disk_full})

    @r.post("/corrupt")
    async def corrupt(req: CorruptRequest) -> CorruptResult:
        rng = random.Random()
        st = node.storage
        if req.fids:
            fids = [f for f in req.fids if st.has(f)]
        else:
            pool = [fid for fid in st.scrub_order(0) if (st.entry(fid) and st.entry(fid).size > 0)]
            fids = rng.sample(pool, min(req.count or 0, len(pool)))
        done = []
        for fid in fids:
            e = st.entry(fid)
            if e is not None and await asyncio.to_thread(damage, e.path, req.mode, rng):
                done.append(fid)
        log.warning("chaos: damaged %d fragment(s) (%s)", len(done), req.mode)
        return CorruptResult(corrupted=done)

    @r.post("/disk_full")
    async def disk_full(req: DiskFullRequest) -> ChaosState:
        node.disk_full = req.on
        log.warning("chaos: disk_full %s", "on" if req.on else "off")
        return ns.state() if ns is not None else ChaosState(pid=node.pid, node_faults={"disk_full": req.on})

    return r
