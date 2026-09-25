"""GET manifest, list, DELETE (tombstone), /health (IFL + min_cut), buckets, /v1/inspect/objects. §4.2–4.3, §7.2.
Task J4 (buckets) + J5 (objects). Owner: Jaiveer.
"""
import json
import time
from typing import Optional

from fastapi import APIRouter, Request

from vault.common import ids
from vault.common.jaiveer_fate import build_domains, ifl, target_ifl
from vault.common.models import (Bucket, BucketCreate, BucketList, ChunkHealth, DeleteResult, DomainRef, FragLoc,
                                 InspectObject, InspectPage, Manifest, ManifestChunk, ObjectHealth, ObjectList,
                                 ObjectListItem, Policy)
from vault.common.service import VaultHTTPError
from vault.metadata.jaiveer_db import kv_incr, one

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


# ── objects (J5) ──

NOT_DURABLE_STATES = ("DEAD", "RETIRED")


def _node_state(brain, nid: str) -> Optional[str]:
    v = brain.node(nid)
    return v.state.value if v else None


def _durable(brain, rows: list[dict]) -> dict[str, set[int]]:
    """ok rows on nodes not DEAD/RETIRED → node_id → {frag_idx}."""
    out: dict[str, set[int]] = {}
    for r in rows:
        st = _node_state(brain, r["node_id"])
        if r["state"] == "ok" and st is not None and st not in NOT_DURABLE_STATES:
            out.setdefault(r["node_id"], set()).add(r["frag_idx"])
    return out


def _available(brain, rows: list[dict]) -> set[int]:
    """Distinct frag_idx with ok rows on ALIVE/SUSPECT/PARTITIONED nodes (§3.4 'available')."""
    return {r["frag_idx"] for r in rows if r["state"] == "ok"
            and _node_state(brain, r["node_id"]) in ("ALIVE", "SUSPECT", "PARTITIONED")}


def _fragloc(brain, r: dict):
    v = brain.node(r["node_id"])
    return FragLoc(frag_idx=r["frag_idx"], node_id=r["node_id"], addr=v.addr if v else "", state=r["state"],
                   sha256=r["sha256"], route=v.route if v else "direct", slow=v.slow if v else False)


def _domains(brain):
    return build_domains(brain.nodes(), brain.cfg.fate.keys)[0]


async def _live(brain, bucket: str, key: str) -> dict:
    """Current committed data version of bucket/key, or 404."""
    if await get_bucket(brain.db, bucket) is None:
        raise VaultHTTPError(404, "no_bucket", f"There's no bucket called {bucket}.", {"bucket": bucket})
    ver = await brain.db.fetchone(
        "SELECT v.* FROM objects o JOIN versions v ON v.version_id = o.current_version_id "
        "WHERE o.bucket = ? AND o.key = ? AND v.kind = 'data' AND v.state = 'committed'", (bucket, key))
    if ver is None:
        raise VaultHTTPError(404, "not_found", f"There's no file called {key} in {bucket}.",
                             {"bucket": bucket, "key": key})
    return ver


async def _chunks_with_frags(brain, version_ids: list[str]) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """version_id → chunks (by idx); chunk_id → fragment rows (ok/corrupt/missing/incoming, not pending)."""
    if not version_ids:
        return {}, {}
    qs = ",".join("?" * len(version_ids))
    chunks = await brain.db.fetchall(f"SELECT * FROM chunks WHERE version_id IN ({qs}) ORDER BY version_id, idx",
                                     version_ids)
    frags = await brain.db.fetchall(
        f"SELECT f.* FROM fragments f JOIN chunks c ON c.chunk_id = f.chunk_id WHERE c.version_id IN ({qs}) "
        f"AND f.state != 'pending' ORDER BY f.chunk_id, f.frag_idx, f.node_id", version_ids)
    by_v: dict[str, list[dict]] = {}
    for ch in chunks:
        by_v.setdefault(ch["version_id"], []).append(ch)
    by_c: dict[str, list[dict]] = {}
    for f in frags:
        by_c.setdefault(f["chunk_id"], []).append(f)
    return by_v, by_c


