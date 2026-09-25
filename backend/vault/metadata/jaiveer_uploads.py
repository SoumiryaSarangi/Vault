"""POST /v1/uploads (plan), /commit (BEGIN IMMEDIATE, ≥W check, seq/commit_seq, supersede), /abort.
ARCHITECTURE §4.1. Task J5. Owner: Jaiveer.

Plan: per chunk, place(...) picks n targets + gateway.spares spares (fate-aware unless Naive), each with the
node's current epoch. Fewer than W eligible machines → 503 not_enough_machines. Fewer than n but ≥ W → the plan
has fewer targets and the chunk is repaired after commit.

Commit (one transaction, rules §4.1):
  - version must exist, be pending and not older than gc.upload_timeout_s (else 410 upload_expired).
    Committing an already-committed version returns the same CommitResult (safe gateway retry).
  - every planned chunk must be reported; fragments count only when ok, on a known node that is not
    DEAD/RETIRED, frag_idx in [0, n), and (replication) the fragment sha equals the chunk sha.
    Counted = distinct frag_idx on distinct nodes ("never trust a count").
  - any chunk with < W → 409 quorum_not_met, version → aborted, write.quorum_failed emitted.
  - seq = objects.last_seq + 1, commit_seq = kv.commit_seq + 1; pointer published; old current → superseded;
    chunks below n get repair jobs (P0 when durable ≤ needed, else P1).
"""
import json
import math
import time
from typing import Any, Optional

from fastapi import APIRouter, Request, Response

from vault.common import ids
from vault.common.jaiveer_placement import Ring, active_fate_keys, place
from vault.common.models import (CommitRequest, CommitResult, JobKind, JobReason, NodeState, PlanChunk, Policy,
                                 SpareRef, TargetRef, UploadPlan, UploadRequest)
from vault.common.service import VaultHTTPError
from vault.metadata.jaiveer_app import enqueue_job_tx
from vault.metadata.jaiveer_db import all_rows, kv_incr, one
from vault.metadata.jaiveer_objects import get_bucket

router = APIRouter()

NOT_DURABLE = (NodeState.DEAD, NodeState.RETIRED)


def _brain(request: Request):
    return request.app.state.brain


# ── ring cache (rebuilt when the membership's ring-relevant fields change) ──

def get_ring(brain) -> Ring:
    nodes = brain.nodes()
    sig = tuple(sorted((n.id, n.capacity, n.state == NodeState.RETIRED) for n in nodes))
    cached = getattr(brain, "_ring_cache", None)
    if cached is None or cached[0] != sig or cached[1].vnodes_per_node != brain.cfg.placement.vnodes_per_node:
        ring = Ring(brain.cfg.placement.vnodes_per_node)
        ring.rebuild(nodes)
        brain._ring_cache = (sig, ring)
    return brain._ring_cache[1]


def fate_keys_for(brain) -> list[str]:
    """Naive mode (fate_aware_placement off) → [] = pure ring order."""
    if not brain.safety().fate_aware_placement:
        return []
    return active_fate_keys(brain.nodes(), brain.cfg.fate.keys)


def write_quorum(brain, policy: Policy) -> int:
    return policy.w if brain.safety().quorum_writes else 1


# ── conditional writes (P1): checked at begin and re-checked inside the commit transaction ──
# Kept in memory: an upload in flight during a metadata restart loses its condition (documented limitation).
_conditions: dict[str, tuple[Optional[str], Optional[str]]] = {}


def _check_conditions(cur: Optional[dict[str, Any]], if_match: Optional[str], if_none_match: Optional[str]) -> None:
    live_etag = cur["sha256"] if cur and cur["kind"] == "data" else None
    if if_none_match is not None and if_none_match.strip() == "*" and live_etag is not None:
        raise VaultHTTPError(412, "precondition_failed", "That file already exists.", {"etag": live_etag})
    if if_match is not None and if_match.strip().strip('"') != (live_etag or ""):
        raise VaultHTTPError(412, "precondition_failed", "That file changed since you last read it.",
                             {"etag": live_etag})


