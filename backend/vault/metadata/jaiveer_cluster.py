"""Build the Snapshot (§8.2) from membership + DB; GET /v1/cluster, /v1/config, PATCH /v1/config, POST /v1/mode,
node register/heartbeat/labels/decommission routes. Task J6 (J4 part: register, heartbeat, inventory, labels,
config, mode). Owner: Jaiveer.
"""
import json
import time
from typing import Any, Optional

from fastapi import APIRouter, Body, Request
from pydantic import ValidationError

from vault.common.config import SafetyCfg
from vault.common.events import MODE_SENTENCE
from vault.common.models import (ActiveJob, ControlView, Heartbeat, HeartbeatReply, Incident, IncidentList,
                                 FateReport, Inventory, Metrics,
                                 InventoryResult, LabelsPatch, LinkView, ModeRequest, ModeResult, NodeState, NodeView,
                                 RegisterReply, RegisterRequest, RepairView, Snapshot, TrafficView)
from vault.common.service import VaultHTTPError

router = APIRouter()

# Structural sections can't change on a running cluster (they'd invalidate placement, addresses, policies).
NOT_PATCHABLE = {"cluster", "nodes", "policies", "placement", "web"}


def _brain(request: Request):
    return request.app.state.brain


# ── nodes ──

@router.post("/v1/nodes/register")
async def register(request: Request, req: RegisterRequest) -> RegisterReply:
    brain = _brain(request)
    m, is_new, row = brain.membership.register(req)
    v = m.view

    async def fn(c):
        if is_new:
            await c.execute("INSERT INTO nodes (id, addr, display_name, labels, capacity_bytes, persisted_state, "
                            "epoch, created_at) VALUES (?,?,?,?,?,?,?,?)",
                            (v.id, v.addr, v.display_name, json.dumps(v.labels), v.capacity,
                             m.persisted_state.value, v.epoch, time.time()))
        else:
            await c.execute("UPDATE nodes SET addr=?, capacity_bytes=?, persisted_state=?, epoch=? WHERE id=?",
                            (row["addr"], row["capacity_bytes"], row["persisted_state"], row["epoch"], v.id))
    await brain.db.write(fn)
    if req.node_id not in brain.cfg.nodes and brain.rpc is not None:
        brain.rpc.set_addr(req.node_id, req.addr)
    await brain.emit("node.joined", {"node": v.id}, {"epoch": v.epoch, "labels": v.labels, "new": is_new})
    if req.discarded_on_startup:
        await brain.emit("node.startup_discarded", {"node": v.id}, {"n": req.discarded_on_startup})
    return RegisterReply(epoch=v.epoch, state=v.state, lease_ttl_ms=brain.cfg.detector.lease_ttl_ms,
                         config_version=brain.config_version)


@router.post("/v1/nodes/{node_id}/heartbeat")
async def heartbeat(request: Request, node_id: str, hb: Heartbeat) -> HeartbeatReply:
    brain = _brain(request)
    if hb.node_id != node_id:
        raise VaultHTTPError(400, "node_mismatch", "Heartbeat body is for a different machine.",
                             {"path": node_id, "body": hb.node_id})
    via = request.headers.get("X-Vault-Via")
    prev = brain.membership.members.get(node_id)
    prev_pass = prev.scrub.last_pass_at if prev else None
    m, accepted, recovery = brain.membership.heartbeat(node_id, hb, via, time.monotonic())
    if accepted and hb.scrub.last_pass_at and prev_pass and hb.scrub.last_pass_at != prev_pass:
        await brain.emit("scrub.completed", {"node": node_id},
                         {"n": hb.scrub.scanned, "c": hb.scrub.corrupt_found, "ms": 0, "mb": 0})
    lease = brain.cfg.detector.lease_ttl_ms
    if recovery is not None:
        await _recovered_in_grace(brain, node_id, recovery["s"])
    if not accepted:
        # Unknown, dead, stale epoch, or not registered since metadata restarted → "re-register" (§4.6).
        epoch = m.view.epoch if m else hb.epoch
        return HeartbeatReply(state=NodeState.DEAD, epoch=epoch, lease_ttl_ms=lease,
                              config_version=brain.config_version)
    return HeartbeatReply(state=m.view.state, epoch=m.view.epoch, lease_ttl_ms=lease,
                          config_version=brain.config_version)


