"""Owner: Jaiveer (J7). Scheduler scan, dispatcher (repair/move/trim) against a fake node API, limits, backoff,
safety.repair off, fragment reports → repair, incident started/completed, GET /v1/repair, startup sentence."""
import hashlib
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from vault.brain.jaiveer_detector import tick
from vault.brain.jaiveer_repair import dispatcher
from vault.brain.jaiveer_scheduler import scan
from vault.common.config import load_config
from vault.common.models import JobKind, JobReason, NodeState
from vault.metadata.jaiveer_app import create_app

REPO = Path(__file__).resolve().parents[3]
CFG = load_config(str(REPO / "vault.yaml"))
IDS = ["n1", "n2", "n3", "n4", "n5", "n6"]


class FakeNodes:
    """Stands in for rpc.request: pull returns the expected sha (or fails), delete returns 204."""

    def __init__(self):
        self.calls, self.fail_pull, self.bad_sha = [], set(), False

    async def request(self, target, method, path, **kw):
        self.calls.append((target, method, path))
        if path.endswith("/pull"):
            if target in self.fail_pull:
                return httpx.Response(424, json={"error": "no_valid_source"})
            body = kw["json"]
            sha = "0" * 64 if self.bad_sha else body["expected_sha256"]
            return httpx.Response(201, json={"fid": body["fid"], "sha256": sha, "size": 10, "ms": 1.0,
                                             "source_used": body["sources"][0]["node_id"]})
        if method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={})


@pytest.fixture
def c(tmp_path):
    app = create_app(CFG, "meta", db_path=str(tmp_path / "v.db"), start_loops=False)
    with TestClient(app) as client:
        client.app_ = app
        brain = app.state.brain
        brain.started_mono -= 1000
        fake = FakeNodes()
        brain.rpc.request = fake.request
        client.fake = fake
        for nid in IDS:
            client.post("/v1/nodes/register", json={"node_id": nid, "addr": CFG.addr(nid), "capacity_bytes": 2 ** 31,
                                                     "display_name": nid, "labels": {}})
            client.post(f"/v1/nodes/{nid}/heartbeat", json={"node_id": nid, "epoch": 1, "disk_used": 0,
                                                            "capacity": 2 ** 31, "fragments": 0})
        client.post("/v1/buckets", json={"name": "clinic", "policy": "rep3"})
        yield client


def put(c, key):
    p = c.post("/v1/uploads", json={"bucket": "clinic", "key": key, "size": 10}).json()
    ch = p["chunks"][0]
    h = hashlib.sha256(key.encode()).hexdigest()
    c.post(f"/v1/uploads/{p['version_id']}/commit", json={
        "object_sha256": h, "size": 10, "chunks": [{"chunk_id": ch["chunk_id"], "sha256": h, "frag_size": 10,
            "fragments": [{"frag_idx": t["frag_idx"], "node_id": t["node_id"], "sha256": h, "ok": True}
                          for t in ch["targets"]]}]})
    return ch["chunk_id"], [t["node_id"] for t in ch["targets"]]


def drain(c, rounds=20):
    """Run scan + dispatch until nothing is running."""
    brain = c.app_.state.brain
    d = dispatcher(brain)
    for _ in range(rounds):
        c.portal.call(scan, brain)
        c.portal.call(d.dispatch_once)
        c.portal.call(_settle, d)
    c.portal.call(scan, brain)


async def _settle(d):
    import asyncio
    while d.running:
        await asyncio.gather(*d.running.values(), return_exceptions=True)


def kill(c, nid):
    brain = c.app_.state.brain
    m = brain.membership.members[nid]
    m.last_hb = time.monotonic() - 100
    m.last_evidence = None
    brain.membership.set_state(m, NodeState.DOWN)
    c.portal.call(tick, brain, time.monotonic())
    assert brain.node(nid).state == NodeState.DEAD


def ok_rows(c, cid):
    return c.portal.call(c.app_.state.db.fetchall,
                         "SELECT frag_idx, node_id FROM fragments WHERE chunk_id=? AND state='ok' ORDER BY frag_idx",
                         (cid,))


def events(c, *t):
    return [e for e in c.get("/v1/events", params={"limit": 1000}).json()["events"] if e["type"] in t]


def test_kill_then_rebuild_to_three_copies(c):
    files = [put(c, f"f{i}") for i in range(6)]
    victim = files[0][1][0]
    kill(c, victim)
    drain(c)
    for cid, holders in files:
        rows = ok_rows(c, cid)
        assert sorted(r["frag_idx"] for r in rows) == [0, 1, 2]
        assert victim not in {r["node_id"] for r in rows}
    started, done = events(c, "repair.started"), events(c, "repair.completed")
    assert len(started) == 1 and len(done) == 1
    assert "Recovered in" in done[0]["human"] and done[0]["data"]["mttr"] >= done[0]["data"]["d"]
    inc = c.get("/v1/cluster").json()["incident"]
    assert inc["recovered_at"] and inc["remaining_chunks"] == 0 and inc["bytes_repaired"] > 0
    assert c.get("/v1/cluster").json()["summary"]["level"] == "ok"
    pulls = [x for x in c.fake.calls if x[2].endswith("/pull")]
    assert pulls and all(t != victim for t, _, _ in pulls)


