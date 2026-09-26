"""Owner: Jaiveer (J9). Shared-Fate Auditor, the §4.12 demo end to end (with the J7 dispatcher on a fake node API):
relabel n3, n5 → power A → files on {n1,n3,n5} drop to IFL 1 (fate.at_risk) → moves raise them to 2 (fate.fixed) →
advice explains why 3 needs another power supply (fate.limited, once) → cutting Power Strip A leaves every file
readable. Also: D1 ec42 advice once and no moves; Naive = report only; GET /v1/fate; cluster-wide version."""
import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vault.brain.jaiveer_auditor import audit
from vault.brain.jaiveer_repair import dispatcher
from vault.common.config import load_config
from vault.common.models import NodeState
from vault.metadata.jaiveer_app import create_app
from vault.tests.test_jaiveer_repair import FakeNodes, _settle

REPO = Path(__file__).resolve().parents[3]
CFG = load_config(str(REPO / "vault.yaml"))
IDS = ["n1", "n2", "n3", "n4", "n5", "n6"]


@pytest.fixture
def c(tmp_path):
    app = create_app(CFG, "meta", db_path=str(tmp_path / "v.db"), start_loops=False)
    with TestClient(app) as client:
        client.app_ = app
        brain = app.state.brain
        brain.started_mono -= 1000
        client.fake = FakeNodes()
        brain.rpc.request = client.fake.request
        for nid in IDS:
            client.post("/v1/nodes/register", json={"node_id": nid, "addr": CFG.addr(nid), "capacity_bytes": 2 ** 31,
                                                     "display_name": nid, "labels": {}})
            client.post(f"/v1/nodes/{nid}/heartbeat", json={"node_id": nid, "epoch": 1, "disk_used": 0,
                                                            "capacity": 2 ** 31, "fragments": 0})
        client.post("/v1/buckets", json={"name": "clinic", "policy": "rep3"})
        client.post("/v1/buckets", json={"name": "archive", "policy": "ec42"})
        yield client


def put(c, key, bucket="clinic"):
    p = c.post("/v1/uploads", json={"bucket": bucket, "key": key, "size": 10}).json()
    ch = p["chunks"][0]
    h = hashlib.sha256(key.encode()).hexdigest()
    frags = [{"frag_idx": t["frag_idx"], "node_id": t["node_id"],
              "sha256": h if bucket == "clinic" else hashlib.sha256(bytes([t["frag_idx"]])).hexdigest(), "ok": True}
             for t in ch["targets"]]
    r = c.post(f"/v1/uploads/{p['version_id']}/commit", json={"object_sha256": h, "size": 10, "chunks": [
        {"chunk_id": ch["chunk_id"], "sha256": h, "frag_size": 10, "fragments": frags}]})
    assert r.status_code == 200, r.text
    return [t["node_id"] for t in ch["targets"]]


def relabel(c, nid, **labels):
    c.post(f"/v1/nodes/{nid}/labels", json={"labels": {**CFG.nodes[nid].labels, **labels}})
    r = c.patch(f"/v1/nodes/{nid}/labels", json={"labels": {**CFG.nodes[nid].labels, **labels}})
    assert r.status_code == 200


def cycle(c, n=4):
    """audit → dispatch moves → settle, n times."""
    brain = c.app_.state.brain
    d = dispatcher(brain)
    for _ in range(n):
        c.portal.call(audit, brain)
        c.portal.call(d.dispatch_once)
        c.portal.call(_settle, d)
    return c.portal.call(audit, brain)


def events(c, t):
    return [e for e in c.get("/v1/events", params={"limit": 1000}).json()["events"] if e["type"] == t]


def test_healthy_cluster(c):
    for i in range(10):
        put(c, f"x{i}")
    rep = c.get("/v1/fate").json()
    assert rep["files"]["histogram"] == {"3": 10} and rep["files"]["at_risk"] == []
    assert rep["fate_keys"] == ["power", "switch", "disk_batch"]
    assert rep["cluster_wide"][0]["key"] == "version" and "1.0" in rep["cluster_wide"][0]["human"]
    assert len(events(c, "fate.cluster_wide")) == 1
    assert rep["advice"] == []


