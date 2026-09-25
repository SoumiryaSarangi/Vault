"""Ping every node + meta every 1 s (timeout 500 ms); reach row goes into heartbeats; feed rpc topology. §4.7. Task S4.
Owner: Soum.

Reusable by the gateway (S7): Pinger(pid, cfg, on_row) with no node object.
  * Reach: nodes answer GET /v1/ping; meta/gw have no /v1/ping, so they get GET /_vault/health (every
    service has it). Any HTTP answer = reachable; NetworkError (netsim block, refused, timeout) =
    unreachable. Direct only, never relayed.
  * Topology (agreed with Jaiveer): GET meta /v1/cluster every tick, relay allowed; feed
    rpc.update_topology(nodes, links). Any error (404 until J6) keeps the last topology.
"""
import asyncio
import logging
import time
from typing import Callable, Optional

from vault.common.config import VaultConfig
from vault.common.models import Reach
from vault.common.rpc import NetworkError, get_rpc

log = logging.getLogger("pinger")

PING_TIMEOUT_S = 0.5


class Pinger:
    def __init__(self, pid: str, cfg: VaultConfig, on_row: Optional[Callable[[dict[str, Reach]], None]] = None):
        self.pid = pid
        self.cfg = cfg
        self.on_row = on_row
        self.row: dict[str, Reach] = {}
        self.node_ids: list[str] = list(cfg.node_ids())   # replaced by the snapshot's list (adds n7+)
        self.have_topology = False

    def targets(self) -> list[str]:
        return ["meta"] + [n for n in self.node_ids if n != self.pid]

    async def ping(self, target: str) -> Reach:
        t0 = time.perf_counter()
        path = "/v1/ping" if target.startswith("n") else "/_vault/health"
        try:
            await get_rpc().request(target, "GET", path, timeout=PING_TIMEOUT_S, allow_relay=False)
        except NetworkError:
            return Reach(ok=False, rtt_ms=None)
        return Reach(ok=True, rtt_ms=round((time.perf_counter() - t0) * 1000, 2))

    async def refresh_topology(self) -> None:
        try:
            r = await get_rpc().request("meta", "GET", "/v1/cluster")
        except NetworkError:
            return
        if r.status_code != 200:
            return                                  # 404 until J6: no topology yet, keep what we have
        try:
            snap = r.json()
            nodes = [(n["id"], n["state"]) for n in snap.get("nodes", [])]
            links = [(l["a"], l["b"], l["a_to_b"], l["b_to_a"]) for l in snap.get("links", [])]
        except (ValueError, KeyError, TypeError):
            return
        get_rpc().update_topology(nodes, links)
        if nodes:
            self.node_ids = [n for n, _ in nodes]
        if not self.have_topology:
            log.info("topology from metadata snapshot: %d nodes, %d abnormal links", len(nodes), len(links))
            self.have_topology = True

    async def tick(self) -> dict[str, Reach]:
        targets = self.targets()
        results = await asyncio.gather(*(self.ping(t) for t in targets), self.refresh_topology())
        self.row = dict(zip(targets, results[:len(targets)]))
        if self.on_row:
            self.on_row(self.row)
        return self.row

    async def run(self) -> None:
        interval = self.cfg.detector.ping_ms / 1000
        while True:
            started = time.monotonic()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("pinger error")
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))
