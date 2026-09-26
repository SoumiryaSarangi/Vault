"""Owner: Jaiveer (J6). Detector state machine, snapshot, links, reports. Time is driven by hand: heartbeats go
through membership.heartbeat(now=...) and the detector through tick(brain, now), so nothing here sleeps.

Cover: steady heartbeats stay ALIVE; silence → SUSPECT (~1.6–2 s) → DOWN (+confirm 1 s) → DEAD (+dead_after) with
epoch++, fragments lost, incident (fault_at from supervisor ground truth), repair jobs, node.* events; peer evidence →
PARTITIONED, evidence gone → DOWN; startup grace blocks DOWN/DEAD; JOINING that never returns → DOWN; DOWN → ALIVE
after 3 heartbeats in 2 s (recovered_in_grace); DEAD node heartbeat → DEAD reply → re-register with new epoch;
snapshot shape and summary levels (ok / degraded / at_risk / critical, naive prefix); abnormal links with relay;
slow flag; relay route events; participant/fault reports; SSE endpoint is registered.
"""
import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vault.brain.jaiveer_detector import tick
from vault.common.config import load_config
from vault.common.models import Heartbeat, NodeState, Reach
from vault.metadata.jaiveer_app import create_app

REPO = Path(__file__).resolve().parents[3]
CFG = load_config(str(REPO / "vault.yaml"))
IDS = ["n1", "n2", "n3", "n4", "n5", "n6"]


def hb(nid, epoch=1, **kw):
    return Heartbeat(node_id=nid, epoch=epoch, disk_used=0, capacity=2 ** 31, fragments=0, **kw)


@pytest.fixture
def c(tmp_path):
    app = create_app(CFG, "meta", db_path=str(tmp_path / "vault.db"), start_loops=False)
    with TestClient(app) as client:
        client.app_ = app
        brain = app.state.brain
        brain.started_mono -= 1000                    # past the startup grace unless a test says otherwise
        for nid in IDS:
            client.post("/v1/nodes/register", json={"node_id": nid, "addr": CFG.addr(nid), "capacity_bytes": 2 ** 31,
                                                     "display_name": nid, "labels": {}})
        client.post("/v1/buckets", json={"name": "clinic", "policy": "rep3"})
        yield client


class Clock:
    """Synthetic monotonic time; every node heartbeats every 0.5 s until silenced."""

    def __init__(self, c):
        self.c, self.brain = c, c.app_.state.brain
        self.ms = self.brain.membership
        self.t = time.monotonic()
        self.next_hb = self.t
        self.silent: set[str] = set()
        self.reach: dict[str, dict[str, Reach]] = {}     # reach row each node sends with its heartbeats
        self.via: dict[str, str] = {}                    # node → relay its heartbeats arrive through

    def beat(self, nid, **kw):
        kw.setdefault("reach", self.reach.get(nid, {}))
        return self.ms.heartbeat(nid, hb(nid, epoch=self.ms.node(nid).epoch, **kw), self.via.get(nid), self.t)

    def run(self, seconds: float, step: float = 0.1):
        end = self.t + seconds
        while self.t < end - 1e-9:
            if self.t >= self.next_hb - 1e-9:
                for nid in IDS:
                    if nid not in self.silent and self.ms.node(nid).state not in (NodeState.DEAD,):
                        self.beat(nid)
                self.next_hb += 0.5
            self.c.portal.call(tick, self.brain, self.t)
            self.t += step

    def state(self, nid):
        return self.ms.node(nid).state


def events(c, *types):
    evs = c.get("/v1/events", params={"limit": 1000}).json()["events"]
    return [e for e in evs if not types or e["type"] in types]


def put(c, key, holders_ok=None):
    """Upload a 1-chunk object through the real plan/commit path; returns its holders."""
    p = c.post("/v1/uploads", json={"bucket": "clinic", "key": key, "size": 10}).json()
    ch = p["chunks"][0]
    h = hashlib.sha256(key.encode()).hexdigest()
    frags = [{"frag_idx": t["frag_idx"], "node_id": t["node_id"], "sha256": h, "ok": True} for t in ch["targets"]]
    r = c.post(f"/v1/uploads/{p['version_id']}/commit",
               json={"object_sha256": h, "size": 10, "chunks": [{"chunk_id": ch["chunk_id"], "sha256": h,
                                                                  "frag_size": 10, "fragments": frags}]})
    assert r.status_code == 200, r.text
    return [t["node_id"] for t in ch["targets"]]


# ── state machine ──

def test_steady_heartbeats_stay_alive(c):
    clk = Clock(c)
    clk.run(10)
    assert all(clk.state(n) == NodeState.ALIVE for n in IDS)
    assert all(clk.ms.node(n).phi < 2 for n in IDS)


