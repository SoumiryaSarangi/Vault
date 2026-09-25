"""Owner: Soum (S7). Holder order (available → direct → not slow → RTT), per-window stats (reset after each
report, 404/412 count as ok), the gw report body, and a read that skips a machine metadata already calls DOWN."""
import os

import pytest

from vault.common.config import load_config
from vault.common.models import FragLoc, Reach
from vault.common.netsim import NetSim
from vault.common.rpc import get_rpc, init_rpc
from vault.gateway.soum_meta_client import Gateway
from vault.gateway.soum_routing import order_holders
from vault.gateway.soum_stats import report_once
from vault.tests.test_soum_gateway import cluster, down  # noqa: F401  (pytest fixture)


def loc(node: str, route: str = "direct", slow: bool = False) -> FragLoc:
    return FragLoc(frag_idx=0, node_id=node, addr="", state="ok", route=route, slow=slow)


def test_order_available_then_direct_then_fast_then_rtt():
    frags = [loc("n1"), loc("n2", route="relay:n4"), loc("n3", slow=True), loc("n4"), loc("n5"), loc("n6")]
    states = {"n1": "DOWN", "n2": "ALIVE", "n3": "ALIVE", "n4": "PARTITIONED", "n5": "SUSPECT"}   # n6 unknown
    rtt = {"n4": 9.0, "n5": 1.0, "n6": 4.0}
    assert [f.node_id for f in order_holders(frags, rtt, states)] == ["n5", "n6", "n4", "n3", "n2", "n1"]


def test_order_without_topology_keeps_route_and_slow_rules():
    frags = [loc("n1", slow=True), loc("n2", route="relay:n3"), loc("n3")]
    assert [f.node_id for f in order_holders(frags)] == ["n3", "n1", "n2"]


def test_stats_window_resets():
    gw = Gateway(load_config("vault.yaml"))
    gw.stats.record("put", True)
    gw.stats.record("get", True)
    gw.stats.record("get", False)
    gw.stats.failover_read("n3")
    snap = gw.stats.take_window()
    assert (snap.ok, snap.failed) == (2, 1) and snap.by_op["get"].failed == 1 and snap.failover_reads == {"n3": 1}
    assert snap.window_s > 0
    empty = gw.stats.take_window()
    assert (empty.ok, empty.failed, empty.by_op, empty.failover_reads) == (0, 0, {}, {})


class _Resp:
    status_code = 204


async def test_report_body(monkeypatch):
    cfg = load_config("vault.yaml")
    init_rpc("gw", cfg, NetSim("gw"))
    gw = Gateway(cfg)
    gw.pinger.row = {"meta": Reach(ok=True, rtt_ms=0.8), "n2": Reach(ok=False)}
    gw.stats.record("put", True)
    sent = []

    async def fake_request(target, method, path, **kw):
        sent.append((target, method, path, kw["json"]))
        return _Resp()
    monkeypatch.setattr(get_rpc(), "request", fake_request)
    assert await report_once(gw) is True
    target, method, path, body = sent[0]
    assert (target, method, path) == ("meta", "POST", "/v1/participants/gw/report")
    assert body["reach"]["n2"] == {"ok": False, "rtt_ms": None} and body["stats"]["by_op"]["put"]["ok"] == 1
    assert gw.stats.take_window().ok == 0                 # the window was consumed by the report


async def test_read_skips_holder_metadata_calls_down(cluster):  # noqa: F811
    c = cluster["client"]
    data = os.urandom(20_000)
    assert (await c.put("/clinic/skip.bin", content=data)).status_code == 200
    gw = cluster["gw"].state.gw
    gw.pinger.states = {"n1": "DOWN"}                      # frag 0 lives on n1 (first in plan order)
    down(cluster, "n1")
    g = await c.get("/clinic/skip.bin")
    assert g.status_code == 200 and g.content == data
    assert g.headers["x-vault-read-path"] == "direct"      # went straight to n2: no failover, no report
    assert dict(gw.stats.failover) == {} and cluster["state"]["reports"] == []
