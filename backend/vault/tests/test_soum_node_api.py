"""Owner: Soum (S3). Node fragment API (ARCHITECTURE §7.3): PUT/GET/HEAD/DELETE, checksum on receive,
epoch + lease fencing (never-leased → fenced), disk full, damaged copy → 409 corrupt + quarantine, ping, health, scrub."""
import asyncio
import os
import time

import pytest
from fastapi.testclient import TestClient

from vault.common import ids
from vault.common.config import SafetyCfg, load_config
from vault.common.hashing import sha256_hex
from vault.common.models import FragmentHeader
from vault.node.soum_app import create_app
from vault.node.soum_storage import Storage, decode_meta, encode_meta

EPOCH = 3


def make_frag(payload: bytes, frag_idx: int = 0) -> FragmentHeader:
    vid = ids.new_version_id()
    cid = ids.chunk_id(vid, 0)
    sha = sha256_hex(payload)
    return FragmentHeader(fid=ids.fid(cid, frag_idx), chunk_id=cid, frag_idx=frag_idx, version_id=vid,
                          bucket="clinic", key="xray-0042.png", policy="rep3", chunk_sha256=sha,
                          frag_sha256=sha, chunk_size=len(payload), frag_size=len(payload), epoch=EPOCH)


def put_headers(h: FragmentHeader, epoch=EPOCH) -> dict:
    hd = {"X-Vault-Meta": encode_meta(h), "X-Vault-Sha256": h.frag_sha256}
    if epoch is not None:
        hd["X-Vault-Epoch"] = str(epoch)
    return hd


@pytest.fixture
def env(tmp_path):
    cfg = load_config("vault.yaml")
    cfg.cluster.data_dir = str(tmp_path)
    app = create_app(cfg, "n1")
    with TestClient(app) as client:
        node = app.state.node
        node.epoch, node.lease_expiry = EPOCH, time.time() + 60
        yield client, node, tmp_path / "n1"


def test_meta_header_round_trip():
    h = make_frag(b"x")
    assert decode_meta(encode_meta(h)) == h
    assert "=" not in encode_meta(h)


def test_put_get_head_delete(env):
    client, node, _ = env
    payload = os.urandom(50_000)
    h = make_frag(payload)
    r = client.put(f"/v1/fragments/{h.fid}", content=payload, headers=put_headers(h))
    assert r.status_code == 201
    assert r.json() == {"fid": h.fid, "sha256": h.frag_sha256, "size": len(payload), "durable": True}

    r = client.get(f"/v1/fragments/{h.fid}")
    assert r.status_code == 200 and r.content == payload
    assert r.headers["x-vault-sha256"] == h.frag_sha256 and decode_meta(r.headers["x-vault-meta"]) == h

    assert client.head(f"/v1/fragments/{h.fid}").status_code == 200
    assert client.delete(f"/v1/fragments/{h.fid}", headers={"X-Vault-Epoch": str(EPOCH)}).status_code == 204
    assert client.head(f"/v1/fragments/{h.fid}").status_code == 404
    r = client.get(f"/v1/fragments/{h.fid}")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert client.delete(f"/v1/fragments/{h.fid}", headers={"X-Vault-Epoch": str(EPOCH)}).status_code == 204


def test_bad_meta(env):
    client, _, _ = env
    payload = b"abc"
    h = make_frag(payload)
    r = client.put(f"/v1/fragments/{h.fid}", content=payload, headers={"X-Vault-Epoch": str(EPOCH)})
    assert r.status_code == 400 and r.json()["error"] == "bad_request"
    r = client.put("/v1/fragments/v_other_00000_f0", content=payload, headers=put_headers(h))
    assert r.status_code == 400 and r.json()["error"] == "bad_request"


def test_checksum_mismatch(env):
    client, node, _ = env
    h = make_frag(b"what was sent")
    r = client.put(f"/v1/fragments/{h.fid}", content=b"what arrived!", headers=put_headers(h))
    assert r.status_code == 400 and r.json()["error"] == "checksum_mismatch"
    assert node.storage.count() == 0


def test_stale_or_missing_epoch(env):
    client, node, _ = env
    payload = b"abc"
    h = make_frag(payload)
    for epoch in (EPOCH - 1, None):
        r = client.put(f"/v1/fragments/{h.fid}", content=payload, headers=put_headers(h, epoch))
        assert r.status_code == 409 and r.json()["error"] == "stale_epoch"
    r = client.delete(f"/v1/fragments/{h.fid}", headers={"X-Vault-Epoch": "1"})
    assert r.status_code == 409 and r.json()["error"] == "stale_epoch"
    assert node.storage.count() == 0