def test_kill_suspect_down_dead(c):
    clk = Clock(c)
    clk.run(5)
    holders = put(c, "xray.png")
    victim = holders[0]
    c.post("/v1/incidents/fault", json={"kind": "node_dead", "subject": victim, "at": time.time()})
    clk.silent.add(victim)
    t_kill = clk.ms.members[victim].last_hb                 # silence starts at its last heartbeat
    times = {}
    while clk.t - t_kill < 30:
        clk.run(0.1)
        s = clk.state(victim)
        times.setdefault(s, clk.t - t_kill)
        if s == NodeState.DEAD:
            break
    assert 1.4 <= times[NodeState.SUSPECT] <= 2.1
    assert 2.4 <= times[NodeState.DOWN] <= 3.2                        # detect ≈ 2.6 s (φ≥8 + confirm 1 s)
    assert CFG.detector.dead_after_s <= times[NodeState.DEAD] <= CFG.detector.dead_after_s + 0.7
    assert all(clk.state(n) == NodeState.ALIVE for n in IDS if n != victim)

    types = [e["type"] for e in events(c, "node.suspect", "node.down", "node.dead")]
    assert types == ["node.suspect", "node.down", "node.dead"]
    dead = events(c, "node.dead")[0]
    assert dead["data"]["e"] == 1 and dead["data"]["e2"] == 2 and dead["data"]["chunks"] == 1
    assert "Rebuilding its 1 copies" in dead["human"]
    assert clk.ms.node(victim).epoch == 2

    db = c.app_.state.db
    rows = c.portal.call(db.fetchall, "SELECT state FROM fragments WHERE node_id=?", (victim,))
    assert [r["state"] for r in rows] == ["lost"]
    persisted = c.portal.call(db.fetchone, "SELECT persisted_state, epoch FROM nodes WHERE id=?", (victim,))
    assert persisted == {"persisted_state": "DEAD", "epoch": 2}
    jobs = c.portal.call(db.fetchall, "SELECT * FROM jobs")
    assert len(jobs) == 1 and jobs[0]["priority"] == 1 and jobs[0]["incident_id"] == dead["data"]["inc"]
    inc = c.portal.call(db.fetchone, "SELECT * FROM incidents WHERE id=?", (dead["data"]["inc"],))
    assert inc["kind"] == "node_dead" and inc["subject"] == victim and inc["affected_chunks"] == 1
    assert inc["fault_at"] <= inc["detected_at"]

    # the dead node comes back: told DEAD, re-registers with the new epoch
    r = c.post(f"/v1/nodes/{victim}/heartbeat", json=hb(victim, epoch=1).model_dump()).json()
    assert r["state"] == "DEAD" and r["epoch"] == 2
    r = c.post("/v1/nodes/register", json={"node_id": victim, "addr": CFG.addr(victim), "capacity_bytes": 2 ** 31,
                                           "display_name": victim, "labels": {}}).json()
    assert r["state"] == "REJOINING" and r["epoch"] == 2


def test_partitioned_with_peer_evidence_then_down(c):
    clk = Clock(c)
    clk.run(3)
    clk.silent.add("n5")
    clk.reach["n2"] = {"n5": Reach(ok=True, rtt_ms=1.0)}   # n2 keeps reaching n5
    clk.run(4)
    assert clk.state("n5") == NodeState.PARTITIONED
    ev = events(c, "node.partitioned")[0]
    assert "Doctor's Desk can" in ev["human"]
    clk.run(CFG.detector.dead_after_s + 2)
    assert clk.state("n5") == NodeState.PARTITIONED     # peers can reach it: never DOWN, never rebuilt
    clk.reach["n2"] = {"n5": Reach(ok=False)}           # evidence stops → DOWN
    clk.run(3)
    assert clk.state("n5") == NodeState.DOWN            # full grace again from the last evidence, not DEAD
    clk.run(CFG.detector.dead_after_s)
    assert clk.state("n5") == NodeState.DEAD


def test_startup_grace_blocks_down_and_dead(c):
    brain = c.app_.state.brain
    clk = Clock(c)
    brain.started_mono = clk.t                          # grace starts now (10 s)
    clk.run(2)
    clk.silent.add("n4")
    clk.run(6)
    assert clk.state("n4") == NodeState.SUSPECT         # suspect is allowed, down is not
    clk.run(4)                                          # grace over → DOWN
    assert clk.state("n4") == NodeState.DOWN