async def _recovered_in_grace(brain, node_id: str, silent_s: float) -> None:
    """DOWN → ALIVE before dead_after_s: no rebuild (§4.5). Counted for metrics (unnecessary_repairs_avoided)."""
    row = await brain.db.fetchone(
        "SELECT COUNT(DISTINCT f.chunk_id) AS n FROM fragments f JOIN chunks ch ON ch.chunk_id = f.chunk_id "
        "JOIN objects o ON o.current_version_id = ch.version_id WHERE f.node_id = ? AND f.state = 'ok'", (node_id,))

    async def fn(c):
        from vault.metadata.jaiveer_db import kv_incr
        await kv_incr(c, "repairs_avoided")
    await brain.db.write(fn)
    await brain.emit("node.recovered_in_grace", {"node": node_id},
                     {"s": silent_s, "x": brain.cfg.detector.dead_after_s, "chunks": row["n"] if row else 0})
    brain.invalidate_snapshot()


@router.post("/v1/nodes/{node_id}/inventory")
async def inventory(request: Request, node_id: str, inv: Inventory) -> InventoryResult:
    """Runs Soum's reconciler (S8) when it exists; always marks the inventory as seen (REJOINING → ALIVE)."""
    brain = _brain(request)
    if brain.membership.node(node_id) is None:
        raise VaultHTTPError(404, "unknown_node", f"No machine called {node_id}.")
    result = InventoryResult()
    try:
        from vault.brain import soum_reconciler
        fn = getattr(soum_reconciler, "reconcile_inventory", None)
        if fn is not None:
            result = await fn(brain, inv)
    except NotImplementedError:
        pass
    brain.membership.inventory_received(node_id)
    return result


@router.patch("/v1/nodes/{node_id}/labels")
async def set_labels(request: Request, node_id: str, body: LabelsPatch) -> NodeView:
    brain = _brain(request)
    view = brain.membership.set_labels(node_id, body.labels, body.display_name)
    if view is None:
        raise VaultHTTPError(404, "unknown_node", f"No machine called {node_id}.")

    async def fn(c):
        await c.execute("UPDATE nodes SET labels=?, display_name=? WHERE id=?",
                        (json.dumps(view.labels), view.display_name, node_id))
    await brain.db.write(fn)
    try:
        from vault.brain import jaiveer_auditor
        req_audit = getattr(jaiveer_auditor, "request_audit", None)
        if req_audit is not None:
            await req_audit(brain, f"labels:{node_id}")
    except NotImplementedError:
        pass
    return view


# ── config / mode ──

@router.get("/v1/config")
async def get_config(request: Request) -> dict[str, Any]:
    return _brain(request).config_body()


@router.patch("/v1/config")
async def patch_config(request: Request, patch: dict[str, Any] = Body(...)) -> dict[str, Any]:
    brain = _brain(request)
    patch = {k: v for k, v in patch.items() if k not in ("config_version", "mode")}
    bad = sorted(set(patch) & NOT_PATCHABLE)
    if bad:
        raise VaultHTTPError(400, "not_patchable", "These settings can't change while Vault is running.",
                             {"keys": bad})
    try:
        await brain.apply_config(patch)
    except (ValidationError, ValueError) as e:
        raise VaultHTTPError(400, "bad_config", "That setting isn't valid.", {"error": str(e)[:500]})
    return brain.config_body()


@router.post("/v1/mode")
async def set_mode(request: Request, req: ModeRequest) -> ModeResult:
    brain = _brain(request)
    changed = req.mode != brain.mode
    safety = SafetyCfg.for_mode(req.mode).model_dump()
    await brain.apply_config({"safety": safety}, mode=req.mode)
    if changed:
        await brain.emit("config.mode_changed", {}, {"mode": req.mode, "flags": safety},
                         human=MODE_SENTENCE[req.mode])
    return ModeResult(mode=req.mode, config_version=brain.config_version)


# ── snapshot (§8.2) ──

SNAPSHOT_TTL_S = 0.5
LINK_STATES = (NodeState.ALIVE, NodeState.PARTITIONED, NodeState.REJOINING, NodeState.DRAINING)
INCIDENT_SHOW_S = 120.0


def _links(brain, now_mono: float) -> list[LinkView]:
    ms = brain.membership
    rows = ms.reach_rows(now_mono)
    live = [n for n in ms.nodes() if n.state in LINK_STATES]

    def ok(a: str, b: str) -> Optional[bool]:
        r = rows.get(a, {}).get(b)
        return None if r is None else r.ok

    def relay_for(a: str, b: str) -> Optional[str]:
        for c in live:
            if c.id not in (a, b) and ok(a, c.id) is True and ok(c.id, b) is True:
                return c.id
        return None

    pairs = [("meta", n.id) for n in live]
    if "gw" in rows:
        pairs += [("gw", n.id) for n in live]
    pairs += [(a.id, b.id) for i, a in enumerate(live) for b in live[i + 1:]]
    out = []
    for a, b in pairs:
        ab, ba = ok(a, b), ok(b, a)
        if ab is False or ba is False:
            relay = None
            if a == "meta":
                route = ms.node(b).route
                relay = route.split(":", 1)[1] if route.startswith("relay:") else relay_for(a, b)
            else:
                relay = relay_for(a, b)
            out.append(LinkView(a=a, b=b, a_to_b=ab is not False, b_to_a=ba is not False, relay=relay))
    return out


