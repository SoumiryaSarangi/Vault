"""Tests for shared code in vault.common (owner: Anushka)."""
import pytest
from fastapi.testclient import TestClient

from vault.common import ids
from vault.common.config import SafetyCfg, VaultConfig, load_config
from vault.common.events import build_event, domain_name, fmt_bytes, render
from vault.common.hashing import placement_hash, sha256_hex
from vault.common.models import Manifest, Snapshot, UploadPlan
from vault.common.service import make_app


def test_config_loads_and_validates():
    cfg = load_config("vault.yaml")
    assert cfg.policy("rep3").n == 3 and cfg.policy("rep3").w == 2 and cfg.policy("rep3").needed == 1
    ec = cfg.policy("ec42")
    assert (ec.k, ec.m, ec.n, ec.w, ec.needed) == (4, 2, 6, 5, 4)
    assert cfg.addr("n3") == "127.0.0.1:7103" and cfg.port("meta") == 7000 and cfg.port("n7") == 7107
    assert cfg.nodes["n3"].display_name == "Lab Laptop"


def test_config_rejects_bad_lease():
    raw = {"detector": {"lease_ttl_ms": 20000, "dead_after_s": 15}}
    with pytest.raises(ValueError):
        VaultConfig.model_validate(raw)


def test_demo_env(monkeypatch):
    monkeypatch.setenv("VAULT_DEMO", "1")
    assert load_config("vault.yaml").detector.dead_after_s == 8


def test_naive_safety_all_off():
    s = SafetyCfg.for_mode("naive")
    assert not any(s.model_dump().values())
    assert all(SafetyCfg.for_mode("vault").model_dump().values())


def test_ids_roundtrip():
    v = ids.new_version_id()
    assert v.startswith("v_") and len(v) == 22
    c = ids.chunk_id(v, 2)
    f = ids.fid(c, 1)
    assert ids.parse_fid(f) == (v, c, 2, 1)
    assert ids.version_of_chunk(c) == v


def test_hashing_is_stable():
    assert placement_hash("n1#0") == placement_hash("n1#0")
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_event_templates():
    sev, human, tech = render("node.down", {"node": "Lab Laptop", "id": "n3", "phi": 9.34, "confirm": 1.0})
    assert sev == "warn" and human == "Lab Laptop stopped responding. Your files are still readable."
    assert tech == "φ=9.3; no peer reached n3 in 1.0s"
    # missing fields never crash
    assert render("repair.progress", {})[1].startswith("Rebuilding:")
    ev = build_event("node.joined", {"node": "n1"}, {"epoch": 1, "labels": {}})
    assert ev["subject"] == {"node": "n1"} and ev["severity"] == "info"
    assert domain_name("power", "A") == "Power Strip A" and fmt_bytes(2_100_000) == "2.1 MB"


def test_contract_examples_parse():
    UploadPlan.model_validate({"version_id": "v_1", "chunk_size": 1048576,
                               "policy": {"name": "rep3", "type": "replication", "n": 3, "w": 2},
                               "chunks": [{"chunk_id": "v_1_00000", "idx": 0, "size": 10,
                                           "targets": [{"frag_idx": 0, "node_id": "n1", "addr": "127.0.0.1:7101", "epoch": 1}],
                                           "spares": []}]})
    Snapshot.model_validate({"ts": 1.0, "mode": "vault", "config_version": 1,
                             "summary": {"level": "ok", "human": "Your data is safe."}})
    Manifest.model_json_schema()


def test_netsim_blocks_and_chaos_routes():
    cfg = load_config("vault.yaml")
    app = make_app(cfg, "n1")

    @app.get("/v1/ping")
    async def ping():
        return {"ok": True}

    with TestClient(app) as c:
        assert c.get("/v1/ping", headers={"X-Vault-From": "n2"}).status_code == 200
        c.post("/_chaos/block", json={"peers": ["n2"], "direction": "in"})
        r = c.get("/v1/ping", headers={"X-Vault-From": "n2"})
        assert r.status_code == 599 and r.headers["x-vault-netsim"] == "blocked"
        assert c.get("/v1/ping").status_code == 200          # browser traffic is never blocked
        c.post("/_chaos/clear")
        assert c.get("/v1/ping", headers={"X-Vault-From": "n2"}).status_code == 200
        assert c.get("/_vault/health").json()["pid"] == "n1"


def test_node_identity_from_yaml_and_env(monkeypatch):
    from vault.common.config import node_identity
    cfg = load_config("vault.yaml")
    assert node_identity(cfg, "n3") == ("Lab Laptop", {"power": "B", "switch": "S2", "disk_batch": "D3", "version": "1.0"})
    monkeypatch.setenv("VAULT_NODE_DISPLAY_NAME", "Storeroom PC")
    monkeypatch.setenv("VAULT_NODE_LABELS", '{"power": "D"}')
    assert node_identity(cfg, "n7") == ("Storeroom PC", {"power": "D"})


def test_chaos_step_contract():
    from vault.common.models import ChaosStep
    s = ChaosStep.model_validate({"t": 25, "action": "power_cut",
                                  "params": {"scope": "label", "label": "power=B", "restore_after_s": 6}})
    assert s.action == "power_cut" and s.params["label"] == "power=B"
    with pytest.raises(ValueError):
        ChaosStep.model_validate({"t": 1, "action": "explode"})
