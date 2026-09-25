"""Verified GET: manifest → per chunk fetch + verify (replication failover / EC decode), headers after first chunk verified, reports → read repair. §4.2. Task S5 (S7 adds routing order).
Owner: Soum.

  * Replication: holders in routing order; GET the fragment, check it against chunk.sha256; on a network
    error, 404, 409 corrupt or a checksum mismatch report it (read repair) and try the next holder.
    safety.read_failover off → only the first holder.
  * EC: data fragments 0..k-1 in parallel; all verified → concatenate. Otherwise add parity fragments until
    k are verified → ec_decode. The rebuilt chunk is checked against chunk.sha256.
  * The response starts only after chunk 0 is verified (else 503 unavailable). A later failure aborts the
    stream; Content-Length is set so the client sees a truncated body.
  * X-Vault-Read-Path (of chunk 0): direct | relay:<via> | failover:<node that served> | ec-decode.
"""
import asyncio
import logging
from typing import AsyncIterator, Optional

from vault.common import ids
from vault.common.hashing import sha256_hex
from vault.common.models import FragLoc, FragmentReport, Manifest, ManifestChunk
from vault.common.rpc import NetworkError, get_rpc
from vault.common.soum_ec import ec_decode
from vault.gateway.soum_meta_client import Gateway, meta
from vault.gateway.soum_routing import order_holders

log = logging.getLogger("gateway.get")


class Unreadable(Exception):
    pass


def report(gw: Gateway, fid: str, node_id: str, problem: str) -> None:
    """Read repair: tell metadata, in the background; failures are only logged."""
    body = FragmentReport(fid=fid, node_id=node_id, problem=problem, observed_by=gw.pid, context="read")

    async def send() -> None:
        try:
            await get_rpc().request("meta", "POST", "/v1/reports/fragment", json=body.model_dump())
        except NetworkError as e:
            log.info("report %s %s not delivered: %s", problem, fid, e.reason)
    asyncio.get_running_loop().create_task(send())


async def fetch(gw: Gateway, chunk_id: str, loc: FragLoc, expected_sha: str) -> tuple[Optional[bytes], str]:
    """→ (verified bytes or None, route). Reports every failure."""
    fid = ids.fid(chunk_id, loc.frag_idx)
    try:
        r = await get_rpc().request(loc.node_id, "GET", f"/v1/fragments/{fid}", timeout=gw.cfg.gateway.timeout_data_s)
    except NetworkError:
        report(gw, fid, loc.node_id, "unreachable")
        return None, ""
    route = r.extensions.get("vault_route", "direct")
    if r.status_code == 404:
        report(gw, fid, loc.node_id, "missing")
        return None, route
    if r.status_code != 200:
        if r.status_code == 409:
            report(gw, fid, loc.node_id, "corrupt")
        return None, route
    if gw.safety.verify_on_read and sha256_hex(r.content) != expected_sha:
        log.warning("GET %s from %s: checksum mismatch", fid, loc.node_id)
        report(gw, fid, loc.node_id, "corrupt")
        return None, route
    return r.content, route


async def read_replicated(gw: Gateway, ch: ManifestChunk) -> tuple[bytes, str]:
    holders = order_holders(ch.fragments)
    if not gw.safety.read_failover:
        holders = holders[:1]
    failed: list[str] = []
    for loc in holders:
        data, route = await fetch(gw, ch.chunk_id, loc, ch.sha256)
        if data is not None:
            for node_id in failed:
                gw.stats.failover_read(node_id)
            if failed:
                return data, f"failover:{loc.node_id}"
            return data, route
        failed.append(loc.node_id)
    raise Unreadable(f"chunk {ch.idx}: no verified copy ({', '.join(failed) or 'no holders'})")


async def read_erasure(gw: Gateway, m: Manifest, ch: ManifestChunk) -> tuple[bytes, str]:
    k = m.policy.k or 1
    by_idx: dict[int, list[FragLoc]] = {}
    for loc in order_holders(ch.fragments):
        by_idx.setdefault(loc.frag_idx, []).append(loc)

    async def one(idx: int) -> tuple[int, Optional[bytes], str]:
        for loc in by_idx.get(idx, []):
            data, route = await fetch(gw, ch.chunk_id, loc, loc.sha256 or "")
            if data is not None:
                return idx, data, route
        return idx, None, ""

    got: dict[int, bytes] = {}
    routes: list[str] = []
    for batch in (list(range(k)), [i for i in sorted(by_idx) if i >= k]):
        for idx, data, route in await asyncio.gather(*(one(i) for i in batch if i not in got)):
            if data is not None and len(got) < k:
                got[idx] = data
                routes.append(route)
        if len(got) >= k:
            break
    if len(got) < k:
        raise Unreadable(f"chunk {ch.idx}: only {len(got)} of {k} pieces verified")
    if sorted(got) == list(range(k)):
        data, path = b"".join(got[i] for i in range(k))[:ch.size], next((r for r in routes if r != "direct"), "direct")
    else:
        data, path = ec_decode(got, k, m.policy.n - k, ch.size), "ec-decode"
    if gw.safety.verify_on_read and sha256_hex(data) != ch.sha256:
        raise Unreadable(f"chunk {ch.idx}: rebuilt chunk failed its checksum")
    return data, path


async def read_chunk(gw: Gateway, m: Manifest, ch: ManifestChunk) -> tuple[bytes, str]:
    if m.policy.type == "erasure":
        return await read_erasure(gw, m, ch)
    return await read_replicated(gw, ch)


async def get_manifest(bucket: str, key: str) -> Manifest:
    return Manifest.model_validate(await meta("GET", f"/v1/objects/{bucket}/{key}"))


async def open_object(gw: Gateway, m: Manifest) -> tuple[AsyncIterator[bytes], str]:
    """Verify chunk 0 before anything is sent. → (body iterator, read path of chunk 0). Raises Unreadable."""
    if not m.chunks:
        async def empty() -> AsyncIterator[bytes]:
            return
            yield b""
        return empty(), "direct"
    first, path = await read_chunk(gw, m, m.chunks[0])

    async def body() -> AsyncIterator[bytes]:
        nxt: Optional[asyncio.Task] = None
        try:
            data = first
            for i in range(len(m.chunks)):
                if i + 1 < len(m.chunks):                   # prefetch one chunk ahead
                    nxt = asyncio.create_task(read_chunk(gw, m, m.chunks[i + 1]))
                yield data
                if nxt is not None:
                    data, _ = await nxt
                    nxt = None
        except Unreadable as e:
            log.error("%s/%s: aborting stream after it started: %s", m.bucket, m.key, e)
            raise
        finally:
            if nxt is not None:
                nxt.cancel()
    return body(), path
