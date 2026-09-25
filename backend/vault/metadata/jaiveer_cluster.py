"""Build the Snapshot (§8.2) from membership + DB; GET /v1/cluster, /v1/config, PATCH /v1/config, POST /v1/mode,
node register/heartbeat/labels/decommission routes. Task J6 (J4 part: register, heartbeat, inventory, labels,
config, mode). Owner: Jaiveer.
"""
import json
import time
from typing import Any

from fastapi import APIRouter, Body, Request
from pydantic import ValidationError

from vault.common.config import SafetyCfg
from vault.common.events import MODE_SENTENCE
from vault.common.models import (Heartbeat, HeartbeatReply, Inventory, InventoryResult, LabelsPatch, ModeRequest,
                                 ModeResult, NodeState, NodeView, RegisterReply, RegisterRequest)
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
    m, accepted = brain.membership.heartbeat(node_id, hb, via, time.monotonic())
    lease = brain.cfg.detector.lease_ttl_ms
    if not accepted:
        # Unknown, dead, stale epoch, or not registered since metadata restarted → "re-register" (§4.6).
        epoch = m.view.epoch if m else hb.epoch
        return HeartbeatReply(state=NodeState.DEAD, epoch=epoch, lease_ttl_ms=lease,
                              config_version=brain.config_version)
    return HeartbeatReply(state=m.view.state, epoch=m.view.epoch, lease_ttl_ms=lease,
                          config_version=brain.config_version)


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