def test_repair_off_dispatches_nothing(c):
    cid, holders = put(c, "a")
    c.post("/v1/mode", json={"mode": "naive"})
    kill(c, holders[0])
    drain(c, 3)
    assert not [x for x in c.fake.calls if x[2].endswith("/pull")]
    assert c.get("/v1/repair").json()["queued"]["p1"] == 1


def test_failures_back_off_then_fail(c, monkeypatch):
    import vault.brain.jaiveer_repair as rep
    monkeypatch.setattr(rep, "BACKOFF_S", (0.0, 0.0, 0.0))
    cid, holders = put(c, "b")
    kill(c, holders[0])
    c.fake.fail_pull = set(IDS)
    drain(c, 6)
    js = c.get("/v1/repair").json()
    assert any(j["state"] == "failed" and j["attempts"] == 3 for j in js["recent"])
    assert events(c, "repair.failed")
    assert not c.portal.call(c.app_.state.db.fetchall, "SELECT * FROM fragments WHERE state='incoming'")


def test_bad_sha_from_target_is_not_trusted(c, monkeypatch):
    import vault.brain.jaiveer_repair as rep
    monkeypatch.setattr(rep, "BACKOFF_S", (0.0, 0.0, 0.0))
    cid, holders = put(c, "c")
    kill(c, holders[0])
    c.fake.bad_sha = True
    drain(c, 5)
    assert len(ok_rows(c, cid)) == 2


def test_corrupt_report_repaired_from_verified_copy(c):
    cid, holders = put(c, "d")
    fid = f"{cid}_f1"
    r = c.post("/v1/reports/fragment", json={"fid": fid, "node_id": holders[1], "problem": "corrupt",
                                             "observed_by": "gw", "context": "read"})
    assert r.status_code == 202
    ev = events(c, "fragment.corrupt")
    assert len(ev) == 1 and "d" in ev[0]["human"]
    drain(c)
    rows = ok_rows(c, cid)
    assert sorted(r["frag_idx"] for r in rows) == [0, 1, 2]
    assert not c.portal.call(c.app_.state.db.fetchall, "SELECT * FROM fragments WHERE state='corrupt'")
    pull = [x for x in c.fake.calls if x[2].endswith("/pull")]
    assert pull and pull[0][2] == f"/v1/fragments/{fid}/pull"
    assert events(c, "repair.completed")


def test_limits_and_move_and_trim(c):
    brain = c.app_.state.brain
    d = dispatcher(brain)
    cid, holders = put(c, "e")
    others = [n for n in IDS if n not in holders]

    async def enq():
        await brain.enqueue_job(JobKind.move, JobReason.fate, chunk_id=cid, frag_idx=0, priority=2,
                                source_node=holders[0], target_node=others[0])
    c.portal.call(enq)
    drain(c, 3)
    nodes = {r["node_id"] for r in ok_rows(c, cid)}
    assert others[0] in nodes and holders[0] not in nodes          # make-before-break: pulled, then trimmed
    assert (holders[0], "DELETE", f"/v1/fragments/{cid}_f0") in c.fake.calls
    assert d.per_node == {} or all(v == 0 for v in d.per_node.values())


def test_startup_sentence(tmp_path):
    app = create_app(CFG, "meta", db_path=str(tmp_path / "s.db"), start_loops=False)
    with TestClient(app) as c:
        s = c.get("/v1/cluster").json()["summary"]["human"]
        assert s == "Vault is starting: waiting for machines to check in (0 of 6 so far)."


# ── J8: metrics / incidents ──

def test_metrics_after_kill_and_rebuild(c):
    files = [put(c, f"m{i}") for i in range(4)]
    m0 = c.get("/v1/metrics").json()
    assert m0["readable_now_pct"] == 100.0 and m0["overhead"]["cluster"] == 3.0
    assert m0["overhead"]["by_policy"] == {"rep3": 3.0} and m0["ifl"]["histogram"] == {"3": 4}
    assert m0["last_incident"] is None and m0["availability_60s"] == 1.0
    kill(c, files[0][1][0])
    assert c.get("/v1/cluster").json()["summary"]["level"] == "degraded"
    drain(c)
    c.app_.state.brain.invalidate_snapshot()
    assert c.get("/v1/cluster").json()["summary"]["level"] == "ok"
    m = c.get("/v1/metrics").json()
    li = m["last_incident"]
    assert li["mttr_s"] == pytest.approx(li["detect_s"] + li["grace_s"] + li["repair_s"], abs=0.2)
    assert li["bytes"] > 0 and m["incidents_avg_mttr_s"] == li["mttr_s"] and m["repair"]["bytes_total"] > 0
    incs = c.get("/v1/incidents").json()["incidents"]
    assert incs[0]["kind"] == "node_dead" and incs[0]["recovered_at"]


def test_availability_and_corrupt_counter(c):
    c.post("/v1/participants/gw/report", json={"reach": {}, "stats": {"window_s": 1, "ok": 9, "failed": 1}})
    assert c.get("/v1/metrics").json()["availability_60s"] == 0.9
    cid, holders = put(c, "z")
    c.post("/v1/reports/fragment", json={"fid": f"{cid}_f0", "node_id": holders[0], "problem": "corrupt",
                                         "observed_by": "n1", "context": "scrub"})
    assert c.get("/v1/metrics").json()["scrub"]["corrupt_found_total"] == 1