def test_joining_that_never_returns_goes_down(c):
    brain = c.app_.state.brain
    clk = Clock(c)
    brain.started_mono = clk.t
    for m in clk.ms.members.values():
        m.state_since = clk.t
    clk.silent.add("n6")
    clk.run(CFG.detector.startup_grace_s + 0.5)
    assert clk.state("n6") == NodeState.DOWN
    assert all(clk.state(n) == NodeState.ALIVE for n in IDS if n != "n6")


def test_recovered_in_grace(c):
    clk = Clock(c)
    clk.run(3)
    clk.silent.add("n3")
    clk.run(4)
    assert clk.state("n3") == NodeState.DOWN
    # back via the real route: 3 heartbeats within 2 s → ALIVE, no rebuild
    for i in range(3):
        r = c.post("/v1/nodes/n3/heartbeat", json=hb("n3").model_dump()).json()
    assert r["state"] == "ALIVE"
    ev = events(c, "node.recovered_in_grace")
    assert len(ev) == 1 and "No rebuild needed" in ev[0]["human"]
    assert c.portal.call(c.app_.state.db.kv_get, "repairs_avoided") == "1"
    assert events(c, "node.dead") == []


def test_suspect_recovers_on_heartbeat(c):
    clk = Clock(c)
    clk.run(3)
    clk.silent.add("n1")
    clk.run(1.9)
    assert clk.state("n1") == NodeState.SUSPECT
    clk.silent.discard("n1")
    clk.run(0.6)
    assert clk.state("n1") == NodeState.ALIVE


# ── flags and routes ──

def test_slow_flag_and_event(c):
    clk = Clock(c)
    clk.run(6)                                                    # past the 5 s warm-up
    for nid in ("n1", "n2", "n3"):
        clk.reach[nid] = {"n6": Reach(ok=True, rtt_ms=800.0)}
    clk.run(3)
    assert clk.ms.node("n6").slow is True
    assert len(events(c, "node.slow")) == 1


def test_relay_route_events(c):
    clk = Clock(c)
    clk.run(1)
    clk.ms.heartbeat("n5", hb("n5"), "n2", clk.t)
    c.portal.call(tick, c.app_.state.brain, clk.t)
    assert clk.ms.node("n5").route == "relay:n2"
    ev = events(c, "link.relayed")
    assert len(ev) == 1 and ev[0]["human"] == ("The cable between Vault index and Records Room looks cut. "
                                               "Messages now go through Doctor's Desk.")
    clk.ms.heartbeat("n5", hb("n5"), None, clk.t + 0.5)
    c.portal.call(tick, c.app_.state.brain, clk.t + 0.5)
    assert len(events(c, "link.restored")) == 1


def test_fault_report_chips(c):
    c.post("/v1/incidents/fault", json={"kind": "disk_full", "subject": "n2", "at": time.time()})
    clk = Clock(c)
    clk.run(0.5)
    assert clk.ms.node("n2").faults == ["disk_full"]
    c.post("/v1/incidents/fault", json={"kind": "clear", "subject": "cluster", "at": time.time()})
    clk.run(0.5)
    assert clk.ms.node("n2").faults == []


# ── snapshot ──

def test_snapshot_ok(c):
    clk = Clock(c)
    clk.run(1)
    put(c, "a")
    s = c.get("/v1/cluster").json()
    assert s["mode"] == "vault" and len(s["nodes"]) == 6
    assert s["summary"]["level"] == "ok"
    assert s["summary"]["human"] == "Your data is safe. 6 of 6 machines are healthy."
    assert s["summary"]["files"] == 1 and s["summary"]["min_ifl"] == 3 and s["summary"]["overhead"] == 3.0
    assert s["control"][0] == {"id": "meta", "ok": True}
    assert s["repair"]["queued"] == {"p0": 0, "p1": 0, "p2": 0, "p3": 0, "p4": 0}
    assert s["incident"] is None and s["links"] == []


def _kill(clk, nid, dead=True):
    clk.silent.add(nid)
    clk.run(CFG.detector.dead_after_s + 1 if dead else 4)


