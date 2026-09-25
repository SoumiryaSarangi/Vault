"""Owner: Soum (S5). Gateway end to end, in process: real node apps (n1–n6), a small fake metadata that follows
the J5 contract, and the gateway, wired by an httpx transport that routes by port (a "down" machine refuses
connections). Covers: rep3 multi-chunk round trip, empty object, spare on a dead target, quorum failure (commit
still posted, every chunk reported → 503 quorum_not_met), read failover on a damaged copy, ec42 round trip and
ec-decode with a machine down, 503 unavailable, 411."""
import contextlib
import os
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from vault.common import ids
from vault.common.config import load_config
from vault.common.hashing import sha256_hex
from vault.common.models import (CommitRequest, CommitResult, FragLoc, FragmentReport, Manifest, ManifestChunk,
                                 PlanChunk, SpareRef, TargetRef, UploadPlan, UploadRequest)
from vault.common.rpc import get_rpc
from vault.gateway.soum_app import create_app as create_gateway
from vault.node.soum_app import create_app as create_node

CHUNK = 64 * 1024
NODES = [f"n{i}" for i in range(1, 7)]


def err(status: int, code: str, **detail):
    return JSONResponse({"error": code, "message": code, "detail": detail}, status_code=status)


def fake_meta(cfg, state: dict) -> FastAPI:
    """Just enough of Jaiveer's J5 metadata: plan (targets in state['order']), commit (≥W distinct frag_idx on
    distinct nodes, else 409), abort, manifest, reports, config."""
    app = FastAPI()
    buckets = {"clinic": cfg.policy("rep3"), "archive": cfg.policy("ec42")}

    @app.post("/v1/uploads")
    async def begin(req: UploadRequest):
        if req.bucket not in buckets:
            return err(404, "no_bucket")
        pol = buckets[req.bucket]
        vid = ids.new_version_id()
        order = state["order"]
        chunks = []
        for idx in range(-(-req.size // CHUNK)):
            cid = ids.chunk_id(vid, idx)
            chunks.append(PlanChunk(chunk_id=cid, idx=idx, size=min(CHUNK, req.size - idx * CHUNK),
                                    targets=[TargetRef(frag_idx=i, node_id=n, addr=cfg.addr(n), epoch=1)
                                             for i, n in enumerate(order[:pol.n])],
                                    spares=[SpareRef(node_id=n, addr=cfg.addr(n), epoch=1)
                                            for n in order[pol.n:pol.n + 2]]))
        state["uploads"][vid] = (req, pol)
        return UploadPlan(version_id=vid, chunk_size=CHUNK, policy=pol, chunks=chunks)

    @app.post("/v1/uploads/{vid}/commit")
    async def commit(vid: str, req: CommitRequest):
        up, pol = state["uploads"][vid]
        state["commits"].append(req)
        chunks = []
        for ch in req.chunks:
            ok = {}
            for f in ch.fragments:
                if f.ok and f.frag_idx not in ok and f.node_id not in ok.values():
                    ok[f.frag_idx] = f.node_id
            if len(ok) < pol.w:
                return err(409, "quorum_not_met", got=len(ok), w=pol.w)
            sha = {f.frag_idx: f.sha256 for f in ch.fragments}
            chunks.append(ManifestChunk(chunk_id=ch.chunk_id, idx=int(ch.chunk_id.rsplit("_", 1)[1]),
                                        size=min(CHUNK, up.size - int(ch.chunk_id.rsplit("_", 1)[1]) * CHUNK),
                                        sha256=ch.sha256, frag_size=ch.frag_size,
                                        fragments=[FragLoc(frag_idx=i, node_id=n, addr=cfg.addr(n), state="ok",
                                                           sha256=sha[i]) for i, n in sorted(ok.items())]))
        state["seq"] += 1
        state["objects"][(up.bucket, up.key)] = Manifest(
            bucket=up.bucket, key=up.key, version_id=vid, seq=1, commit_seq=state["seq"], size=up.size,
            sha256=req.object_sha256, policy=pol, chunk_size=CHUNK, chunks=chunks)
        return CommitResult(version_id=vid, seq=1, commit_seq=state["seq"], etag=req.object_sha256)

    @app.post("/v1/uploads/{vid}/abort")
    async def abort(vid: str):
        state["aborted"].append(vid)
        return Response(status_code=204)

    @app.get("/v1/objects/{bucket}/{key:path}")
    async def manifest(bucket: str, key: str):
        m = state["objects"].get((bucket, key))
        return m if m else err(404, "not_found")

    @app.post("/v1/reports/fragment", status_code=202)
    async def reports(r: FragmentReport):
        state["reports"].append(r)
        return {}

    return app


class Dispatch(httpx.AsyncBaseTransport):
    """Route by port to in-process ASGI apps; ports in `down` refuse connections."""

    def __init__(self, apps: dict[int, FastAPI]):
        self.apps = {p: httpx.ASGITransport(app=a) for p, a in apps.items()}
        self.down: set[int] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.port in self.down or request.url.port not in self.apps:
            raise httpx.ConnectError("refused", request=request)
        return await self.apps[request.url.port].handle_async_request(request)


@pytest.fixture
async def cluster(tmp_path):
    cfg = load_config("vault.yaml")
    cfg.cluster.data_dir = str(tmp_path)
    state = {"order": list(NODES), "uploads": {}, "commits": [], "aborted": [], "objects": {}, "reports": [],
             "seq": 0}
    nodes = {n: create_node(cfg, n, start_loops=False) for n in NODES}
    gw = create_gateway(cfg, "gw", start_loops=False)
    apps = {cfg.port(n): a for n, a in nodes.items()}
    apps[cfg.port("meta")] = fake_meta(cfg, state)
    async with contextlib.AsyncExitStack() as stack:
        for n, a in nodes.items():
            await stack.enter_async_context(a.router.lifespan_context(a))
            a.state.node.epoch, a.state.node.lease_expiry = 1, time.time() + 600
        await stack.enter_async_context(gw.router.lifespan_context(gw))    # last: the process rpc is the gateway's
        dispatch = Dispatch(apps)
        rpc = get_rpc()
        await rpc.client.aclose()
        rpc.client = httpx.AsyncClient(transport=dispatch, timeout=5)
        rpc.relay_enabled = False
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gw), base_url="http://gw", timeout=30)
        yield {"client": client, "state": state, "nodes": nodes, "dispatch": dispatch, "cfg": cfg, "gw": gw}
        await client.aclose()