async def _current_version(db_or_conn, bucket: str, key: str, in_tx: bool = False) -> Optional[dict[str, Any]]:
    sql = ("SELECT v.* FROM objects o JOIN versions v ON v.version_id = o.current_version_id "
           "WHERE o.bucket = ? AND o.key = ?")
    if in_tx:
        return await one(db_or_conn, sql, (bucket, key))
    return await db_or_conn.fetchone(sql, (bucket, key))


# ── plan ──

@router.post("/v1/uploads")
async def begin_upload(request: Request, req: UploadRequest) -> UploadPlan:
    brain = _brain(request)
    bucket = await get_bucket(brain.db, req.bucket)
    if bucket is None:
        raise VaultHTTPError(404, "no_bucket", f"There's no bucket called {req.bucket}.", {"bucket": req.bucket})
    if req.size < 0:
        raise VaultHTTPError(400, "bad_size", "Size can't be negative.", {"size": req.size})
    if req.if_match is not None or req.if_none_match is not None:
        _check_conditions(await _current_version(brain.db, req.bucket, req.key), req.if_match, req.if_none_match)

    policy = bucket.policy
    chunk_size = brain.cfg.cluster.chunk_size
    nchunks = math.ceil(req.size / chunk_size) if req.size else 0
    w = write_quorum(brain, policy)
    ring = get_ring(brain)
    nodes = {n.id: n for n in brain.nodes()}
    keys = fate_keys_for(brain)
    spares = brain.cfg.gateway.spares
    version_id = ids.new_version_id()

    chunks: list[PlanChunk] = []
    for idx in range(nchunks):
        cid = ids.chunk_id(version_id, idx)
        chosen = place(ring, nodes, cid, policy.n + spares, fate_keys=keys)
        if len(chosen) < w:
            alive = sum(1 for n in nodes.values() if n.state == NodeState.ALIVE)
            raise VaultHTTPError(503, "not_enough_machines",
                                 f"Only {alive} machines are available; {bucket.name} needs at least {w}.",
                                 {"available": alive, "w": w, "n": policy.n})
        targets = chosen[:policy.n]
        size = min(chunk_size, req.size - idx * chunk_size)
        chunks.append(PlanChunk(
            chunk_id=cid, idx=idx, size=size,
            targets=[TargetRef(frag_idx=i, node_id=t, addr=nodes[t].addr, epoch=nodes[t].epoch)
                     for i, t in enumerate(targets)],
            spares=[SpareRef(node_id=s, addr=nodes[s].addr, epoch=nodes[s].epoch) for s in chosen[policy.n:]]))

    now = time.time()

    async def fn(c):
        await c.execute("INSERT INTO versions (version_id, bucket, key, kind, state, size, policy, chunk_size, "
                        "created_at) VALUES (?,?,?, 'data', 'pending', ?,?,?,?)",
                        (version_id, req.bucket, req.key, req.size, policy.model_dump_json(), chunk_size, now))
        await c.executemany("INSERT INTO chunks (chunk_id, version_id, idx, size, sha256, frag_size) "
                            "VALUES (?,?,?,?, NULL, 0)", [(ch.chunk_id, version_id, ch.idx, ch.size) for ch in chunks])
        await c.executemany("INSERT INTO fragments (chunk_id, frag_idx, node_id, state, sha256, updated_at) "
                            "VALUES (?,?,?, 'pending', NULL, ?)",
                            [(ch.chunk_id, t.frag_idx, t.node_id, now) for ch in chunks for t in ch.targets])
    await brain.db.write(fn)
    if req.if_match is not None or req.if_none_match is not None:
        _conditions[version_id] = (req.if_match, req.if_none_match)
    return UploadPlan(version_id=version_id, chunk_size=chunk_size, policy=policy, chunks=chunks)


# ── commit ──