def test_summary_levels(c):
    clk = Clock(c)
    clk.run(1)
    holders = put(c, "f")
    _kill(clk, holders[0], dead=False)                  # DOWN: copies still durable, still readable
    s = c.get("/v1/cluster").json()["summary"]
    assert s["level"] == "ok" and s["human"] == "Your data is safe. 5 of 6 machines are healthy."
    clk.run(CFG.detector.dead_after_s)                  # DEAD: below target
    c.app_.state.brain.invalidate_snapshot()
    s = c.get("/v1/cluster").json()
    assert s["summary"]["level"] == "degraded" and s["summary"]["under_replicated_chunks"] == 1
    assert "1 files have fewer copies" in s["summary"]["human"] and "(0% done)" in s["summary"]["human"]
    assert s["incident"]["kind"] == "node_dead" and s["repair"]["queued"]["p1"] == 1
    _kill(clk, holders[1])                              # one copy left → at_risk
    c.app_.state.brain.invalidate_snapshot()
    s = c.get("/v1/cluster").json()["summary"]
    assert s["level"] == "at_risk" and s["at_risk_files"] == 1
    _kill(clk, holders[2], dead=False)                  # last holder DOWN → unreadable now
    c.app_.state.brain.invalidate_snapshot()
    s = c.get("/v1/cluster").json()["summary"]
    assert s["level"] == "critical" and s["unreadable_files"] == 1
    assert f"Turn {CFG.nodes[holders[2]].display_name} back on." in s["human"]


def test_naive_prefix(c):
    c.post("/v1/mode", json={"mode": "naive"})
    s = c.get("/v1/cluster").json()
    assert s["mode"] == "naive" and s["summary"]["human"].startswith("Comparison mode")


def test_links_and_relay(c):
    clk = Clock(c)
    clk.run(1)
    ms = clk.ms
    ms.participant_report("gw", {n: Reach(ok=(n != "n4"), rtt_ms=1.0) for n in IDS}, clk.t)
    for nid in IDS:
        clk.beat(nid, reach={p: Reach(ok=True, rtt_ms=1.0) for p in IDS + ["meta"] if p != nid})
    ms.participant_report("meta", {n: Reach(ok=True, rtt_ms=1.0) for n in IDS} | {"gw": Reach(ok=True)}, clk.t)
    c.app_.state.brain.invalidate_snapshot()
    s = c.get("/v1/cluster").json()
    assert len(s["links"]) == 1
    link = s["links"][0]
    assert (link["a"], link["b"], link["a_to_b"], link["b_to_a"]) == ("gw", "n4", False, True)
    assert link["relay"] in IDS and link["relay"] != "n4"               # a node both sides can reach
    assert s["control"][1] == {"id": "gw", "ok": True}
    # meta→n5 cut, n5 heartbeats relayed via n2
    ms.participant_report("meta", {n: Reach(ok=(n != "n5"), rtt_ms=1.0) for n in IDS} | {"gw": Reach(ok=True)}, clk.t)
    ms.heartbeat("n5", hb("n5", reach={"meta": Reach(ok=False)}), "n2", clk.t)
    c.app_.state.brain.invalidate_snapshot()
    links = c.get("/v1/cluster").json()["links"]
    assert {"a": "meta", "b": "n5", "a_to_b": False, "b_to_a": False, "relay": "n2"} in links


def test_participant_report_traffic(c):
    r = c.post("/v1/participants/gw/report", json={"reach": {"n1": {"ok": True, "rtt_ms": 1.0}},
                                                   "stats": {"window_s": 1, "ok": 5, "failed": 0,
                                                             "by_op": {"put": {"ok": 3, "failed": 1},
                                                                       "get": {"ok": 2, "failed": 0}}}})
    assert r.status_code == 204
    c.app_.state.brain.invalidate_snapshot()
    assert c.get("/v1/cluster").json()["traffic"] == {"puts_per_s": 4.0, "gets_per_s": 2.0}



def test_no_slow_flag_at_boot(c):
    # Soum's handoff: at boot peers' pings fail while they start → must not flag every machine slow.
    brain = c.app_.state.brain
    clk = Clock(c)
    brain.started_mono = clk.t                                     # fresh start: grace running
    for nid in IDS:                                               # everyone "can't reach" everyone else yet
        clk.reach[nid] = {p: Reach(ok=False) for p in IDS if p != nid}
    clk.run(CFG.detector.startup_grace_s - 0.5)
    assert not any(clk.ms.node(n).slow for n in IDS)
    for nid in IDS:                                               # boot finished: peers answer normally
        clk.reach[nid] = {p: Reach(ok=True, rtt_ms=5.0) for p in IDS if p != nid}
    clk.run(8)
    assert not any(clk.ms.node(n).slow for n in IDS)
    assert events(c, "node.slow") == []


def test_slow_loss_rule_needs_two_peers_and_says_why(c):
    clk = Clock(c)
    clk.run(6)                                                    # everyone warmed up
    clk.reach["n1"] = {"n6": Reach(ok=False)}                     # one peer alone: not enough
    clk.run(2)
    assert clk.ms.node("n6").slow is False
    clk.reach["n2"] = {"n6": Reach(ok=False)}
    clk.run(2)
    assert clk.ms.node("n6").slow is True
    ev = events(c, "node.slow")[0]
    assert ev["data"]["why"].startswith("ping loss 2/")
