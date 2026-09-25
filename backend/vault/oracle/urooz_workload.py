"""N concurrent seeded clients over keyspace in bucket oracle: 50% PUT (1 KB–2 MB), 40% GET, 10% DELETE via the gateway; outcome ok|fail|unknown. §5. Task U3.
Owner: Urooz.
"""
import asyncio
import hashlib
import random
import time
from pathlib import Path
from typing import Callable

from vault.common.models import LedgerEntry
from vault.common.rpc import Rpc
from vault.oracle.urooz_ledger import Ledger

# ── constants ────────────────────────────────────────────────────────────────
BUCKET = "oracle"
PUT_FRACTION = 0.50
GET_FRACTION = 0.90   # cumulative; DELETE = 1.0
MIN_PUT_BYTES = 1_024         # 1 KB
MAX_PUT_BYTES = 2_097_152     # 2 MB


def _random_bytes(rng: random.Random, size: int) -> bytes:
    return rng.randbytes(size)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _do_put(rpc: Rpc, key: str, data: bytes, timeout: float) -> tuple[str, str | None, int | None, int | None]:
    """Returns (outcome, sha256, commit_seq, seq, http)"""
    sha = _sha256(data)
    try:
        r = await rpc.request(
            "gw", "PUT", f"/{BUCKET}/{key}",
            content=data,
            headers={"Content-Length": str(len(data))},
            timeout=timeout,
        )
        if r.status_code == 200:
            body = r.json()
            return "ok", sha, body.get("commit_seq"), body.get("seq"), 200
        # 4xx before effect
        return "fail", sha, None, None, r.status_code
    except Exception:
        # timeout, connection reset, 5xx after body sent → unknown
        return "unknown", sha, None, None, None


async def _do_get(rpc: Rpc, key: str, timeout: float) -> tuple[str, str | None, int | None, int | None, int | None]:
    """Returns (outcome, sha256, commit_seq, seq, http)"""
    try:
        r = await rpc.request("gw", "GET", f"/{BUCKET}/{key}", timeout=timeout)
        if r.status_code == 200:
            sha = _sha256(r.content)
            cs = int(r.headers.get("X-Vault-Commit-Seq", 0)) or None
            seq = int(r.headers.get("X-Vault-Seq", 0)) or None
            return "ok", sha, cs, seq, 200
        if r.status_code == 404:
            return "ok", None, None, None, 404
        return "fail", None, None, None, r.status_code
    except Exception:
        return "unknown", None, None, None, None


async def _do_delete(rpc: Rpc, key: str, timeout: float) -> tuple[str, int | None, int | None]:
    """Returns (outcome, commit_seq, http)"""
    try:
        r = await rpc.request("gw", "DELETE", f"/{BUCKET}/{key}", timeout=timeout)
        if r.status_code == 200:
            body = r.json()
            return "ok", body.get("commit_seq"), 200
        return "fail", None, r.status_code
    except Exception:
        return "unknown", None, None


async def _client_loop(
    client_id: int,
    rpc: Rpc,
    ledger: Ledger,
    keyspace: int,
    duration_s: float,
    seed: int,
    stop_event: asyncio.Event,
    counters: dict,
    timeout: float = 10.0,
) -> None:
    """One async client: runs until stop_event is set or duration_s elapsed."""
    rng = random.Random(seed + client_id * 1_000_003)
    deadline = time.monotonic() + duration_s

    while not stop_event.is_set() and time.monotonic() < deadline:
        key = f"k{rng.randint(0, keyspace - 1):03d}"
        r = rng.random()
        now = time.time()

        if r < PUT_FRACTION:
            size = rng.randint(MIN_PUT_BYTES, MAX_PUT_BYTES)
            data = _random_bytes(rng, size)
            invoke = time.time()
            outcome, sha, commit_seq, seq, http = await _do_put(rpc, key, data, timeout)
            complete = time.time()
            ledger.append(LedgerEntry(
                op="put", key=key, invoke=invoke, complete=complete,
                outcome=outcome, sha256=sha, commit_seq=commit_seq, seq=seq, http=http,
            ))
        elif r < GET_FRACTION:
            invoke = time.time()
            outcome, sha, commit_seq, seq, http = await _do_get(rpc, key, timeout)
            complete = time.time()
            ledger.append(LedgerEntry(
                op="get", key=key, invoke=invoke, complete=complete,
                outcome=outcome, sha256=sha, commit_seq=commit_seq, seq=seq, http=http,
            ))
        else:
            invoke = time.time()
            outcome, commit_seq, http = await _do_delete(rpc, key, timeout)
            complete = time.time()
            ledger.append(LedgerEntry(
                op="delete", key=key, invoke=invoke, complete=complete,
                outcome=outcome, commit_seq=commit_seq, http=http,
            ))

        counters["ops"] += 1
        if outcome == "ok":
            pass  # counted above by caller
        elif outcome == "unknown":
            counters["unknown"] += 1
        elif outcome == "fail":
            counters["failed"] += 1

        await asyncio.sleep(0)   # yield to event loop


async def run_workload(
    rpc: Rpc,
    ledger: Ledger,
    keyspace: int = 200,
    clients: int = 8,
    duration_s: float = 60.0,
    seed: int = 42,
    stop_event: asyncio.Event | None = None,
    counters: dict | None = None,
) -> dict:
    """Launch *clients* concurrent client loops and run them for *duration_s* seconds.

    Returns a counters dict with keys: ops, unknown, failed.
    """
    _stop = stop_event or asyncio.Event()
    _counters: dict = counters or {"ops": 0, "unknown": 0, "failed": 0}

    tasks = [
        asyncio.create_task(
            _client_loop(i, rpc, ledger, keyspace, duration_s, seed, _stop, _counters),
            name=f"oracle-client-{i}",
        )
        for i in range(clients)
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    return _counters


async def verify_all_keys(rpc: Rpc, keyspace: int = 200) -> dict[str, tuple[str | None, int | None]]:
    """After settling: GET every key and return {key: (sha256|None, commit_seq|None)}."""
    result: dict[str, tuple[str | None, int | None]] = {}
    sem = asyncio.Semaphore(16)

    async def _fetch(key: str) -> None:
        async with sem:
            outcome, sha, commit_seq, _, _ = await _do_get(rpc, key, timeout=15.0)
            result[key] = (sha, commit_seq) if outcome in ("ok",) else (None, None)

    await asyncio.gather(*[_fetch(f"k{i:03d}") for i in range(keyspace)])
    return result
