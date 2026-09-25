"""In-memory fake gateway for Oracle workload development (U3).
Run standalone: uvicorn vault.tests.urooz.urooz_fake_gateway:app --port 7080
Or import and use in tests.

Implements PUT/{bucket}/{key}, GET/{bucket}/{key}, DELETE/{bucket}/{key}
with X-Vault-Commit-Seq and X-Vault-Seq headers.
"""
import hashlib
import time
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, Response as FR

app = FastAPI(title="vault-fake-gw")

# In-memory object store: (bucket, key) -> {sha256, content, commit_seq, seq}
_store: dict[tuple[str, str], dict] = {}
_tombstones: set[tuple[str, str]] = set()
_commit_seq = 0
_seq: dict[str, int] = {}   # (bucket, key) -> per-key seq


def _next_cs() -> int:
    global _commit_seq
    _commit_seq += 1
    return _commit_seq


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@app.put("/{bucket}")
async def create_bucket(bucket: str):
    return {"bucket": bucket, "policy": "rep3"}


@app.get("/{bucket}")
async def list_bucket(bucket: str, prefix: str = "", limit: int = 1000):
    items = []
    for (b, k), v in _store.items():
        if b == bucket and k.startswith(prefix):
            items.append({
                "key": k, "size": v["size"], "etag": v["sha256"],
                "seq": v["seq"], "commit_seq": v["commit_seq"],
                "policy": "rep3", "updated_at": v["updated_at"],
            })
    return {"bucket": bucket, "objects": items[:limit]}


@app.put("/{bucket}/{key:path}")
async def put_object(bucket: str, key: str, request: Request):
    data = await request.body()
    sha = _sha(data)
    cs = _next_cs()
    bk = (bucket, key)
    sq = _seq.get(f"{bucket}:{key}", 0) + 1
    _seq[f"{bucket}:{key}"] = sq
    _store[bk] = {
        "sha256": sha, "content": data, "commit_seq": cs,
        "seq": sq, "size": len(data), "updated_at": time.time(),
    }
    _tombstones.discard(bk)
    headers = {
        "X-Vault-Commit-Seq": str(cs),
        "X-Vault-Seq": str(sq),
    }
    return JSONResponse({
        "bucket": bucket, "key": key, "version_id": f"v_{cs:08x}",
        "seq": sq, "commit_seq": cs, "etag": sha,
        "size": len(data), "policy": "rep3",
        "stored": {"chunks": 1, "fragments_acked": 3, "w": 2, "relayed": 0},
    }, headers=headers)


@app.get("/{bucket}/{key:path}")
async def get_object(bucket: str, key: str):
    bk = (bucket, key)
    if bk in _tombstones or bk not in _store:
        return FR(status_code=404)
    obj = _store[bk]
    headers = {
        "X-Vault-Commit-Seq": str(obj["commit_seq"]),
        "X-Vault-Seq": str(obj["seq"]),
        "ETag": obj["sha256"],
    }
    return FR(content=obj["content"], status_code=200,
              media_type="application/octet-stream", headers=headers)


@app.delete("/{bucket}/{key:path}")
async def delete_object(bucket: str, key: str):
    bk = (bucket, key)
    cs = _next_cs()
    _tombstones.add(bk)
    _store.pop(bk, None)
    return {"deleted": True, "commit_seq": cs}


@app.get("/_vault/health")
async def health():
    return {"pid": "fake-gw", "ok": True, "config_version": 0}
