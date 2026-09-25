"""Owner: Jaiveer (J5). Cover: commit rejects < W ok fragments on distinct non-dead nodes; seq/commit_seq
monotonic; old version → superseded; tombstone delete; expired upload → 410; kill -9 durability (optional).

Also: plan shape (targets + spares + epochs, fate-aware), 503 not_enough_machines, 404 no_bucket, spares,
"never trust a count" (same node twice, dead node, wrong sha, unknown node), under-replication → repair job,
idempotent commit retry, abort, EC (ec42, W=5), Naive (W=1, pure ring), manifest, list, inspect, health (IFL),
everything survives a restart.
"""
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vault.common.config import load_config
from vault.common.models import NodeState
from vault.metadata.jaiveer_app import create_app

REPO = Path(__file__).resolve().parents[3]
CFG = load_config(str(REPO / "vault.yaml"))
MiB = 1024 * 1024
INDEPENDENT = [{"n1", "n3", "n5"}, {"n2", "n4", "n6"}]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def bring_up(c, ids=("n1", "n2", "n3", "n4", "n5", "n6")):
    for nid in ids:
        c.post("/v1/nodes/register", json={"node_id": nid, "addr": CFG.addr(nid), "capacity_bytes": 2 ** 31,
                                           "display_name": nid, "labels": {}})
        c.post(f"/v1/nodes/{nid}/heartbeat", json={"node_id": nid, "epoch": 1, "disk_used": 0,
                                                   "capacity": 2 ** 31, "fragments": 0})


@pytest.fixture
def dbpath(tmp_path):
    return str(tmp_path / "vault.db")


@pytest.fixture
def c(dbpath):
    app = create_app(CFG, "meta", db_path=dbpath, start_loops=False)
    with TestClient(app) as client:
        client.app_ = app
        bring_up(client)
        client.post("/v1/buckets", json={"name": "clinic", "policy": "rep3"})
        client.post("/v1/buckets", json={"name": "archive", "policy": "ec42"})
        yield client


def plan(c, key="a.txt", size=100, bucket="clinic"):
    r = c.post("/v1/uploads", json={"bucket": bucket, "key": key, "size": size})
    assert r.status_code == 200, r.text
    return r.json()


def report(p, data: bytes = b"x", ok=None, override=None):
    """CommitRequest where every planned target reports ok (replication), unless `ok` says otherwise.
    ok: {frag_idx: bool}. override: {frag_idx: dict} to change node/sha per fragment."""
    ok = ok or {}
    override = override or {}
    chunks = []
    for ch in p["chunks"]:
        csha = sha(data + bytes([ch["idx"]]))
        frags = []
        for t in ch["targets"]:
            f = {"frag_idx": t["frag_idx"], "node_id": t["node_id"], "sha256": csha, "ok": ok.get(t["frag_idx"], True)}
            f.update(override.get(t["frag_idx"], {}))
            frags.append(f)
        chunks.append({"chunk_id": ch["chunk_id"], "sha256": csha, "frag_size": ch["size"], "fragments": frags})
    return {"object_sha256": sha(data), "size": sum(ch["size"] for ch in p["chunks"]), "chunks": chunks}


def put(c, key="a.txt", size=100, data=b"x", bucket="clinic", **kw):
    p = plan(c, key, size, bucket)
    r = c.post(f"/v1/uploads/{p['version_id']}/commit", json=report(p, data, **kw))
    return p, r


def jobs(c):
    return c.portal.call(c.app_.state.db.fetchall, "SELECT * FROM jobs ORDER BY id")


def version_state(c, vid):
    return c.portal.call(c.app_.state.db.fetchone, "SELECT state FROM versions WHERE version_id=?", (vid,))["state"]


# ── plan ──

def test_plan_shape_rep3(c):
    p = plan(c, size=2 * MiB + 5)
    assert p["version_id"].startswith("v_") and p["chunk_size"] == MiB and p["policy"]["name"] == "rep3"
    assert [ch["size"] for ch in p["chunks"]] == [MiB, MiB, 5]
    for ch in p["chunks"]:
        targets = [t["node_id"] for t in ch["targets"]]
        assert [t["frag_idx"] for t in ch["targets"]] == [0, 1, 2]
        assert set(targets) in INDEPENDENT                          # fate-aware
        assert len(ch["spares"]) == 2 and not set(s["node_id"] for s in ch["spares"]) & set(targets)
        assert all(t["epoch"] == 1 and t["addr"] == CFG.addr(t["node_id"]) for t in ch["targets"])


def test_plan_zero_size_and_errors(c):
    assert plan(c, size=0)["chunks"] == []
    r = c.post("/v1/uploads", json={"bucket": "nope", "key": "k", "size": 1})
    assert r.status_code == 404 and r.json()["error"] == "no_bucket"
    assert c.post("/v1/uploads", json={"bucket": "clinic", "key": "k", "size": -1}).status_code == 400