def count_fragments(chunk_frags, policy: Policy, chunk_sha: str, node_state) -> list[tuple[int, str, str]]:
    """Valid ok fragments → [(frag_idx, node_id, sha)], one per (frag_idx, node). Also returns nothing for
    fragments on dead/unknown nodes, out-of-range frag_idx, or (replication) a sha that isn't the chunk's."""
    valid, seen = [], set()
    for f in sorted(chunk_frags, key=lambda f: (f.frag_idx, f.node_id)):
        if not f.ok or not (0 <= f.frag_idx < policy.n):
            continue
        st = node_state(f.node_id)
        if st is None or st in NOT_DURABLE:
            continue
        if policy.type == "replication" and f.sha256 != chunk_sha:
            continue
        if not f.sha256 or (f.frag_idx, f.node_id) in seen:
            continue
        seen.add((f.frag_idx, f.node_id))
        valid.append((f.frag_idx, f.node_id, f.sha256))
    return valid


def distinct_matching(valid: list[tuple[int, str, str]]) -> int:
    """Largest set of (frag_idx, node) pairs with distinct frag_idx AND distinct nodes (bipartite matching)."""
    by_idx: dict[int, list[str]] = {}
    for fi, nid, _ in valid:
        by_idx.setdefault(fi, []).append(nid)
    match: dict[str, int] = {}                      # node → frag_idx

    def augment(fi: int, seen: set[str]) -> bool:
        for nid in by_idx[fi]:
            if nid in seen:
                continue
            seen.add(nid)
            if nid not in match or augment(match[nid], seen):
                match[nid] = fi
                return True
        return False
    return sum(1 for fi in by_idx if augment(fi, set()))


