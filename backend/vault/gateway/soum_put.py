"""Streaming PUT: UploadPlan → per 1 MiB chunk (≤2 in flight) sha256, EC encode, fragment PUT ×n, ≥W acks, spares, commit. §4.1. Task S5.
Owner: Soum.

Per chunk: send the n fragments in parallel. A frag_idx whose target fails moves to the next unused spare.
As soon as W distinct frag_idx are acked, stragglers get STRAGGLER_GRACE_S more, then count as not ok
(the copy is completed by repair; a late write is adopted or trimmed by reconciliation, §4.10).

Quorum failure (< W after spares): stop sending, but keep reading the body (hash only) so that every planned
chunk can be reported, then POST the commit anyway. Metadata answers 409 quorum_not_met, aborts the version
and emits write.quorum_failed (Jaiveer J5); the client gets 503 quorum_not_met. /abort is only used when the
client goes away or the body is the wrong length.
"""
import asyncio
import logging
from collections import deque
from typing import AsyncIterator, Optional

from vault.common import ids
from vault.common.hashing import new_sha256, sha256_hex
from vault.common.models import (CommitChunk, CommitFragment, CommitRequest, CommitResult, FragmentHeader,
                                 PlanChunk, PutResult, PutStored, UploadPlan, UploadRequest)
from vault.common.rpc import NetworkError, get_rpc
from vault.common.service import VaultHTTPError
from vault.common.soum_ec import ec_encode
from vault.gateway.soum_meta_client import Gateway, meta
from vault.node.soum_storage import encode_meta

log = logging.getLogger("gateway.put")

# > 2 s on purpose: on Windows a connect to a dead local port is refused only after ~2.0 s (measured), so a
# shorter grace would cancel the failing attempt before its spare is tried. Only matters in the seconds after
# a crash, until metadata stops placing on that machine.
STRAGGLER_GRACE_S = 2.5
COMMIT_ATTEMPTS = 3


class ChunkOutcome:
    def __init__(self, commit: CommitChunk, acked: int, relayed: int, quorum: bool):
        self.commit, self.acked, self.relayed, self.quorum = commit, acked, relayed, quorum


async def store_chunk(gw: Gateway, plan: UploadPlan, pc: PlanChunk, data: bytes, bucket: str, key: str,
                      w: int) -> ChunkOutcome:
    policy = plan.policy
    chunk_sha = sha256_hex(data)
    if policy.type == "erasure":
        frags = ec_encode(data, policy.k, policy.m)
    else:
        frags = [data] * policy.n
    frag_size = len(frags[0]) if frags else 0
    frag_sha = [sha256_hex(f) for f in frags] if policy.type == "erasure" else [chunk_sha] * policy.n
    spares = deque(pc.spares)
    attempts: list[CommitFragment] = []
    acked: set[int] = set()
    relayed = 0
    timeout = gw.cfg.gateway.timeout_data_s

    async def put_one(frag_idx: int, node_id: str, epoch: int) -> bool:
        nonlocal relayed
        fid = ids.fid(pc.chunk_id, frag_idx)
        header = FragmentHeader(fid=fid, chunk_id=pc.chunk_id, frag_idx=frag_idx, version_id=plan.version_id,
                                bucket=bucket, key=key, policy=policy.name, chunk_sha256=chunk_sha,
                                frag_sha256=frag_sha[frag_idx], chunk_size=len(data), frag_size=frag_size,
                                epoch=epoch)
        ok = False
        try:
            r = await get_rpc().request(node_id, "PUT", f"/v1/fragments/{fid}", content=frags[frag_idx],
                                        headers={"X-Vault-Meta": encode_meta(header),
                                                 "X-Vault-Sha256": frag_sha[frag_idx],
                                                 "X-Vault-Epoch": str(epoch)}, timeout=timeout)
            ok = r.status_code == 201
            if ok and r.extensions.get("vault_route", "direct") != "direct":
                relayed += 1
            if not ok:
                log.info("PUT %s on %s -> HTTP %d %s", fid, node_id, r.status_code, r.text[:120])
        except NetworkError as e:
            log.info("PUT %s on %s unreachable: %s", fid, node_id, e.reason)
        finally:
            # also runs on cancellation: a straggler is reported as not ok
            attempts.append(CommitFragment(frag_idx=frag_idx, node_id=node_id, sha256=frag_sha[frag_idx], ok=ok))
        return ok

    async def place(frag_idx: int, node_id: str, epoch: int) -> None:
        if await put_one(frag_idx, node_id, epoch):
            acked.add(frag_idx)
            return
        while spares:                                    # the target failed: try the next unused spare
            s = spares.popleft()
            if await put_one(frag_idx, s.node_id, s.epoch):
                acked.add(frag_idx)
                return

    pending = {asyncio.create_task(place(t.frag_idx, t.node_id, t.epoch)) for t in pc.targets}
    while pending and len(acked) < w:
        _, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
    if pending:                                          # W reached: short grace for the rest
        _, pending = await asyncio.wait(pending, timeout=STRAGGLER_GRACE_S)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    commit = CommitChunk(chunk_id=pc.chunk_id, sha256=chunk_sha, frag_size=frag_size, fragments=attempts)
    return ChunkOutcome(commit, len(acked), relayed, len(acked) >= w)


