"""Owner: Soum (S6). Relay endpoint (hop check, forward once with the caller as origin, hop-by-hop headers
dropped, failed forward → 502 not 599, off in Naive) and node chaos (/_chaos/corrupt bitflip|zero|truncate|delete
on the payload only, found by read verification; /_chaos/disk_full with netsim clear + state hooks)."""
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from vault.common import ids
from vault.common.config import SafetyCfg, load_config
from vault.common.hashing import sha256_hex
from vault.common.models import FragmentHeader
from vault.common.rpc import NetworkError, get_rpc
from vault.node.soum_app import create_app
from vault.node.soum_storage import encode_meta


def frag(payload: bytes, i: int = 0) -> FragmentHeader:
    vid = ids.new_version_id()
    cid = ids.chunk_id(vid, 0)
    sha = sha256_hex(payload)
    return FragmentHeader(fid=ids.fid(cid, i), chunk_id=cid, frag_idx=i, version_id=vid, bucket="clinic", key="k",
                          policy="rep3", chunk_sha256=sha, frag_sha256=sha, chunk_size=len(payload),
                          frag_size=len(payload), epoch=1)


@pytest.fixture
def env(tmp_path):
    cfg = load_config("vault.yaml")
    cfg.cluster.data_dir = str(tmp_path)
    app = create_app(cfg, "n1", start_loops=False)
    with TestClient(app) as client:
        node = app.state.node
        node.epoch, node.lease_expiry = 1, time.time() + 600
        yield client, node


def put(client, payload: bytes, i: int = 0) -> FragmentHeader:
    h = frag(payload, i)
    r = client.put(f"/v1/fragments/{h.fid}", content=payload,
                   headers={"X-Vault-Meta": encode_meta(h), "X-Vault-Sha256": h.frag_sha256, "X-Vault-Epoch": "1"})
    assert r.status_code == 201
    return h


# ── relay ──

def test_relay_requires_hop_1(env):
    client, _ = env
    r = client.get("/v1/relay/n2/v1/ping", headers={"X-Vault-From": "gw"})
    assert r.status_code == 400 and r.json()["error"] == "bad_hop"


def test_relay_forwards_once(env, monkeypatch):
    client, _ = env
    calls = []

    async def fake_forward(target, method, path, *, origin, content, headers, params=None, timeout=None):
        calls.append((target, method, path, origin, content, params))
        return httpx.Response(201, content=b'{"ok":true}',
                              headers={"content-type": "application/json", "x-vault-sha256": "abc",
                                       "connection": "keep-alive", "transfer-encoding": "chunked"})
    monkeypatch.setattr(get_rpc(), "forward", fake_forward)
    r = client.put("/v1/relay/n2/v1/fragments/v_x_00000_f0?a=1", content=b"payload",
                   headers={"X-Vault-From": "gw", "X-Vault-Hop": "1"})
    assert r.status_code == 201 and r.json() == {"ok": True} and r.headers["x-vault-sha256"] == "abc"
    assert calls == [("n2", "PUT", "/v1/fragments/v_x_00000_f0", "gw", b"payload", {"a": "1"})]


def test_relay_failed_forward_is_502_not_599(env, monkeypatch):
    client, _ = env

    async def dead(*a, **kw):
        raise NetworkError("n2", "ConnectError")
    monkeypatch.setattr(get_rpc(), "forward", dead)
    r = client.post("/v1/relay/n2/v1/nodes/n5/heartbeat", json={}, headers={"X-Vault-From": "n5", "X-Vault-Hop": "1"})
    assert r.status_code == 502 and r.json()["error"] == "target_unreachable"


def test_relay_off_in_naive(env):
    client, node = env
    node.safety = SafetyCfg.for_mode("naive")
    r = client.get("/v1/relay/n2/v1/ping", headers={"X-Vault-From": "gw", "X-Vault-Hop": "1"})
    assert r.status_code == 503 and r.json()["error"] == "relay_disabled"


# ── corrupt ──

@pytest.mark.parametrize("mode,status", [("bitflip", 409), ("zero", 409), ("truncate", 409), ("delete", 404)])
def test_corrupt_modes_are_found_on_read(env, mode, status):
    client, node = env
    hs = [put(client, os.urandom(2000 + i), i) for i in range(3)]
    r = client.post("/_chaos/corrupt", json={"fids": [hs[1].fid], "mode": mode})
    assert r.status_code == 200 and r.json() == {"corrupted": [hs[1].fid]}
    assert node.storage.has(hs[1].fid)                        # silent: the node doesn't know yet
    assert client.get(f"/v1/fragments/{hs[1].fid}").status_code == status
    for h in (hs[0], hs[2]):
        assert client.get(f"/v1/fragments/{h.fid}").status_code == 200


def test_corrupt_random_count_and_header_intact(env):
    client, node = env
    hs = [put(client, os.urandom(500), i) for i in range(5)]
    put(client, b"", 9)                                       # empty payload: can't be damaged, never picked
    r = client.post("/_chaos/corrupt", json={"count": 3})
    damaged = r.json()["corrupted"]
    assert len(damaged) == 3 and set(damaged) <= {h.fid for h in hs}
    for fid in damaged:                                        # header still parses: only the payload changed
        assert node.storage.entry(fid) is not None
    assert client.post("/_chaos/corrupt", json={"count": 50}).json()["corrupted"].__len__() == 5
    client.post("/v1/scrub")
    for _ in range(100):
        if client.get("/v1/scrub").json()["last_pass_at"]:
            break
        time.sleep(0.02)
    assert client.get("/v1/scrub").json()["corrupt_found"] == 5


# ── disk full ──

def test_disk_full_and_clear(env):
    client, node = env
    s = client.post("/_chaos/disk_full", json={"on": True}).json()
    assert s["pid"] == "n1" and s["node_faults"] == {"disk_full": True}
    h = frag(b"abc")
    r = client.put(f"/v1/fragments/{h.fid}", content=b"abc",
                   headers={"X-Vault-Meta": encode_meta(h), "X-Vault-Sha256": h.frag_sha256, "X-Vault-Epoch": "1"})
    assert r.status_code == 507
    assert client.get("/_chaos").json()["node_faults"] == {"disk_full": True}
    client.post("/_chaos/clear")
    assert node.disk_full is False and client.get("/_chaos").json()["node_faults"] == {"disk_full": False}
    put(client, b"abc")