def test_demo_relabel_end_to_end(c):
    placed = [put(c, f"xray-{i}") for i in range(20)]
    triple = [p for p in placed if set(p) == {"n1", "n3", "n5"}]
    assert triple, "some files should be on {n1,n3,n5}"
    relabel(c, "n3", power="A")
    relabel(c, "n5", power="A")
    rep = c.portal.call(audit, c.app_.state.brain)
    assert rep["files"]["histogram"].get("1") if isinstance(rep, dict) else rep.files.histogram.get("1") == len(triple)
    at = events(c, "fate.at_risk")
    assert len(at) == 1 and "Power Strip A" in at[0]["human"] and f"{len(triple)} files" in at[0]["human"]

    rep = cycle(c, 8)
    assert rep.files.histogram.get("1", 0) == 0                      # moves raised them…
    assert rep.files.histogram.get("2", 0) == len(triple)            # …to 2 (3 needs another power supply)
    assert len(events(c, "fate.fixed")) == 1
    adv = events(c, "fate.limited")
    assert len(adv) == 1                                              # once, not per file / per audit
    assert adv[0]["human"] == ("Power Strip A feeds 4 of 6 machines. Give Lab Laptop or Records Room its own "
                               "power supply to get 3 independent copies.")
    assert rep.advice[0].human == adv[0]["human"]

    # every move was make-before-break: pulled to the new machine, then deleted from the old one
    pulls = [x for x in c.fake.calls if x[2].endswith("/pull")]
    deletes = [x for x in c.fake.calls if x[1] == "DELETE"]
    assert len(pulls) == len(deletes) == len(triple)

    # cut Power Strip A: every file still has a copy elsewhere
    for nid in ("n1", "n2", "n3", "n5"):
        c.app_.state.membership.members[nid].view.state = NodeState.DOWN
    c.app_.state.brain.invalidate_snapshot()
    s = c.get("/v1/cluster").json()["summary"]
    assert s["unreadable_files"] == 0 and s["level"] != "critical"


def test_ec42_d1_advice_once_no_moves(c):
    for i in range(3):
        put(c, f"a{i}", bucket="archive")
    rep = cycle(c, 2)
    assert rep.files.histogram == {"2": 3}                            # honest: ec42 on 6 machines is IFL 2
    assert not [x for x in c.fake.calls if x[2].endswith("/pull")]  # no churn
    adv = events(c, "fate.limited")
    assert len(adv) == 1
    assert adv[0]["human"] == ("Archive files are split into 6 pieces across all 6 machines, and each power strip "
                               "feeds 2 of them. They survive losing one power strip, but not two. To survive two, "
                               "give each machine its own power supply.")
    cycle(c, 2)
    assert len(events(c, "fate.limited")) == 1                        # deduplicated until the layout changes


def test_naive_reports_but_never_moves(c):
    placed = [put(c, f"n{i}") for i in range(20)]
    c.post("/v1/mode", json={"mode": "naive"})
    relabel(c, "n3", power="A")
    relabel(c, "n5", power="A")
    rep = cycle(c, 2)
    assert rep.files.histogram.get("1", 0) >= 1
    assert not [x for x in c.fake.calls if x[2].endswith("/pull")]


def test_object_health_after_relabel(c):
    placed = [put(c, f"h{i}") for i in range(20)]
    key = next(f"h{i}" for i, p in enumerate(placed) if set(p) == {"n1", "n3", "n5"})
    relabel(c, "n3", power="A")
    relabel(c, "n5", power="A")
    h = c.get(f"/v1/objects/clinic/{key}/health").json()
    assert h["ifl"] == 1 and h["min_cut"] == [{"key": "power", "value": "A"}]
