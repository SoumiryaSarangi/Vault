"""Seed ~200 synthetic clinic files (xray-0042.png, lab-report-0113.pdf, 20 KB–3 MB random bytes)
through the gateway (ARCHITECTURE §6). Owner: Urooz. Task U2.
No real patient data, ever.
"""
import asyncio
import random

from vault.common.rpc import Rpc

# File name templates — purely synthetic, no real patient data
_PREFIXES = [
    "xray", "lab-report", "ct-scan", "mri", "ecg", "ultrasound",
    "blood-test", "pathology", "prescription", "discharge-summary",
]
_EXTENSIONS = [".png", ".pdf", ".jpg", ".dcm", ".txt"]

# Size distribution: mostly 20–500 KB, a few 1–3 MB (ARCHITECTURE §6 / task U2)
_SIZE_BUCKETS = [
    (0.70, 20_000, 500_000),    # 70%: 20 KB–500 KB
    (0.20, 500_000, 1_000_000),  # 20%: 500 KB–1 MB
    (0.10, 1_000_000, 3_000_000),  # 10%: 1–3 MB
]

_MAX_PARALLEL = 8   # ~8 concurrent PUTs to stay within the ≤ 20 s total reset target


def _make_filename(idx: int, rng: random.Random) -> str:
    prefix = _PREFIXES[idx % len(_PREFIXES)]
    ext = rng.choice(_EXTENSIONS)
    return f"{prefix}-{idx:04d}{ext}"


def _make_size(rng: random.Random) -> int:
    r = rng.random()
    cum = 0.0
    for frac, lo, hi in _SIZE_BUCKETS:
        cum += frac
        if r < cum:
            return rng.randint(lo, hi)
    return rng.randint(20_000, 500_000)


async def _upload_one(rpc: Rpc, bucket: str, key: str, content: bytes) -> bool:
    """PUT one file through the gateway. Returns True on success."""
    try:
        r = await rpc.request(
            "gw", "PUT", f"/{bucket}/{key}",
            content=content,
            headers={"Content-Length": str(len(content))},
            timeout=30.0,
        )
        return r.status_code == 200
    except Exception:
        return False


async def seed(rpc: Rpc, bucket: str = "clinic", count: int = 200, seed: int = 42) -> int:
    """Upload *count* synthetic files into *bucket*.

    Returns the number of files successfully uploaded.
    Signature is fixed (seam I9): seed(rpc, bucket, count, seed) -> int.
    """
    rng = random.Random(seed)

    # Pre-generate file specs
    files = []
    for i in range(count):
        key = _make_filename(i, rng)
        size = _make_size(rng)
        # Deterministic content: seeded random bytes so the run is reproducible
        content = rng.randbytes(size)
        files.append((key, content))

    uploaded = 0
    sem = asyncio.Semaphore(_MAX_PARALLEL)

    async def upload_with_sem(key: str, content: bytes) -> None:
        nonlocal uploaded
        async with sem:
            ok = await _upload_one(rpc, bucket, key, content)
            if ok:
                uploaded += 1

    await asyncio.gather(*[upload_with_sem(k, c) for k, c in files])
    return uploaded