def down(c, *pids):
    c["dispatch"].down.update(c["cfg"].port(p) for p in pids)


def stored_on(c, fid_prefix: str) -> set[str]:
    return {n for n, a in c["nodes"].items() if any(f.startswith(fid_prefix) for f in a.state.node.storage._index)}


async def test_rep3_round_trip_multi_chunk(cluster):
    c = cluster["client"]
    data = os.urandom(3 * CHUNK + 1234)
    r = await c.put("/clinic/xray-0001.png", content=data)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["etag"] == sha256_hex(data) == r.headers["etag"]
    assert res["stored"] == {"chunks": 4, "fragments_acked": 12, "w": 2, "relayed": 0}
    assert r.headers["x-vault-commit-seq"] == str(res["commit_seq"])
    assert stored_on(cluster, res["version_id"]) == {"n1", "n2", "n3"}
    g = await c.get("/clinic/xray-0001.png")
    assert g.status_code == 200 and g.content == data
    assert g.headers["x-vault-read-path"] == "direct" and g.headers["etag"] == res["etag"]
    h = await c.head("/clinic/xray-0001.png")
    assert h.status_code == 200 and h.headers["content-length"] == str(len(data))


async def test_empty_object(cluster):
    c = cluster["client"]
    r = await c.put("/clinic/empty.txt", content=b"")
    assert r.status_code == 200 and r.json()["stored"]["chunks"] == 0
    g = await c.get("/clinic/empty.txt")
    assert g.status_code == 200 and g.content == b""