def test_not_enough_machines(dbpath):
    app = create_app(CFG, "meta", db_path=dbpath, start_loops=False)
    with TestClient(app) as c:
        bring_up(c, ["n1"])
        c.post("/v1/buckets", json={"name": "clinic", "policy": "rep3"})
        r = c.post("/v1/uploads", json={"bucket": "clinic", "key": "k", "size": 10})
        assert r.status_code == 503 and r.json()["error"] == "not_enough_machines"
        bring_up(c, ["n2"])                                          # 2 machines ≥ W=2: allowed, 2 targets
        p = plan(c, size=10)
        assert len(p["chunks"][0]["targets"]) == 2 and p["chunks"][0]["spares"] == []


# ── commit ──

def test_commit_happy_path(c):
    p, r = put(c, size=100)
    assert r.status_code == 200
    res = r.json()
    assert res["seq"] == 1 and res["commit_seq"] == 1 and res["etag"] == sha(b"x") and res["under_replicated_chunks"] == 0
    m = c.get("/v1/objects/clinic/a.txt").json()
    assert m["version_id"] == p["version_id"] and m["sha256"] == sha(b"x") and m["policy"]["n"] == 3
    frs = m["chunks"][0]["fragments"]
    assert len(frs) == 3 and all(f["state"] == "ok" and f["route"] == "direct" and f["addr"] for f in frs)
    assert jobs(c) == []


def test_two_of_three_commits_and_queues_repair(c):
    p, r = put(c, ok={2: False})
    assert r.status_code == 200 and r.json()["under_replicated_chunks"] == 1
    js = jobs(c)
    assert len(js) == 1 and js[0]["kind"] == "repair" and js[0]["reason"] == "under_replicated"
    assert js[0]["frag_idx"] == 2 and js[0]["priority"] == 1 and js[0]["chunk_id"] == p["chunks"][0]["chunk_id"]
    assert len(c.get("/v1/objects/clinic/a.txt").json()["chunks"][0]["fragments"]) == 2


def test_quorum_not_met_aborts_and_emits(c):
    p, r = put(c, ok={1: False, 2: False})
    assert r.status_code == 409 and r.json()["error"] == "quorum_not_met"
    assert r.json()["detail"] == {"got": 1, "w": 2, "chunk_idx": 0}
    assert version_state(c, p["version_id"]) == "aborted"
    ev = c.get("/v1/events").json()["events"][-1]
    assert ev["type"] == "write.quorum_failed" and "a.txt" in ev["human"] and "only 1 of 2" in ev["human"]
    assert c.get("/v1/objects/clinic/a.txt").status_code == 404
    r2 = c.post(f"/v1/uploads/{p['version_id']}/commit", json=report(p))
    assert r2.status_code == 410


@pytest.mark.parametrize("override", [
    {1: {"node_id": "PLACEHOLDER_SAME"}},        # same node twice → one distinct node
    {1: {"node_id": "n9"}},                      # unknown node
    {1: {"sha256": "0" * 64}},                   # replication: fragment sha must equal the chunk sha
    {1: {"frag_idx": 7}},                        # out of range
])
def test_never_trust_a_count(c, override):
    p = plan(c)
    t = p["chunks"][0]["targets"]
    if override.get(1, {}).get("node_id") == "PLACEHOLDER_SAME":
        override = {1: {"node_id": t[0]["node_id"]}}
    r = c.post(f"/v1/uploads/{p['version_id']}/commit", json=report(p, ok={2: False}, override=override))
    assert r.status_code == 409 and r.json()["detail"]["got"] == 1


def test_dead_node_fragments_dont_count(c):
    p = plan(c)
    t = p["chunks"][0]["targets"]
    ms = c.app_.state.membership
    ms.members[t[1]["node_id"]].view.state = NodeState.DEAD
    r = c.post(f"/v1/uploads/{p['version_id']}/commit", json=report(p, ok={2: False}))
    assert r.status_code == 409


def test_spare_counts(c):
    p = plan(c)
    ch = p["chunks"][0]
    spare = ch["spares"][0]["node_id"]
    r = c.post(f"/v1/uploads/{p['version_id']}/commit",
               json=report(p, ok={1: False}, override={2: {"node_id": spare}}))
    assert r.status_code == 200 and r.json()["under_replicated_chunks"] == 1
    holders = {f["node_id"] for f in c.get("/v1/objects/clinic/a.txt").json()["chunks"][0]["fragments"]}
    assert spare in holders and ch["targets"][1]["node_id"] not in holders


