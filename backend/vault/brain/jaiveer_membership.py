"""In-memory node table (NodeView), epochs, leases, register/rejoin, route via relay (X-Vault-Via). §4.5–4.6.
Owner: Jaiveer. J4 = table + register + heartbeat bookkeeping; J6 = state machine support (the detector loop in
jaiveer_detector.py drives the timed transitions; heartbeats drive the recoveries here).

Persisted (nodes table): id, addr, display_name, labels, capacity, persisted_state (ALIVE|DEAD|DRAINING|RETIRED), epoch.
In memory only: live state (JOINING/ALIVE/SUSPECT/…), φ, heartbeat times, route, reach rows, disk stats, fenced, slow.

Startup: every node row loads as JOINING (DEAD/DRAINING/RETIRED keep their state). The first heartbeat of a
JOINING node makes it ALIVE. A heartbeat from a DEAD node, from an unknown node, or with a stale epoch gets
state=DEAD in the reply, which tells the node to re-register (§4.6).
Register: unknown ids (n7+) are created from the request. DEAD → REJOINING (epoch already bumped at death);
REJOINING → ALIVE once its inventory has been received (§4.10). RETIRED stays RETIRED.
Heartbeat recoveries (§4.5): SUSPECT/PARTITIONED → ALIVE at once; DOWN → ALIVE after 3 heartbeats within 2 s
(node.recovered_in_grace).
"""
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from vault.common.jaiveer_phi import Phi
from vault.common.models import Heartbeat, NodeState, NodeView, Reach, RegisterRequest, ScrubStatus

REACH_FRESH_S = 3.0          # a reach entry older than this is "unknown" (§4.7)
DOWN_RECOVER_BEATS = 3       # DOWN → ALIVE needs 3 heartbeats …
DOWN_RECOVER_WINDOW_S = 2.0  # … within 2 s


@dataclass
class Member:
    view: NodeView
    persisted_state: NodeState = NodeState.ALIVE
    phi: Phi = field(default_factory=Phi)
    last_hb: Optional[float] = None          # monotonic
    last_hb_wall: Optional[float] = None     # time.time(), for incident fault_at
    hb_times: deque = field(default_factory=lambda: deque(maxlen=8))
    reach: dict[str, Reach] = field(default_factory=dict)
    reach_at: Optional[float] = None         # monotonic time of the last reach row
    scrub: ScrubStatus = field(default_factory=ScrubStatus)
    registered: bool = False                 # registered since this metadata process started
    inventory_seen: bool = False
    state_since: float = field(default_factory=time.monotonic)
    down_wall: Optional[float] = None        # when it went DOWN (wall)
    silent_since_wall: Optional[float] = None  # last heartbeat before the current silence (wall)
    last_evidence: Optional[float] = None    # monotonic: last time a peer reached it while it was silent


@dataclass
class Participant:
    """A non-node participant's reach row (gateway via /v1/participants/{pid}/report, metadata's own pings)."""
    reach: dict[str, Reach] = field(default_factory=dict)
    at: float = 0.0                          # monotonic