@router.get("/v1/objects/{bucket}/{key:path}/health")
async def object_health(request: Request, bucket: str, key: str) -> ObjectHealth:
    brain = _brain(request)
    ver = await _live(brain, bucket, key)
    policy = Policy.model_validate_json(ver["policy"])
    by_v, by_c = await _chunks_with_frags(brain, [ver["version_id"]])
    domains = _domains(brain)
    target = target_ifl(policy)
    worst, worst_cut, chunks_out = None, [], []
    for ch in by_v.get(ver["version_id"], []):
        rows = by_c.get(ch["chunk_id"], [])
        holders = _durable(brain, rows)
        level, cut = ifl(holders, policy.needed, domains)
        if worst is None or level < worst:
            worst, worst_cut = level, cut
        chunks_out.append(ChunkHealth(idx=ch["idx"], needed=policy.needed,
                                      durable=len(set().union(*holders.values())) if holders else 0,
                                      available=len(_available(brain, rows)),
                                      fragments=[_fragloc(brain, r) for r in rows if r["state"] != "pending"]))
    if worst is None:                                  # empty file: nothing to lose
        worst = target
    # domains shared by every durable holder of the worst chunk (what the Inspect drawer highlights)
    shared: list[DomainRef] = []
    if chunks_out:
        worst_chunk = min(by_v[ver["version_id"]], key=lambda ch: ifl(_durable(brain, by_c.get(ch["chunk_id"], [])),
                                                                       policy.needed, domains)[0])
        holders = set(_durable(brain, by_c.get(worst_chunk["chunk_id"], [])))
        if len(holders) > 1:
            shared = [DomainRef(key=k, value=v) for (k, v), members in domains.items()
                      if k != "node" and holders <= members]
    return ObjectHealth(ifl=worst, target=target, chunks=chunks_out, shared=shared,
                        min_cut=[DomainRef(key=k, value=v) for k, v in worst_cut])


@router.get("/v1/objects/{bucket}/{key:path}")
async def get_manifest(request: Request, bucket: str, key: str) -> Manifest:
    brain = _brain(request)
    ver = await _live(brain, bucket, key)
    by_v, by_c = await _chunks_with_frags(brain, [ver["version_id"]])
    chunks = [ManifestChunk(chunk_id=ch["chunk_id"], idx=ch["idx"], size=ch["size"], sha256=ch["sha256"] or "",
                            frag_size=ch["frag_size"],
                            fragments=[_fragloc(brain, r) for r in by_c.get(ch["chunk_id"], []) if r["state"] == "ok"])
              for ch in by_v.get(ver["version_id"], [])]
    return Manifest(bucket=bucket, key=key, version_id=ver["version_id"], seq=ver["seq"],
                    commit_seq=ver["commit_seq"], size=ver["size"], sha256=ver["sha256"] or "",
                    policy=Policy.model_validate_json(ver["policy"]), chunk_size=ver["chunk_size"], chunks=chunks)


@router.get("/v1/objects/{bucket}")
async def list_objects(request: Request, bucket: str, prefix: str = "", limit: int = 1000) -> ObjectList:
    brain = _brain(request)
    if await get_bucket(brain.db, bucket) is None:
        raise VaultHTTPError(404, "no_bucket", f"There's no bucket called {bucket}.", {"bucket": bucket})
    limit = max(1, min(limit, 10000))
    esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = await brain.db.fetchall(
        "SELECT o.key, v.size, v.sha256, v.seq, v.commit_seq, v.policy, v.committed_at FROM objects o "
        "JOIN versions v ON v.version_id = o.current_version_id "
        "WHERE o.bucket = ? AND v.kind = 'data' AND v.state = 'committed' AND o.key LIKE ? ESCAPE '\\' "
        "ORDER BY o.key LIMIT ?", (bucket, esc + "%", limit))
    return ObjectList(bucket=bucket, objects=[
        ObjectListItem(key=r["key"], size=r["size"], etag=r["sha256"] or "", seq=r["seq"], commit_seq=r["commit_seq"],
                       policy=json.loads(r["policy"])["name"], updated_at=r["committed_at"]) for r in rows])