def test_seq_commit_seq_monotonic_and_supersede(c):
    p1, r1 = put(c, "k1", data=b"one")
    _, r2 = put(c, "k2", data=b"two")
    p3, r3 = put(c, "k1", data=b"three")
    assert [r.json()["commit_seq"] for r in (r1, r2, r3)] == [1, 2, 3]
    assert [r1.json()["seq"], r3.json()["seq"]] == [1, 2] and r2.json()["seq"] == 1
    assert version_state(c, p1["version_id"]) == "superseded"
    m = c.get("/v1/objects/clinic/k1").json()
    assert m["version_id"] == p3["version_id"] and m["sha256"] == sha(b"three") and m["seq"] == 2


def test_idempotent_commit_retry(c):
    p = plan(c)
    body = report(p)
    a = c.post(f"/v1/uploads/{p['version_id']}/commit", json=body).json()
    b = c.post(f"/v1/uploads/{p['version_id']}/commit", json=body).json()
    assert a["seq"] == b["seq"] and a["commit_seq"] == b["commit_seq"] and a["etag"] == b["etag"]


def test_expired_upload_410(c):
    p = plan(c)

    async def age(conn):
        await conn.execute("UPDATE versions SET created_at = created_at - 3600 WHERE version_id=?", (p["version_id"],))
    c.portal.call(c.app_.state.db.write, age)
    r = c.post(f"/v1/uploads/{p['version_id']}/commit", json=report(p))
    assert r.status_code == 410 and r.json()["error"] == "upload_expired"
    assert version_state(c, p["version_id"]) == "aborted"


def test_abort_then_commit_410(c):
    p = plan(c)
    assert c.post(f"/v1/uploads/{p['version_id']}/abort").status_code == 204
    assert c.post(f"/v1/uploads/{p['version_id']}/abort").status_code == 204
    assert c.post(f"/v1/uploads/{p['version_id']}/commit", json=report(p)).status_code == 410
    assert c.post("/v1/uploads/v_nope/commit", json=report(p)).status_code == 404


def test_bad_commit_shapes(c):
    p = plan(c, size=2 * MiB)
    body = report(p)
    assert c.post(f"/v1/uploads/{p['version_id']}/commit", json={**body, "size": 5}).json()["error"] == "size_mismatch"
    assert c.post(f"/v1/uploads/{p['version_id']}/commit",
                  json={**body, "chunks": body["chunks"][:1]}).json()["error"] == "bad_commit"


def test_empty_object(c):
    p = plan(c, "empty", size=0)
    r = c.post(f"/v1/uploads/{p['version_id']}/commit", json={"object_sha256": sha(b""), "size": 0, "chunks": []})
    assert r.status_code == 200
    m = c.get("/v1/objects/clinic/empty").json()
    assert m["size"] == 0 and m["chunks"] == []


# ── EC ──

def ec_report(p, ok_count=6):
    chunks = []
    for ch in p["chunks"]:
        frags = [{"frag_idx": t["frag_idx"], "node_id": t["node_id"], "sha256": sha(bytes([t["frag_idx"]])),
                  "ok": i < ok_count} for i, t in enumerate(ch["targets"])]
        chunks.append({"chunk_id": ch["chunk_id"], "sha256": sha(b"chunk"), "frag_size": 25, "fragments": frags})
    return {"object_sha256": sha(b"obj"), "size": 100, "chunks": chunks}


def test_ec42(c):
    p = plan(c, "e", 100, "archive")
    assert p["policy"]["k"] == 4 and len(p["chunks"][0]["targets"]) == 6 and p["chunks"][0]["spares"] == []
    r = c.post(f"/v1/uploads/{p['version_id']}/commit", json=ec_report(p, 5))
    assert r.status_code == 200 and r.json()["under_replicated_chunks"] == 1
    js = jobs(c)
    assert len(js) == 1 and js[0]["priority"] == 1                    # durable 5 > k=4
    frs = c.get("/v1/objects/archive/e").json()["chunks"][0]["fragments"]
    assert sorted(f["frag_idx"] for f in frs) == [0, 1, 2, 3, 4]
    assert len({f["sha256"] for f in frs}) == 5                        # EC: each fragment its own sha
    p2 = plan(c, "e2", 100, "archive")
    assert c.post(f"/v1/uploads/{p2['version_id']}/commit", json=ec_report(p2, 4)).status_code == 409   # W=5


# ── Naive mode ──

def test_naive_w1_and_pure_ring(c):
    c.post("/v1/mode", json={"mode": "naive"})
    p, r = put(c, ok={1: False, 2: False})
    assert r.status_code == 200 and r.json()["under_replicated_chunks"] == 1
    triples = set()
    for i in range(60):
        pl = plan(c, f"n{i}")
        triples.add(frozenset(t["node_id"] for t in pl["chunks"][0]["targets"]))
    assert len(triples) > 2                                           # not only the 2 independent triples