def drained_chunk(plan: UploadPlan, pc: PlanChunk, data: bytes) -> CommitChunk:
    """A chunk read after a quorum failure: reported with its checksum and no fragments."""
    p = plan.policy
    frag_size = -(-len(data) // p.k) if p.type == "erasure" else len(data)
    return CommitChunk(chunk_id=pc.chunk_id, sha256=sha256_hex(data), frag_size=frag_size, fragments=[])


async def put_object(gw: Gateway, bucket: str, key: str, size: int, body: AsyncIterator[bytes]) -> PutResult:
    plan = UploadPlan.model_validate(await meta("POST", "/v1/uploads",
                                                json=UploadRequest(bucket=bucket, key=key, size=size).model_dump()))
    w = plan.policy.w if gw.safety.quorum_writes else 1
    in_flight = max(1, gw.cfg.gateway.chunks_in_flight)
    whole = new_sha256()
    results: list[Optional[CommitChunk]] = [None] * len(plan.chunks)
    tasks: deque[tuple[int, asyncio.Task]] = deque()
    acked = relayed = 0
    failed_idx: Optional[int] = None
    received = 0

    async def settle(idx: int, task: asyncio.Task) -> None:
        nonlocal acked, relayed, failed_idx
        out: ChunkOutcome = await task
        results[idx] = out.commit
        acked += out.acked
        relayed += out.relayed
        if not out.quorum and failed_idx is None:
            failed_idx = idx
            log.warning("%s/%s chunk %d: only %d of %d machines confirmed", bucket, key, idx, out.acked, w)

    async def cut(idx: int, data: bytes) -> None:
        whole.update(data)
        pc = plan.chunks[idx]
        if failed_idx is not None:
            results[idx] = drained_chunk(plan, pc, data)
            return
        tasks.append((idx, asyncio.create_task(store_chunk(gw, plan, pc, data, bucket, key, w))))
        while len(tasks) >= in_flight or (tasks and tasks[0][1].done()):
            await settle(*tasks.popleft())

    buf = bytearray()
    idx = 0
    try:
        async for piece in body:
            received += len(piece)
            if received > size:
                raise VaultHTTPError(400, "bad_length", "The body is longer than Content-Length.")
            buf += piece
            while idx < len(plan.chunks) and len(buf) >= plan.chunks[idx].size:
                n = plan.chunks[idx].size
                data = bytes(buf[:n])
                del buf[:n]
                await cut(idx, data)
                idx += 1
        if received != size:
            raise VaultHTTPError(400, "bad_length", "The body is shorter than Content-Length.",
                                 {"expected": size, "got": received})
        while tasks:
            await settle(*tasks.popleft())
    except BaseException:
        for _, t in tasks:
            t.cancel()
        await asyncio.gather(*(t for _, t in tasks), return_exceptions=True)
        try:
            await meta("POST", f"/v1/uploads/{plan.version_id}/abort", ok=(204,))
        except VaultHTTPError:
            pass
        raise

    req = CommitRequest(object_sha256=whole.hexdigest(), size=size, chunks=[c for c in results if c is not None])
    result = await commit(plan.version_id, req, gw.cfg.gateway.timeout_data_s)
    return PutResult(bucket=bucket, key=key, version_id=result.version_id, seq=result.seq,
                     commit_seq=result.commit_seq, etag=result.etag, size=size, policy=plan.policy.name,
                     stored=PutStored(chunks=len(plan.chunks), fragments_acked=acked, w=w, relayed=relayed))


async def commit(version_id: str, req: CommitRequest, timeout: float) -> CommitResult:
    """Commit is idempotent at metadata, so a network failure is retried. A metadata error is final."""
    last: Optional[VaultHTTPError] = None
    for attempt in range(COMMIT_ATTEMPTS):
        try:
            return CommitResult.model_validate(await meta("POST", f"/v1/uploads/{version_id}/commit",
                                                          json=req.model_dump(), timeout=timeout))
        except VaultHTTPError as e:
            if e.body.error == "quorum_not_met":
                raise VaultHTTPError(503, "quorum_not_met", e.body.message, e.body.detail) from None
            if e.body.error != "metadata_unavailable":
                if e.status < 500 and e.status not in (400, 404, 412):
                    raise VaultHTTPError(503, e.body.error, e.body.message, e.body.detail) from None
                raise
            last = e
            await asyncio.sleep(0.2 * (attempt + 1))
    assert last is not None
    raise last
