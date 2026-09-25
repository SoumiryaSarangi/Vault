"""POST /v1/fragments/{fid}/pull: copy (verify, store) or ec_rebuild (fetch k, decode, re-encode one). §4.8 step 3. Task S4.
Owner: Soum.

Repair is pull-based (D8): metadata tells the TARGET to fetch from verified sources, the target checks
the bytes against expected_sha256 before storing, and only then answers 201.
  * copy: try sources in order; the first whose bytes hash to expected_sha256 wins.
  * ec_rebuild: fetch the other fragments in parallel, keep those whose bytes match the sha in their own
    X-Vault-Meta, rebuild `ec.frag_idx` from k of them, check it against expected_sha256.
  * A source that returns bytes that fail verification is reported to metadata as corrupt (context repair).
  * Fenced → 503 (a fenced node takes no writes); already stored and intact → 201 (idempotent retry).
"""
import asyncio
import time
from typing import Awaitable, Callable, Optional

from vault.common import ids
from vault.common.hashing import sha256_hex
from vault.common.models import PullRequest, PullResult, PullSource
from vault.common.rpc import NetworkError, get_rpc
from vault.common.service import VaultHTTPError
from vault.common.soum_ec import ec_rebuild
from vault.node.soum_storage import NotFound, decode_meta

# fetch(source) → (status, body, headers); raises NetworkError when unreachable
Fetch = Callable[[PullSource], Awaitable[tuple[int, bytes, dict[str, str]]]]


def rpc_fetch(timeout_s: float) -> Fetch:
    async def fetch(src: PullSource) -> tuple[int, bytes, dict[str, str]]:
        r = await get_rpc().request(src.node_id, "GET", f"/v1/fragments/{src.fid}", timeout=timeout_s)
        return r.status_code, r.content, {k.lower(): v for k, v in r.headers.items()}
    return fetch


async def _try(fetch: Fetch, src: PullSource) -> tuple[str, bytes, dict[str, str]]:
    """→ (outcome, body, headers). outcome: ok | bad (reachable, wrong bytes) | gone (404/409/5xx/network)."""
    try:
        status, body, headers = await fetch(src)
    except NetworkError:
        return "gone", b"", {}
    return ("ok" if status == 200 else "gone"), body, headers


async def pull(node, req: PullRequest, fetch: Optional[Fetch] = None) -> PullResult:
    if req.header.fid != req.fid:
        raise VaultHTTPError(400, "bad_request", "The pull header is for a different fragment.")
    if node.safety.fencing and node.fenced():
        raise VaultHTTPError(503, "fenced", "This machine lost contact with Vault and isn't accepting writes.")
    fetch = fetch or rpc_fetch(node.cfg.gateway.timeout_data_s)
    t0 = time.perf_counter()
    st = node.storage

    if st.has(req.fid):                                      # a retried job: keep the intact copy
        try:
            if await st.verify(req.fid) and st.entry(req.fid).header.frag_sha256 == req.expected_sha256:
                return PullResult(fid=req.fid, sha256=req.expected_sha256, size=st.entry(req.fid).size,
                                  ms=round((time.perf_counter() - t0) * 1000, 1), source_used="local")
        except NotFound:
            pass

    verify = node.safety.verify_on_receive
    tried: list[str] = []
    payload: Optional[bytes] = None
    source_used = ""

    if req.mode == "copy":
        for src in req.sources:
            outcome, body, _ = await _try(fetch, src)
            tried.append(f"{src.node_id}:{outcome}")
            if outcome != "ok":
                continue
            if verify and sha256_hex(body) != req.expected_sha256:
                tried[-1] = f"{src.node_id}:bad"
                node.report(src.fid, "corrupt", "repair", node_id=src.node_id)
                continue
            payload, source_used = body, src.node_id
            break
    else:
        if req.ec is None:
            raise VaultHTTPError(400, "bad_request", "ec_rebuild needs the ec parameters.")
        k, m, want = req.ec.k, req.ec.n - req.ec.k, req.ec.frag_idx
        srcs = [s for s in req.sources if ids.parse_fid(s.fid)[3] != want]
        results = await asyncio.gather(*(_try(fetch, s) for s in srcs))
        frags: dict[int, bytes] = {}
        used: list[str] = []
        for src, (outcome, body, headers) in zip(srcs, results):
            idx = ids.parse_fid(src.fid)[3]
            if outcome == "ok":
                try:
                    expected = decode_meta(headers.get("x-vault-meta", "")).frag_sha256
                except ValueError:
                    expected = headers.get("x-vault-sha256", "")
                if verify and sha256_hex(body) != expected:
                    outcome = "bad"
                    node.report(src.fid, "corrupt", "repair", node_id=src.node_id)
            tried.append(f"{src.node_id}/f{idx}:{outcome}")
            if outcome == "ok" and idx not in frags and len(frags) < k:
                frags[idx] = body
                used.append(src.node_id)
        if len(frags) == k:
            rebuilt = ec_rebuild(frags, k, m, want)
            if not verify or sha256_hex(rebuilt) == req.expected_sha256:
                payload, source_used = rebuilt, "ec:" + ",".join(used)
            else:
                tried.append("rebuilt:bad")

    if payload is None:
        raise VaultHTTPError(424, "no_valid_source", "No source had a verified copy.", {"tried": tried})
    if node.disk_full or st.disk_used() + len(payload) > node.capacity:
        raise VaultHTTPError(507, "disk_full", "This machine's disk is full.")
    await st.put(req.header, payload)
    return PullResult(fid=req.fid, sha256=sha256_hex(payload), size=len(payload),
                      ms=round((time.perf_counter() - t0) * 1000, 1), source_used=source_used)