@router.delete("/v1/objects/{bucket}/{key:path}")
async def delete_object(request: Request, bucket: str, key: str) -> DeleteResult:
    """Commit a tombstone (§4.3). Missing or already-deleted key → {"deleted": false} and nothing is written."""
    brain = _brain(request)
    b = await get_bucket(brain.db, bucket)
    if b is None:
        raise VaultHTTPError(404, "no_bucket", f"There's no bucket called {bucket}.", {"bucket": bucket})
    now = time.time()

    async def fn(c):
        obj = await one(c, "SELECT o.last_seq, o.current_version_id, v.kind FROM objects o "
                           "JOIN versions v ON v.version_id = o.current_version_id WHERE o.bucket=? AND o.key=?",
                        (bucket, key))
        if obj is None or obj["kind"] != "data":
            return DeleteResult(deleted=False, commit_seq=None)
        vid = ids.new_version_id()
        seq = obj["last_seq"] + 1
        commit_seq = await kv_incr(c, "commit_seq")
        await c.execute("INSERT INTO versions (version_id, bucket, key, kind, state, seq, commit_seq, size, sha256, "
                        "policy, chunk_size, created_at, committed_at) "
                        "VALUES (?,?,?, 'tombstone', 'committed', ?,?, 0, NULL, ?,?,?,?)",
                        (vid, bucket, key, seq, commit_seq, b.policy.model_dump_json(),
                         brain.cfg.cluster.chunk_size, now, now))
        await c.execute("UPDATE versions SET state='superseded', superseded_at=? WHERE version_id=?",
                        (now, obj["current_version_id"]))
        await c.execute("UPDATE objects SET current_version_id=?, last_seq=? WHERE bucket=? AND key=?",
                        (vid, seq, bucket, key))
        return DeleteResult(deleted=True, commit_seq=commit_seq)
    return await brain.db.write(fn)


@router.get("/v1/inspect/objects")
async def inspect_objects(request: Request, cursor: Optional[str] = None, limit: int = 500) -> InspectPage:
    """Every object (live or deleted) with durability, for the Oracle (I7) and the Files page.
    cursor = "<bucket>/<key>" of the last row of the previous page."""
    brain = _brain(request)
    limit = max(1, min(limit, 5000))
    cb, ck = ("", "")
    if cursor:
        cb, _, ck = cursor.partition("/")
    rows = await brain.db.fetchall(
        "SELECT o.bucket, o.key, v.* FROM objects o JOIN versions v ON v.version_id = o.current_version_id "
        "WHERE (o.bucket > ? OR (o.bucket = ? AND o.key > ?)) ORDER BY o.bucket, o.key LIMIT ?",
        (cb, cb, ck, limit + 1))
    more = len(rows) > limit
    rows = rows[:limit]
    live_ids = [r["version_id"] for r in rows if r["kind"] == "data"]
    by_v, by_c = await _chunks_with_frags(brain, live_ids)
    domains = _domains(brain)
    out = []
    for r in rows:
        policy = Policy.model_validate_json(r["policy"])
        target = policy.n
        if r["kind"] != "data":
            out.append(InspectObject(bucket=r["bucket"], key=r["key"], state="deleted", seq=r["seq"],
                                     commit_seq=r["commit_seq"], target=target))
            continue
        durable_min, level = target, target_ifl(policy)
        for ch in by_v.get(r["version_id"], []):
            holders = _durable(brain, by_c.get(ch["chunk_id"], []))
            distinct = len(set().union(*holders.values())) if holders else 0
            durable_min = min(durable_min, distinct)
            level = min(level, ifl(holders, policy.needed, domains)[0])
        out.append(InspectObject(bucket=r["bucket"], key=r["key"], state="live", seq=r["seq"],
                                 commit_seq=r["commit_seq"], sha256=r["sha256"], size=r["size"],
                                 durable_min=durable_min, target=target, ifl=level))
    nxt = f"{rows[-1]['bucket']}/{rows[-1]['key']}" if more and rows else None
    return InspectPage(objects=out, next_cursor=nxt)
