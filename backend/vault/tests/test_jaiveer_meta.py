"""Owner: Jaiveer (J4). Metadata DB + app skeleton + membership basics.

Cover: schema applied once; buckets (create, 409 bucket_exists, unknown policy, list); register (known node,
unknown n7+, DEAD → REJOINING); heartbeat (JOINING → ALIVE, X-Vault-Via route, unknown / stale epoch /
not-registered → DEAD reply); config GET/PATCH (not_patchable, bad_config, config_version bump); mode
(naive → safety off + synchronous OFF, config.mode_changed); events GET/POST; meta.recovered on start;
everything survives a restart; jobs dedupe; kill -9 mid-write loses nothing that was acknowledged.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from vault.common.config import load_config
from vault.common.models import JobKind, JobReason, NodeState
from vault.metadata.jaiveer_app import create_app

REPO = Path(__file__).resolve().parents[3]
CFG = load_config(str(REPO / "vault.yaml"))


def reg(node_id="n1", **kw):
    body = {"node_id": node_id, "addr": CFG.addr(node_id) if node_id in CFG.nodes else "127.0.0.1:7107",
            "capacity_bytes": 2 ** 31, "display_name": kw.pop("display_name", node_id), "labels": {}}
    body.update(kw)
    return body


def hb(node_id="n1", epoch=1, **kw):
    return {"node_id": node_id, "epoch": epoch, "disk_used": 10, "capacity": 2 ** 31, "fragments": 0, **kw}


@pytest.fixture
def dbpath(tmp_path):
    return str(tmp_path / "vault.db")


@pytest.fixture
def client(dbpath):
    app = create_app(CFG, "meta", db_path=dbpath)
    with TestClient(app) as c:
        c.app_ = app
        yield c


async def _writer_sync(db) -> int:
    cur = await db._w.execute("PRAGMA synchronous")
    return (await cur.fetchone())[0]


def event_types(c):
    return [e["type"] for e in c.get("/v1/events").json()["events"]]


# ── startup ──

def test_meta_recovered_and_nodes_seeded(client):
    evs = client.get("/v1/events").json()["events"]
    assert evs[0]["type"] == "meta.recovered"
    assert evs[0]["human"].startswith("Vault's index recovered 0 files")
    ms = client.app_.state.membership
    assert sorted(n.id for n in ms.nodes()) == ["n1", "n2", "n3", "n4", "n5", "n6"]
    assert all(n.state == NodeState.JOINING for n in ms.nodes())
    assert ms.node("n3").display_name == "Lab Laptop" and ms.node("n3").labels["power"] == "B"


def test_wal_and_full_sync(client):
    db = client.app_.state.db
    assert db.durable is True
    assert client.portal.call(db.fetchone, "PRAGMA journal_mode")["journal_mode"] == "wal"


# ── buckets ──

def test_bucket_create_list_and_409(client):
    r = client.post("/v1/buckets", json={"name": "clinic", "policy": "rep3"})
    assert r.status_code == 201
    assert r.json()["policy"] == {"name": "rep3", "type": "replication", "n": 3, "w": 2, "k": None, "m": None}
    r = client.post("/v1/buckets", json={"name": "clinic", "policy": "rep3"})
    assert r.status_code == 409 and r.json()["error"] == "bucket_exists"
    client.post("/v1/buckets", json={"name": "archive", "policy": "ec42"})
    b = client.get("/v1/buckets").json()["buckets"]
    assert [x["name"] for x in b] == ["archive", "clinic"]
    assert b[0]["policy"]["k"] == 4 and b[0]["policy"]["n"] == 6


def test_bucket_default_policy_and_bad_input(client):
    assert client.post("/v1/buckets", json={"name": "x"}).json()["policy"]["name"] == "rep3"
    assert client.post("/v1/buckets", json={"name": "y", "policy": "rep9"}).status_code == 422
    assert client.post("/v1/buckets", json={"name": "a/b"}).json()["error"] == "bad_bucket_name"


# ── register / heartbeat ──

def test_register_then_first_heartbeat_makes_alive(client):
    r = client.post("/v1/nodes/register", json=reg("n1")).json()
    assert r == {"epoch": 1, "state": "JOINING", "lease_ttl_ms": 5000, "config_version": 0}
    r = client.post("/v1/nodes/n1/heartbeat", json=hb("n1")).json()
    assert r["state"] == "ALIVE" and r["epoch"] == 1 and r["lease_ttl_ms"] == 5000
    v = client.app_.state.membership.node("n1")
    assert v.state == NodeState.ALIVE and v.route == "direct" and v.disk_used == 10
    assert "node.joined" in event_types(client)


def test_heartbeat_via_relay_sets_route(client):
    client.post("/v1/nodes/register", json=reg("n5"))
    client.post("/v1/nodes/n5/heartbeat", json=hb("n5"), headers={"X-Vault-Via": "n2"})
    assert client.app_.state.membership.node("n5").route == "relay:n2"
    client.post("/v1/nodes/n5/heartbeat", json=hb("n5"))
    assert client.app_.state.membership.node("n5").route == "direct"


def test_heartbeat_without_register_or_unknown_or_stale_says_dead(client):
    assert client.post("/v1/nodes/n2/heartbeat", json=hb("n2")).json()["state"] == "DEAD"      # not registered
    assert client.post("/v1/nodes/n9/heartbeat", json=hb("n9")).json()["state"] == "DEAD"      # unknown
    client.post("/v1/nodes/register", json=reg("n2"))
    assert client.post("/v1/nodes/n2/heartbeat", json=hb("n2", epoch=7)).json()["state"] == "DEAD"  # stale
    assert client.post("/v1/nodes/n2/heartbeat", json=hb("n1")).status_code == 400            # body/path mismatch


def test_register_unknown_node_n7(client):
    r = client.post("/v1/nodes/register", json=reg("n7", display_name="New PC", labels={"power": "B"}))
    assert r.status_code == 200 and r.json()["epoch"] == 1
    v = client.app_.state.membership.node("n7")
    assert v.display_name == "New PC" and v.labels == {"power": "B"}
    assert client.app_.state.brain.rpc.addr("n7") == "127.0.0.1:7107"
    assert client.post("/v1/nodes/n7/heartbeat", json=hb("n7")).json()["state"] == "ALIVE"


def test_dead_node_reregisters_as_rejoining_then_alive_after_inventory(client):
    ms = client.app_.state.membership
    client.post("/v1/nodes/register", json=reg("n3"))
    m = ms.members["n3"]
    m.view.state, m.persisted_state, m.view.epoch = NodeState.DEAD, NodeState.DEAD, 2     # what J6 does at DEAD
    assert client.post("/v1/nodes/n3/heartbeat", json=hb("n3", epoch=1)).json()["state"] == "DEAD"
    r = client.post("/v1/nodes/register", json=reg("n3")).json()
    assert r["state"] == "REJOINING" and r["epoch"] == 2
    assert client.post("/v1/nodes/n3/heartbeat", json=hb("n3", epoch=2)).json()["state"] == "REJOINING"
    assert client.post("/v1/nodes/n3/inventory", json={"node_id": "n3", "epoch": 2, "fragments": []}).status_code == 200
    assert client.post("/v1/nodes/n3/heartbeat", json=hb("n3", epoch=2)).json()["state"] == "ALIVE"


def test_startup_discarded_event(client):
    client.post("/v1/nodes/register", json=reg("n4", discarded_on_startup=2))
    ev = client.get("/v1/events").json()["events"][-1]
    assert ev["type"] == "node.startup_discarded" and "Pharmacy PC" in ev["human"] and "2" in ev["human"]


def test_relabel(client):
    r = client.patch("/v1/nodes/n3/labels", json={"labels": {"power": "A", "switch": "S2"}})
    assert r.status_code == 200 and r.json()["labels"] == {"power": "A", "switch": "S2"}
    assert client.patch("/v1/nodes/n9/labels", json={"labels": {}}).status_code == 404


# ── config / mode ──

def test_config_get_patch(client):
    c0 = client.get("/v1/config").json()
    assert c0["config_version"] == 0 and c0["mode"] == "vault" and c0["safety"]["repair"] is True
    r = client.patch("/v1/config", json={"safety": {"repair": False}})
    assert r.status_code == 200 and r.json()["safety"]["repair"] is False and r.json()["config_version"] == 1
    assert client.get("/_vault/health").json()["config_version"] == 1
    assert client.patch("/v1/config", json={"nodes": {}}).json()["error"] == "not_patchable"
    bad = client.patch("/v1/config", json={"detector": {"dead_after_s": 1}})           # lease must be < dead_after
    assert bad.status_code == 400 and bad.json()["error"] == "bad_config"
    assert client.get("/v1/config").json()["config_version"] == 1                       # bad patch changed nothing


def test_mode_naive_and_back(client):
    r = client.post("/v1/mode", json={"mode": "naive"}).json()
    assert r == {"mode": "naive", "config_version": 1}
    brain = client.app_.state.brain
    assert not any(brain.safety().model_dump().values())
    assert client.app_.state.db.durable is False
    assert brain.rpc.relay_enabled is False
    assert client.portal.call(_writer_sync, client.app_.state.db) == 0                 # OFF
    ev = client.get("/v1/events").json()["events"][-1]
    assert ev["type"] == "config.mode_changed" and ev["human"].startswith("Comparison mode")
    assert client.post("/v1/mode", json={"mode": "vault"}).json()["config_version"] == 2
    assert all(brain.safety().model_dump().values()) and client.app_.state.db.durable is True
    assert client.portal.call(_writer_sync, client.app_.state.db) == 2                 # FULL
    assert client.post("/v1/mode", json={"mode": "vault"}).json()["config_version"] == 3   # still bumps
    assert event_types(client).count("config.mode_changed") == 2                           # no-op: no event


# ── events ──

def test_post_external_event(client):
    r = client.post("/v1/events", json={"type": "chaos.kill", "severity": "warn", "subject": {"node": "n3"},
                                        "human": "You turned off Lab Laptop.", "data": {"at": 1.0}})
    assert r.status_code == 201 and r.json()["id"] > 0 and r.json()["human"] == "You turned off Lab Laptop."
    assert client.post("/v1/events", json={"type": "node.dead", "human": "x"}).status_code == 400
    evs = client.get("/v1/events", params={"after_id": r.json()["id"] - 1}).json()["events"]
    assert evs[0]["type"] == "chaos.kill"


# ── jobs ──

def test_enqueue_job_dedupes(client):
    brain = client.app_.state.brain
    call = client.portal.call
    a = call(lambda: brain.enqueue_job(JobKind.repair, JobReason.under_replicated, chunk_id="c1", frag_idx=0,
                                       priority=1))
    b = call(lambda: brain.enqueue_job(JobKind.repair, JobReason.corrupt, chunk_id="c1", frag_idx=0, priority=0))
    c = call(lambda: brain.enqueue_job(JobKind.repair, JobReason.under_replicated, chunk_id="c1", frag_idx=1,
                                       priority=1))
    assert a == b != c
    row = call(brain.db.fetchone, "SELECT priority FROM jobs WHERE id = ?", (a,))
    assert row["priority"] == 0                                                         # upgraded


# ── restart ──

def test_everything_survives_restart(dbpath):
    app = create_app(CFG, "meta", db_path=dbpath)
    with TestClient(app) as c:
        c.post("/v1/buckets", json={"name": "clinic", "policy": "rep3"})
        c.post("/v1/nodes/register", json=reg("n7", display_name="New PC", labels={"power": "B"}))
        c.patch("/v1/nodes/n3/labels", json={"labels": {"power": "A"}})
        c.post("/v1/mode", json={"mode": "naive"})
    app2 = create_app(CFG, "meta", db_path=dbpath)
    with TestClient(app2) as c:
        assert [b["name"] for b in c.get("/v1/buckets").json()["buckets"]] == ["clinic"]
        ms = app2.state.membership
        assert ms.node("n7").display_name == "New PC" and ms.node("n7").state == NodeState.JOINING
        assert ms.node("n3").labels == {"power": "A"}
        cfg = c.get("/v1/config").json()
        assert cfg["mode"] == "naive" and cfg["config_version"] == 1 and cfg["safety"]["repair"] is False
        assert app2.state.db.durable is False
        assert event_types(c).count("meta.recovered") == 2
        assert c.post("/v1/buckets", json={"name": "clinic"}).status_code == 409


# ── kill -9 mid-write ──

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(url: str, body: dict, timeout: float = 2.0) -> int:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _wait_up(url: str, proc, timeout: float = 20) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"metadata exited: {proc.stdout.read().decode(errors='replace')[-2000:]}")
        try:
            with urllib.request.urlopen(url + "/_vault/health", timeout=0.5):
                return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.1)
    raise TimeoutError("metadata did not start")


def test_kill_minus_9_mid_write_loses_nothing_acknowledged(tmp_path):
    raw = yaml.safe_load((REPO / "vault.yaml").read_text(encoding="utf-8"))
    port = _free_port()
    raw["cluster"]["data_dir"] = str(tmp_path / "data")
    raw["cluster"]["logs_dir"] = str(tmp_path / "logs")
    raw["cluster"]["ports"]["metadata"] = port
    cfg_path = tmp_path / "vault.yaml"
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    env = {**os.environ, "VAULT_CONFIG": str(cfg_path), "PYTHONPATH": str(REPO / "backend")}
    env.pop("VAULT_DEMO", None)
    url = f"http://127.0.0.1:{port}"

    def start():
        p = subprocess.Popen([sys.executable, "-m", "vault.metadata"], cwd=str(tmp_path), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        _wait_up(url, p)
        return p

    proc = start()
    acked: list[str] = []
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            name = f"b{i:05d}"
            try:
                if _post(url + "/v1/buckets", {"name": name, "policy": "rep3"}) == 201:
                    acked.append(name)
            except (urllib.error.URLError, ConnectionError, OSError, TimeoutError):
                return                              # connection died: the kill landed
            i += 1

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    deadline = time.time() + 15
    while len(acked) < 40 and time.time() < deadline:
        time.sleep(0.01)
    proc.kill()                                     # SIGKILL on POSIX, TerminateProcess on Windows
    proc.wait(timeout=10)
    stop.set()
    t.join(timeout=5)
    assert len(acked) >= 40, "writer too slow to test"

    proc = start()
    try:
        with urllib.request.urlopen(url + "/v1/buckets", timeout=2) as r:
            names = {b["name"] for b in json.loads(r.read())["buckets"]}
        missing = [n for n in acked if n not in names]
        assert not missing, f"acknowledged but lost after kill -9: {missing[:5]}"
        with urllib.request.urlopen(url + "/v1/events", timeout=2) as r:
            types = [e["type"] for e in json.loads(r.read())["events"]]
        assert types.count("meta.recovered") == 2
    finally:
        proc.kill()
        proc.wait(timeout=10)