@router.post("/v1/uploads/{version_id}/commit")
async def commit_upload(request: Request, version_id: str, req: CommitRequest) -> CommitResult:
    brain = _brain(request)
    now = time.time()
    timeout = brain.cfg.gc.upload_timeout_s
    safety_w = brain.safety().quorum_writes

    def node_state(nid: str):
        v = brain.node(nid)
        return v.state if v else None

    async def fn(c):
        ver = await one(c, "SELECT * FROM versions WHERE version_id = ?", (version_id,))
        if ver is None or ver["kind"] != "data":
            return ("err", 404, "no_upload", "That upload doesn't exist.", {})
        if ver["state"] == "committed":
            return ("ok", CommitResult(version_id=version_id, seq=ver["seq"], commit_seq=ver["commit_seq"],
                                       etag=ver["sha256"] or "", under_replicated_chunks=0), [])
        if ver["state"] != "pending":
            return ("err", 410, "upload_expired", "This upload was cancelled or took too long. Try again.",
                    {"state": ver["state"]})
        if ver["created_at"] < now - timeout:
            await c.execute("UPDATE versions SET state='aborted' WHERE version_id=?", (version_id,))
            return ("expired",)
        if req.size != ver["size"]:
            return ("err", 400, "size_mismatch", "The file size doesn't match the upload plan.",
                    {"planned": ver["size"], "got": req.size})
        policy = Policy.model_validate_json(ver["policy"])
        w = policy.w if safety_w else 1
        planned = {r["chunk_id"]: r for r in await all_rows(c, "SELECT * FROM chunks WHERE version_id=? ORDER BY idx",
                                                             (version_id,))}
        got = {ch.chunk_id: ch for ch in req.chunks}
        if set(got) != set(planned):
            return ("err", 400, "bad_commit", "The upload report doesn't match the plan.",
                    {"missing": sorted(set(planned) - set(got))[:5], "unknown": sorted(set(got) - set(planned))[:5]})
        if req.chunks and not req.object_sha256:
            return ("err", 400, "bad_commit", "Missing the file checksum.", {})

        per_chunk: dict[str, list[tuple[int, str, str]]] = {}
        for cid, ch in got.items():
            if not ch.sha256:
                return ("err", 400, "bad_commit", "A chunk is missing its checksum.", {"chunk_id": cid})
            valid = count_fragments(ch.fragments, policy, ch.sha256, node_state)
            n_ok = distinct_matching(valid)
            if n_ok < w:
                await c.execute("UPDATE versions SET state='aborted' WHERE version_id=?", (version_id,))
                return ("quorum", ver["key"], n_ok, w, planned[cid]["idx"])
            per_chunk[cid] = valid

        # conditional writes (P1) re-checked inside the transaction
        cond = _conditions.get(version_id)
        cur = await _current_version(c, ver["bucket"], ver["key"], in_tx=True)
        if cond:
            try:
                _check_conditions(cur, *cond)
            except VaultHTTPError as e:
                await c.execute("UPDATE versions SET state='aborted' WHERE version_id=?", (version_id,))
                return ("err", e.status, e.body.error, e.body.message, e.body.detail)

        # fragments: replace the plan's pending rows with what actually landed
        await c.execute("DELETE FROM fragments WHERE state='pending' AND chunk_id IN "
                        "(SELECT chunk_id FROM chunks WHERE version_id=?)", (version_id,))
        under, jobs = 0, []
        for cid, valid in per_chunk.items():
            ch = got[cid]
            await c.execute("UPDATE chunks SET sha256=?, frag_size=? WHERE chunk_id=?", (ch.sha256, ch.frag_size, cid))
            await c.executemany("INSERT OR REPLACE INTO fragments (chunk_id, frag_idx, node_id, state, sha256, "
                                "updated_at) VALUES (?,?,?, 'ok', ?, ?)",
                                [(cid, fi, nid, sha, now) for fi, nid, sha in valid])
            have = {fi for fi, _, _ in valid}
            if len(have) < policy.n:
                under += 1
                prio = 0 if len(have) <= policy.needed else 1
                for fi in range(policy.n):
                    if fi not in have:
                        jobs.append((cid, fi, prio))

        obj = await one(c, "SELECT * FROM objects WHERE bucket=? AND key=?", (ver["bucket"], ver["key"]))
        seq = (obj["last_seq"] if obj else 0) + 1
        commit_seq = await kv_incr(c, "commit_seq")
        if obj and obj["current_version_id"]:
            await c.execute("UPDATE versions SET state='superseded', superseded_at=? WHERE version_id=?",
                            (now, obj["current_version_id"]))
        await c.execute("INSERT INTO objects (bucket, key, current_version_id, last_seq) VALUES (?,?,?,?) "
                        "ON CONFLICT(bucket, key) DO UPDATE SET current_version_id=excluded.current_version_id, "
                        "last_seq=excluded.last_seq", (ver["bucket"], ver["key"], version_id, seq))
        await c.execute("UPDATE versions SET state='committed', seq=?, commit_seq=?, sha256=?, committed_at=? "
                        "WHERE version_id=?", (seq, commit_seq, req.object_sha256, now, version_id))
        for cid, fi, prio in jobs:
            await enqueue_job_tx(c, JobKind.repair, JobReason.under_replicated, chunk_id=cid, frag_idx=fi,
                                 priority=prio)
        return ("ok", CommitResult(version_id=version_id, seq=seq, commit_seq=commit_seq, etag=req.object_sha256,
                                   under_replicated_chunks=under), [])

    res = await brain.db.write(fn)
    _conditions.pop(version_id, None)
    if res[0] == "ok":
        return res[1]
    if res[0] == "expired":
        raise VaultHTTPError(410, "upload_expired", "This upload took too long and was cancelled. Try again.",
                             {"timeout_s": timeout})
    if res[0] == "quorum":
        _, key, n_ok, w, idx = res
        await brain.emit("write.quorum_failed", {"key": key}, {"got": n_ok, "w": w, "chunk": idx}, file=key)
        raise VaultHTTPError(409, "quorum_not_met", f"Only {n_ok} of {w} machines confirmed the file. Nothing was saved.",
                             {"got": n_ok, "w": w, "chunk_idx": idx})
    _, status, code, msg, detail = res
    raise VaultHTTPError(status, code, msg, detail)


@router.post("/v1/uploads/{version_id}/abort", status_code=204)
async def abort_upload(request: Request, version_id: str) -> Response:
    brain = _brain(request)

    async def fn(c):
        await c.execute("UPDATE versions SET state='aborted' WHERE version_id=? AND state='pending'", (version_id,))
    await brain.db.write(fn)
    _conditions.pop(version_id, None)
    return Response(status_code=204)