class Membership:
    def __init__(self, cfg):
        self.cfg = cfg
        self.members: dict[str, Member] = {}
        self.participants: dict[str, Participant] = {"meta": Participant(), "gw": Participant()}
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

    def set_state(self, m: Member, state: NodeState, now_mono: Optional[float] = None) -> None:
        if m.view.state != state:
            m.view.state = state
            m.state_since = now_mono if now_mono is not None else time.monotonic()

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
            self.members[r["id"]] = Member(view=view, persisted_state=ps, phi=self._phi(),
                                           state_since=self.started_at)

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
            self.set_state(m, NodeState.REJOINING)
            m.persisted_state = NodeState.ALIVE
            m.inventory_seen = False
        elif m.persisted_state == NodeState.RETIRED:
            self.set_state(m, NodeState.RETIRED)
        elif m.persisted_state == NodeState.DRAINING:
            self.set_state(m, NodeState.DRAINING)
        else:
            self.set_state(m, NodeState.JOINING)
        m.phi = self._phi()
        m.last_hb = None
        m.hb_times.clear()
        m.down_wall = None
        m.last_evidence = None
        m.registered = True
        row = {"addr": v.addr, "capacity_bytes": v.capacity, "persisted_state": m.persisted_state.value,
               "epoch": v.epoch}
        return m, is_new, row

    # ── heartbeat ──
    def heartbeat(self, node_id: str, hb: Heartbeat, via: Optional[str],
                  now_mono: float) -> tuple[Optional[Member], bool, Optional[dict[str, Any]]]:
        """Record an arrival. → (member or None, accepted, recovery).
        Not accepted = reply DEAD (re-register). recovery = {"s": seconds silent} when DOWN → ALIVE."""
        m = self.members.get(node_id)
        if m is None:
            return None, False, None
        v = m.view
        if v.state in (NodeState.DEAD, NodeState.RETIRED) or hb.epoch != v.epoch or not m.registered:
            return m, False, None
        m.phi.heartbeat(now_mono)
        m.hb_times.append(now_mono)
        m.last_hb, m.last_hb_wall = now_mono, time.time()
        v.route = f"relay:{via}" if via else "direct"
        v.disk_used, v.fragments, v.fenced = hb.disk_used, hb.fragments, hb.fenced
        if hb.capacity:
            v.capacity = hb.capacity
        m.reach, m.reach_at, m.scrub = dict(hb.reach), now_mono, hb.scrub
        v.phi = 0.0
        recovery = None
        if v.state == NodeState.JOINING:
            self.set_state(m, NodeState.ALIVE, now_mono)
        elif v.state == NodeState.REJOINING and m.inventory_seen:
            self.set_state(m, NodeState.ALIVE, now_mono)
        elif v.state in (NodeState.SUSPECT, NodeState.PARTITIONED):
            self.set_state(m, NodeState.ALIVE, now_mono)
            m.silent_since_wall = None
        elif v.state == NodeState.DOWN:
            recent = [t for t in m.hb_times if now_mono - t <= DOWN_RECOVER_WINDOW_S]
            if len(recent) >= DOWN_RECOVER_BEATS:
                silent = (time.time() - m.silent_since_wall) if m.silent_since_wall else 0.0
                self.set_state(m, NodeState.ALIVE, now_mono)
                m.down_wall, m.silent_since_wall = None, None
                recovery = {"s": round(silent, 1)}
        return m, True, recovery

    def inventory_received(self, node_id: str) -> None:
        m = self.members.get(node_id)
        if m is None:
            return
        m.inventory_seen = True
        if m.view.state == NodeState.REJOINING:
            self.set_state(m, NodeState.ALIVE)

    def set_labels(self, node_id: str, labels: dict[str, str], display_name: Optional[str]) -> Optional[NodeView]:
        m = self.members.get(node_id)
        if m is None:
            return None
        m.view.labels = dict(labels)
        if display_name:
            m.view.display_name = display_name
        return m.view

    # ── reachability (§4.7) ──
    def participant_report(self, pid: str, reach: dict[str, Reach], now_mono: float) -> None:
        p = self.participants.setdefault(pid, Participant())
        p.reach, p.at = dict(reach), now_mono

    def reach_rows(self, now_mono: float) -> dict[str, dict[str, Reach]]:
        """Fresh rows only: reporter → {target: Reach}. Nodes (from heartbeats), gw, meta (own pings)."""
        rows: dict[str, dict[str, Reach]] = {}
        for nid, m in self.members.items():
            if m.reach_at is not None and now_mono - m.reach_at <= REACH_FRESH_S:
                rows[nid] = m.reach
        for pid, p in self.participants.items():
            if p.at and now_mono - p.at <= REACH_FRESH_S:
                rows[pid] = p.reach
        return rows

    def evidence(self, node_id: str, now_mono: float, within_s: float = 2.0) -> Optional[str]:
        """A participant (not the node itself) that reached node_id ok within `within_s` → its id, else None.
        Nodes are preferred over gw/meta so the event can say "{peer} can reach it"."""
        best = None
        for reporter, row in self.reach_rows(now_mono).items():
            if reporter == node_id:
                continue
            at = (self.members[reporter].reach_at if reporter in self.members else self.participants[reporter].at)
            r = row.get(node_id)
            if r is not None and r.ok and at is not None and now_mono - at <= within_s:
                if reporter in self.members:
                    return reporter
                best = best or reporter
        return best