async def test_missing_object_and_bucket(cluster):
    c = cluster["client"]
    assert (await c.get("/clinic/nope")).json()["error"] == "not_found"
    r = await c.put("/nobucket/x", content=b"abc")
    assert r.status_code == 404 and r.json()["error"] == "no_bucket"
    assert (await c.head("/clinic/nope")).status_code == 404


async def test_dead_target_uses_spare(cluster):
    c = cluster["client"]
    down(cluster, "n2")
    data = os.urandom(CHUNK + 10)
    r = await c.put("/clinic/spare.bin", content=data)
    assert r.status_code == 200, r.text
    assert r.json()["stored"]["fragments_acked"] == 6
    commit = cluster["state"]["commits"][-1]
    frags = [(f.frag_idx, f.node_id, f.ok) for f in commit.chunks[0].fragments]
    assert (1, "n2", False) in frags and (1, "n4", True) in frags
    assert (await c.get("/clinic/spare.bin")).content == data


async def test_quorum_failure_posts_commit_with_every_chunk(cluster):
    c = cluster["client"]
    down(cluster, "n2", "n3", "n4", "n5", "n6")          # only n1 left: 1 < W=2
    data = os.urandom(3 * CHUNK)
    r = await c.put("/clinic/fail.bin", content=data)
    assert r.status_code == 503 and r.json()["error"] == "quorum_not_met"
    commit = cluster["state"]["commits"][-1]
    assert len(commit.chunks) == 3 and commit.object_sha256 == sha256_hex(data)
    assert cluster["state"]["aborted"] == []              # metadata aborts it (and emits the event)
    assert (await c.get("/clinic/fail.bin")).status_code == 404


async def test_read_failover_on_damaged_copy(cluster):
    c = cluster["client"]
    data = os.urandom(CHUNK // 2)
    res = (await c.put("/clinic/lab.pdf", content=data)).json()
    fid = ids.fid(ids.chunk_id(res["version_id"], 0), 0)            # frag 0 on n1 = first holder
    node = cluster["nodes"]["n1"].state.node
    path = node.storage.entry(fid).path
    path.write_bytes(path.read_bytes()[:-1] + b"\x00")
    g = await c.get("/clinic/lab.pdf")
    assert g.status_code == 200 and g.content == data
    assert g.headers["x-vault-read-path"] == "failover:n2"
    reports = [(r.fid, r.node_id, r.problem) for r in cluster["state"]["reports"]]
    assert (fid, "n1", "corrupt") in reports
    assert dict(cluster["gw"].state.gw.stats.failover) == {"n1": 1}


async def test_unavailable_when_no_copy_reachable(cluster):
    c = cluster["client"]
    data = os.urandom(1000)
    assert (await c.put("/clinic/x.bin", content=data)).status_code == 200
    down(cluster, "n1", "n2", "n3")
    g = await c.get("/clinic/x.bin")
    assert g.status_code == 503 and g.json()["error"] == "unavailable"


async def test_ec42_round_trip_and_decode_with_machine_down(cluster):
    c = cluster["client"]
    data = os.urandom(2 * CHUNK + 777)
    r = await c.put("/archive/scan.tiff", content=data)
    assert r.status_code == 200, r.text
    assert r.json()["stored"]["fragments_acked"] == 18 and stored_on(cluster, r.json()["version_id"]) == set(NODES)
    g = await c.get("/archive/scan.tiff")
    assert g.content == data and g.headers["x-vault-read-path"] == "direct"
    down(cluster, "n2")                                    # data fragment 1 gone → decode from parity
    g = await c.get("/archive/scan.tiff")
    assert g.status_code == 200 and g.content == data and g.headers["x-vault-read-path"] == "ec-decode"


async def test_content_length_required(cluster):
    c = cluster["client"]

    async def gen():
        yield b"abc"
    r = await c.put("/clinic/stream.bin", content=gen())
    assert r.status_code == 411 and r.json()["error"] == "length_required"
