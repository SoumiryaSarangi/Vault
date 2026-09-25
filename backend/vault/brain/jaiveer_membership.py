"""In-memory node table (NodeView), epochs, leases, register/rejoin, route via relay (X-Vault-Via). §4.5–4.6.
Owner: Jaiveer. J4 = table + register + heartbeat bookkeeping; J6 adds the detector state machine on top.

Persisted (nodes table): id, addr, display_name, labels, capacity, persisted_state (ALIVE|DEAD|DRAINING|RETIRED), epoch.
In memory only: live state (JOINING/ALIVE/SUSPECT/…), φ, last heartbeat, route, reach rows, disk stats, fenced.

Startup: every node row loads as JOINING (DEAD/DRAINING/RETIRED keep their state). The first heartbeat of a
JOINING node makes it ALIVE. A heartbeat from a DEAD node, from an unknown node, or with a stale epoch gets
state=DEAD in the reply, which tells the node to re-register (§4.6).
Register: unknown ids (n7+) are created from the request. DEAD → REJOINING (epoch already bumped at death);
REJOINING → ALIVE once its inventory has been received (§4.10). RETIRED stays RETIRED.
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from vault.common.jaiveer_phi import Phi
from vault.common.models import Heartbeat, NodeState, NodeView, Reach, RegisterRequest, ScrubStatus

PERSISTED = {NodeState.ALIVE, NodeState.DEAD, NodeState.DRAINING, NodeState.RETIRED}


@dataclass
class Member:
    view: NodeView
    persisted_state: NodeState = NodeState.ALIVE
    phi: Phi = field(default_factory=Phi)
    last_hb: Optional[float] = None          # monotonic
    last_hb_wall: Optional[float] = None     # time.time(), for incident fault_at
    reach: dict[str, Reach] = field(default_factory=dict)
    reach_at: Optional[float] = None         # monotonic time of the last reach row
    scrub: ScrubStatus = field(default_factory=ScrubStatus)
    registered: bool = False                 # registered since this metadata process started
    inventory_seen: bool = False


class Membership:
    def __init__(self, cfg):
        self.cfg = cfg
        self.members: dict[str, Member] = {}
        self.started_at = time.monotonic()

    # ── views ──
    def nodes(self) -> list[NodeView]:
        return [m.view for m in self.members.values()]

    def node(self, node_id: str) -> Optional[NodeView]:
        m = self.members.get(node_id)
        return m.view if m else None

    def display_name(self, node_id: str) -> str:
        m = self.members.get(node_id)
        return m.view.display_name if m else node_id

    def _phi(self) -> Phi:
        d = self.cfg.detector
        return Phi(window=d.window, min_std_s=d.min_std_ms / 1000, first_s=d.heartbeat_ms / 1000)

    # ── load / seed (called from the app lifespan) ──
    @staticmethod
    def seed_rows(cfg, now: float) -> list[tuple]:
        """Rows for nodes in vault.yaml, inserted with INSERT OR IGNORE on every start (first start seeds them)."""
        return [(nid, cfg.addr(nid), nc.display_name, json.dumps(nc.labels), cfg.cluster.node_capacity_bytes,
                 "ALIVE", 1, now) for nid, nc in cfg.nodes.items()]

    def load(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            ps = NodeState(r["persisted_state"])
            live = ps if ps in (NodeState.DEAD, NodeState.DRAINING, NodeState.RETIRED) else NodeState.JOINING
            view = NodeView(id=r["id"], display_name=r["display_name"], addr=r["addr"], state=live,
                            epoch=r["epoch"], labels=json.loads(r["labels"] or "{}"), capacity=r["capacity_bytes"])
            self.members[r["id"]] = Member(view=view, persisted_state=ps, phi=self._phi())

    # ── register ──
    def register(self, req: RegisterRequest) -> tuple[Member, bool, dict[str, Any]]:
        """Update memory; → (member, is_new, row_update). The caller persists row_update (dict of columns)."""
        m = self.members.get(req.node_id)
        is_new = m is None
        if is_new:
            view = NodeView(id=req.node_id, display_name=req.display_name, addr=req.addr, state=NodeState.JOINING,
                            epoch=1, labels=dict(req.labels), capacity=req.capacity_bytes)
            m = Member(view=view, persisted_state=NodeState.ALIVE, phi=self._phi())
            self.members[req.node_id] = m
        v = m.view
        # Metadata is the source of truth for labels and names once a row exists (relabels happen here);
        # the address and capacity come from the node.
        v.addr, v.capacity = req.addr, req.capacity_bytes
        if m.persisted_state == NodeState.DEAD:
            v.state = NodeState.REJOINING
            m.persisted_state = NodeState.ALIVE
            m.inventory_seen = False
        elif m.persisted_state == NodeState.RETIRED:
            v.state = NodeState.RETIRED
        elif m.persisted_state == NodeState.DRAINING:
            v.state = NodeState.DRAINING
        else:
            v.state = NodeState.JOINING
        m.phi = self._phi()
        m.last_hb = None
        m.registered = True
        row = {"addr": v.addr, "capacity_bytes": v.capacity, "persisted_state": m.persisted_state.value,
               "epoch": v.epoch}
        return m, is_new, row

    # ── heartbeat ──
    def heartbeat(self, node_id: str, hb: Heartbeat, via: Optional[str], now_mono: float) -> tuple[Optional[Member], bool]:
        """Record an arrival. → (member or None if unknown, accepted). Not accepted = reply DEAD (re-register)."""
        m = self.members.get(node_id)
        if m is None:
            return None, False
        v = m.view
        if v.state in (NodeState.DEAD, NodeState.RETIRED) or hb.epoch != v.epoch or not m.registered:
            return m, False
        m.phi.heartbeat(now_mono)
        m.last_hb, m.last_hb_wall = now_mono, time.time()
        v.route = f"relay:{via}" if via else "direct"
        v.disk_used, v.fragments, v.fenced = hb.disk_used, hb.fragments, hb.fenced
        if hb.capacity:
            v.capacity = hb.capacity
        m.reach, m.reach_at, m.scrub = dict(hb.reach), now_mono, hb.scrub
        v.phi = 0.0
        if v.state == NodeState.JOINING:
            v.state = NodeState.ALIVE
        elif v.state == NodeState.REJOINING and m.inventory_seen:
            v.state = NodeState.ALIVE
        return m, True

    def inventory_received(self, node_id: str) -> None:
        m = self.members.get(node_id)
        if m is None:
            return
        m.inventory_seen = True
        if m.view.state == NodeState.REJOINING:
            m.view.state = NodeState.ALIVE

    def set_labels(self, node_id: str, labels: dict[str, str], display_name: Optional[str]) -> Optional[NodeView]:
        m = self.members.get(node_id)
        if m is None:
            return None
        m.view.labels = dict(labels)
        if display_name:
            m.view.display_name = display_name
        return m.view