@pytest.mark.parametrize("lease", ["expired", "never"])
def test_fenced_refuses_writes_but_serves_reads(env, lease):
    client, node, _ = env
    payload = b"stored before the lease ran out"
    h = make_frag(payload)
    assert client.put(f"/v1/fragments/{h.fid}", content=payload, headers=put_headers(h)).status_code == 201
    node.lease_expiry = time.time() - 1 if lease == "expired" else None
    h2 = make_frag(b"new")
    r = client.put(f"/v1/fragments/{h2.fid}", content=b"new", headers=put_headers(h2))
    assert r.status_code == 503 and r.json()["error"] == "fenced"
    r = client.delete(f"/v1/fragments/{h.fid}", headers={"X-Vault-Epoch": str(EPOCH)})
    assert r.status_code == 503
    assert client.get(f"/v1/fragments/{h.fid}").content == payload
    assert client.get("/v1/health").json()["fenced"] is True


def test_naive_mode_skips_fencing_and_verification(env):
    client, node, _ = env
    node.safety = SafetyCfg.for_mode("naive")
    node.lease_expiry = None
    h = make_frag(b"expected")
    r = client.put(f"/v1/fragments/{h.fid}", content=b"damaged!", headers=put_headers(h, None))
    assert r.status_code == 201 and r.json()["durable"] is False
    assert client.get(f"/v1/fragments/{h.fid}").content == b"damaged!"     # served unchecked


def test_disk_full(env):
    client, node, _ = env
    payload = b"abc"
    h = make_frag(payload)
    node.disk_full = True
    r = client.put(f"/v1/fragments/{h.fid}", content=payload, headers=put_headers(h))
    assert r.status_code == 507 and r.json()["error"] == "disk_full"
    node.disk_full, node.capacity = False, 10
    big = os.urandom(100)
    h2 = make_frag(big)
    assert client.put(f"/v1/fragments/{h2.fid}", content=big, headers=put_headers(h2)).status_code == 507


def test_damaged_copy_is_quarantined_on_read(env):
    client, node, root = env
    payload = os.urandom(8192)
    h = make_frag(payload)
    client.put(f"/v1/fragments/{h.fid}", content=payload, headers=put_headers(h))
    path = node.storage.entry(h.fid).path
    blob = bytearray(path.read_bytes())
    blob[-10] ^= 0x01
    path.write_bytes(bytes(blob))
    r = client.get(f"/v1/fragments/{h.fid}")
    assert r.status_code == 409 and r.json()["error"] == "corrupt"
    assert (root / "quarantine" / f"{h.fid}.blk").exists() and not path.exists()
    assert client.get(f"/v1/fragments/{h.fid}").status_code == 404


def test_scrub_finds_damage(env):
    client, node, _ = env
    payloads = [os.urandom(1000 + i) for i in range(3)]
    frags = [make_frag(p, i) for i, p in enumerate(payloads)]
    for h, p in zip(frags, payloads):
        assert client.put(f"/v1/fragments/{h.fid}", content=p, headers=put_headers(h)).status_code == 201
    path = node.storage.entry(frags[1].fid).path
    path.write_bytes(path.read_bytes()[:-1] + b"\x00")
    assert client.post("/v1/scrub").status_code == 202
    for _ in range(100):
        s = client.get("/v1/scrub").json()
        if s["last_pass_at"] and not s["running"]:
            break
        time.sleep(0.02)
    assert s["scanned"] == 3 and s["corrupt_found"] == 1
    assert node.storage.count() == 2 and not node.storage.has(frags[1].fid)


def test_ping_and_health(env):
    client, node, _ = env
    p = client.get("/v1/ping").json()
    assert p["pid"] == "n1" and p["epoch"] == EPOCH
    h = client.get("/v1/health").json()
    assert h["fenced"] is False and h["fragments"] == 0 and h["capacity"] == node.capacity


def test_startup_loads_saved_lease_and_epoch(tmp_path):
    cfg = load_config("vault.yaml")
    cfg.cluster.data_dir = str(tmp_path)
    app = create_app(cfg, "n2")
    with TestClient(app):
        pass
    st = Storage(tmp_path / "n2", "n2")
    asyncio.run(st.write_node_json({"epoch": 7, "lease_expiry": time.time() + 30}))
    app = create_app(cfg, "n2")
    with TestClient(app) as client:
        assert client.get("/v1/ping").json()["epoch"] == 7
        assert client.get("/v1/health").json()["fenced"] is False
