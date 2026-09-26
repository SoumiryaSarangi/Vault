"""Owner: Soum. LAN mode (docs/soum_lan_demo.md): VAULT_HUB / bind vs advertised address, VAULT_NODE_ADDR kept
across config swaps, addresses learned from the topology, CORS for LAN origins, orphan age on the node's clock."""
import re

import pytest

from vault.common import ids
from vault.common.config import load_config, node_addr
from vault.common.models import Inventory, InventoryItem
from vault.common.netsim import NetSim
from vault.common.rpc import get_rpc, init_rpc
from vault.common.service import LOCAL_ORIGINS
from vault.brain.soum_reconciler import reconcile_inventory
from vault.node.soum_heartbeat import HeartbeatLoop
from vault.node.soum_pinger import Pinger
from vault.tests.test_soum_node_loops import node  # noqa: F401  (fixture)
from vault.tests.test_soum_reconcile_gc import SHA, ctx, make_version  # noqa: F401  (fixture + helpers)


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, ""

    def json(self):
        return self._body


# ── config ──

def test_single_laptop_listens_where_it_is_reached(monkeypatch):
    monkeypatch.delenv("VAULT_HUB", raising=False)
    cfg = load_config("vault.yaml")
    assert cfg.listen_host() == cfg.cluster.host == "127.0.0.1"
    assert cfg.addr("meta") == "127.0.0.1:7000"


def test_hub_env_points_control_plane_at_hub_and_listens_everywhere(monkeypatch):
    monkeypatch.setenv("VAULT_HUB", "192.168.1.101")
    cfg = load_config("vault.yaml")
    assert cfg.addr("meta") == "192.168.1.101:7000" and cfg.addr("sup") == "192.168.1.101:7070"
    assert cfg.listen_host() == "0.0.0.0"
    assert cfg.cluster.ports.agent == 7071


def test_node_addr_env_wins(monkeypatch):
    cfg = load_config("vault.yaml")
    monkeypatch.delenv("VAULT_NODE_ADDR", raising=False)
    assert node_addr(cfg, "n3") == "127.0.0.1:7103"
    monkeypatch.setenv("VAULT_NODE_ADDR", "192.168.1.103:7103")
    assert node_addr(cfg, "n3") == "192.168.1.103:7103"


@pytest.mark.parametrize("origin,ok", [
    ("http://localhost:3000", True), ("http://127.0.0.1:3001", True), ("http://192.168.1.20:3000", True),
    ("http://10.0.0.5:3000", True), ("http://172.20.3.4:3000", True),
    ("http://172.40.3.4:3000", False), ("http://8.8.8.8:3000", False), ("http://evil.com", False),
])
def test_cors_allows_lan_origins_only(origin, ok):
    assert bool(re.fullmatch(LOCAL_ORIGINS, origin)) is ok


# ── node: advertised address survives metadata's config replacing ours ──

async def test_register_keeps_own_addr_after_config_swap(node, monkeypatch):
    monkeypatch.setenv("VAULT_NODE_ADDR", "192.168.1.102:7101")
    hb = HeartbeatLoop(node)
    node.cfg = load_config("vault.yaml")                # what metadata's config looks like on the hub
    sent = []

    async def fake_request(target, method, path, **kw):
        sent.append((path, kw.get("json")))
        return _Resp(503, {})
    monkeypatch.setattr(get_rpc(), "request", fake_request)
    assert await hb.register() is False
    assert sent[0][0] == "/v1/nodes/register" and sent[0][1]["addr"] == "192.168.1.102:7101"


# ── pinger (node and gateway): learn addresses from the topology ──

async def test_pinger_learns_node_addresses_from_topology(monkeypatch):
    cfg = load_config("vault.yaml")
    init_rpc("gw", cfg, NetSim("gw"))
    snap = {"nodes": [{"id": "n1", "state": "ALIVE", "addr": "127.0.0.1:7101"},
                      {"id": "n7", "state": "ALIVE", "addr": "192.168.1.107:7107"}], "links": []}

    async def fake_request(target, method, path, **kw):
        return _Resp(200, snap)
    monkeypatch.setattr(get_rpc(), "request", fake_request)
    p = Pinger("gw", cfg)
    await p.refresh_topology()
    assert get_rpc().addr("n7") == "192.168.1.107:7107" and "n7" in p.node_ids
    assert get_rpc().addr("n1") == "127.0.0.1:7101"
    assert get_rpc().relay_candidates("n1") == ["n7"]      # the joined laptop can relay too
    await get_rpc().close()


# ── reconciler: orphan age on the node's own clock ──

async def test_orphan_age_uses_node_clock(ctx):
    grace = ctx.cfg.gc.orphan_grace_s
    aborted = await make_version(ctx, "a.png", {}, state="aborted", current=False)
    item = [InventoryItem(fid=ids.fid(aborted, 0), sha256=SHA, size=100, mtime=ctx.t - 3600 - 1)]
    # the node's clock is an hour behind metadata's: its file is 1 s old, not an hour
    res = await reconcile_inventory(ctx, Inventory(node_id="n4", epoch=2, fragments=item, sent_at=ctx.t - 3600))
    assert res.orphans == 1 and ctx.jobs == []
    old = [InventoryItem(fid=ids.fid(aborted, 0), sha256=SHA, size=100, mtime=ctx.t - 3600 - grace - 5)]
    await reconcile_inventory(ctx, Inventory(node_id="n4", epoch=2, fragments=old, sent_at=ctx.t - 3600))
    assert [(j["chunk_id"], j["reason"]) for j in ctx.jobs] == [(aborted, "orphan")]