# ── delete / list ──

def test_delete_tombstone(c):
    put(c, "d")
    r = c.delete("/v1/objects/clinic/d").json()
    assert r == {"deleted": True, "commit_seq": 2}
    assert c.get("/v1/objects/clinic/d").status_code == 404
    assert c.delete("/v1/objects/clinic/d").json() == {"deleted": False, "commit_seq": None}
    assert c.delete("/v1/objects/clinic/never").json() == {"deleted": False, "commit_seq": None}
    assert c.delete("/v1/objects/nope/x").status_code == 404
    _, r = put(c, "d", data=b"again")
    assert r.json()["seq"] == 3 and r.json()["commit_seq"] == 3        # tombstone took seq 2


def test_list_with_prefix_and_nested_keys(c):
    for k in ["xray/1.png", "xray/2.png", "notes.txt", "x_y"]:
        put(c, k)
    c.delete("/v1/objects/clinic/notes.txt")
    lst = c.get("/v1/objects/clinic").json()
    assert lst["bucket"] == "clinic" and [o["key"] for o in lst["objects"]] == ["x_y", "xray/1.png", "xray/2.png"]
    o = lst["objects"][1]
    assert o["policy"] == "rep3" and o["size"] == 100 and o["etag"] == sha(b"x") and o["updated_at"] > 0
    assert [o["key"] for o in c.get("/v1/objects/clinic", params={"prefix": "xray/"}).json()["objects"]] == \
        ["xray/1.png", "xray/2.png"]
    assert [o["key"] for o in c.get("/v1/objects/clinic", params={"prefix": "x_"}).json()["objects"]] == ["x_y"]
    assert c.get("/v1/objects/clinic/xray/2.png").json()["key"] == "xray/2.png"
    assert c.get("/v1/objects/nope").status_code == 404


# ── inspect / health ──

def test_inspect_objects(c):
    for i in range(5):
        put(c, f"k{i}")
    put(c, "under", ok={2: False})
    c.delete("/v1/objects/clinic/k0")
    page = c.get("/v1/inspect/objects", params={"limit": 4}).json()
    assert len(page["objects"]) == 4 and page["next_cursor"] == "clinic/k3"
    rest = c.get("/v1/inspect/objects", params={"cursor": page["next_cursor"], "limit": 4}).json()
    assert rest["next_cursor"] is None
    allo = {o["key"]: o for o in page["objects"] + rest["objects"]}
    assert allo["k0"]["state"] == "deleted"
    assert allo["k1"]["state"] == "live" and allo["k1"]["durable_min"] == 3 and allo["k1"]["ifl"] == 3
    assert allo["under"]["durable_min"] == 2 and allo["under"]["target"] == 3


def test_health_ifl_and_demo_relabel(c):
    p, _ = put(c, "h")
    h = c.get("/v1/objects/clinic/h/health").json()
    assert h["ifl"] == 3 and h["target"] == 3 and h["chunks"][0]["durable"] == 3 and h["chunks"][0]["available"] == 3
    holders = {t["node_id"] for t in p["chunks"][0]["targets"]}
    others = sorted(holders - {"n1", "n2"})                             # the two holders not on Power Strip A
    for nid in others:
        c.patch(f"/v1/nodes/{nid}/labels", json={"labels": {**CFG.nodes[nid].labels, "power": "A"}})
    h = c.get("/v1/objects/clinic/h/health").json()
    assert h["ifl"] == 1 and h["min_cut"] == [{"key": "power", "value": "A"}]
    assert {"key": "power", "value": "A"} in h["shared"]
    assert c.get("/v1/objects/clinic/missing/health").status_code == 404


# ── restart ──

def test_commits_survive_restart(dbpath):
    app = create_app(CFG, "meta", db_path=dbpath, start_loops=False)
    with TestClient(app) as c:
        bring_up(c)
        c.post("/v1/buckets", json={"name": "clinic", "policy": "rep3"})
        for i in range(3):
            put(c, f"k{i}", data=bytes([i]))
    app2 = create_app(CFG, "meta", db_path=dbpath, start_loops=False)
    with TestClient(app2) as c:
        ev = c.get("/v1/events").json()["events"]
        rec = [e for e in ev if e["type"] == "meta.recovered"][-1]
        assert rec["human"].startswith("Vault's index recovered 3 files")
        assert c.get("/v1/objects/clinic/k2").json()["sha256"] == sha(bytes([2]))
        bring_up(c)
        _, r = put(c, "k3")
        assert r.json()["commit_seq"] == 4                              # commit_seq continues after restart
