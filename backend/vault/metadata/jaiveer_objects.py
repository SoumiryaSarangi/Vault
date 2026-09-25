"""GET manifest, list, DELETE (tombstone), /health (IFL + min_cut), buckets, /v1/inspect/objects. §4.2–4.3, §7.2.
Task J4 (buckets) + J5 (objects). Owner: Jaiveer.
"""
import json
import time
from typing import Optional

from fastapi import APIRouter, Request

from vault.common.models import Bucket, BucketCreate, BucketList, Policy
from vault.common.service import VaultHTTPError
from vault.metadata.jaiveer_db import one

router = APIRouter()


def _brain(request: Request):
    return request.app.state.brain


async def get_bucket(db, name: str) -> Optional[Bucket]:
    row = await db.fetchone("SELECT name, policy FROM buckets WHERE name = ?", (name,))
    return Bucket(name=row["name"], policy=Policy.model_validate_json(row["policy"])) if row else None


# ── buckets ──

@router.post("/v1/buckets", status_code=201)
async def create_bucket(request: Request, req: BucketCreate) -> Bucket:
    brain = _brain(request)
    name = req.name.strip()
    if not name or "/" in name or len(name) > 63:
        raise VaultHTTPError(400, "bad_bucket_name", "Bucket names are 1–63 characters with no '/'.", {"name": req.name})
    if req.policy not in brain.cfg.policies:
        raise VaultHTTPError(400, "unknown_policy", f"There's no policy called {req.policy}.", {"policy": req.policy})
    policy = brain.cfg.policy(req.policy)

    async def fn(c):
        if await one(c, "SELECT 1 FROM buckets WHERE name = ?", (name,)):
            return False
        await c.execute("INSERT INTO buckets (name, policy, created_at) VALUES (?,?,?)",
                        (name, policy.model_dump_json(), time.time()))
        return True
    if not await brain.db.write(fn):
        existing = await get_bucket(brain.db, name)
        raise VaultHTTPError(409, "bucket_exists", f"A bucket called {name} already exists.",
                             {"name": name, "policy": existing.policy.name if existing else None})
    return Bucket(name=name, policy=policy)


@router.get("/v1/buckets")
async def list_buckets(request: Request) -> BucketList:
    rows = await _brain(request).db.fetchall("SELECT name, policy FROM buckets ORDER BY name")
    return BucketList(buckets=[Bucket(name=r["name"], policy=Policy.model_validate_json(r["policy"])) for r in rows])