async def _repair_view(brain) -> RepairView:
    q = {f"p{p}": 0 for p in range(5)}
    for r in await brain.db.fetchall("SELECT priority, COUNT(*) AS n FROM jobs WHERE state='queued' GROUP BY priority"):
        q[f"p{min(max(r['priority'], 0), 4)}"] += r["n"]
    active = [ActiveJob(job_id=r["id"], kind=r["kind"], reason=r["reason"], src=r["source_node"], dst=r["target_node"],
                        bytes=r["bytes"], progress=brain.job_progress.get(r["id"], 0.0))
              for r in await brain.db.fetchall("SELECT * FROM jobs WHERE state='running' ORDER BY id")]
    return RepairView(queued=q, active=active, mbps=round(brain.repair_mbps, 1))


async def _incident(brain) -> Optional[Incident]:
    r = await brain.db.fetchone("SELECT * FROM incidents ORDER BY id DESC LIMIT 1")
    if r is None or (r["recovered_at"] is not None and time.time() - r["recovered_at"] > INCIDENT_SHOW_S):
        return None
    return Incident(**r)


def _traffic(brain) -> TrafficView:
    stats, at = brain.gw_stats
    if stats is None or time.monotonic() - at > 3.0:
        return TrafficView()
    w = stats.window_s or 1
    op = lambda name: stats.by_op.get(name)
    rate = lambda name: round(((op(name).ok + op(name).failed) / w) if op(name) else 0.0, 1)
    return TrafficView(puts_per_s=rate("put"), gets_per_s=rate("get"))


async def build_snapshot(brain, force: bool = False) -> Snapshot:
    now_mono = time.monotonic()
    if not force and brain._snap is not None and now_mono - brain._snap_at < SNAPSHOT_TTL_S:
        return brain._snap
    from vault.brain.jaiveer_summary import build_summary, file_health
    files = await file_health(brain)
    inc = await _incident(brain)
    pct = None
    if inc is not None and inc.affected_chunks:
        pct = round(100 * (inc.affected_chunks - inc.remaining_chunks) / inc.affected_chunks)
    summary = await build_summary(brain, files, pct)
    links = _links(brain, now_mono)
    gw = brain.membership.reach_rows(now_mono).get("meta", {}).get("gw")
    snap = Snapshot(ts=time.time(), mode=brain.mode, config_version=brain.config_version, summary=summary,
                    nodes=[n.model_copy() for n in brain.nodes()],
                    control=[ControlView(id="meta", ok=True), ControlView(id="gw", ok=bool(gw and gw.ok))],
                    links=links, repair=await _repair_view(brain), incident=inc, traffic=_traffic(brain))
    brain._snap, brain._snap_at = snap, now_mono
    if brain.rpc is not None:
        brain.rpc.update_topology([(n.id, n.state.value) for n in snap.nodes],
                                  [(l.a, l.b, l.a_to_b, l.b_to_a) for l in links])
    return snap


@router.get("/v1/cluster")
async def get_cluster(request: Request) -> Snapshot:
    return await build_snapshot(_brain(request))


# ── J8: metrics + incidents ──

@router.get("/v1/metrics")
async def get_metrics(request: Request) -> Metrics:
    from vault.brain.jaiveer_metrics import build_metrics
    return await build_metrics(_brain(request))


@router.get("/v1/incidents")
async def list_incidents(request: Request, limit: int = 20) -> IncidentList:
    rows = await _brain(request).db.fetchall("SELECT * FROM incidents ORDER BY id DESC LIMIT ?",
                                             (max(1, min(limit, 200)),))
    return IncidentList(incidents=[Incident(**r) for r in rows])


# ── J9: fate report ──

@router.get("/v1/fate")
async def get_fate(request: Request) -> FateReport:
    from vault.brain.jaiveer_auditor import _state, audit
    brain = _brain(request)
    st = _state(brain)
    if st.report is None or time.time() - (st.report.last_audit_at or 0) > 10:
        return await audit(brain)
    return st.report
