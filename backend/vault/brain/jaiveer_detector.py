"""Failure detector: tick every 100 ms, φ ≥ 8 → SUSPECT, peer evidence → PARTITIONED, confirm → DOWN, grace → DEAD.
§4.5 state machine, §4.6 epochs, §4.7 metadata's own pings. Task J6. Owner: Jaiveer.

Entry point for loops: async def run(ctx) -> None (started by jaiveer_app). ctx is the metadata Brain
(BrainContext + membership + faults). tick(ctx, now) is separate so tests can drive time.

Transitions made here (timed); recoveries on heartbeat are in jaiveer_membership.heartbeat:
  ALIVE        φ ≥ phi_suspect                                  → SUSPECT      node.suspect
  SUSPECT      a participant reached it ok in the last 2 s      → PARTITIONED  node.partitioned
  SUSPECT      no evidence for confirm_ms                       → DOWN         node.down        (not in startup grace)
  PARTITIONED  nobody reaches it any more                       → DOWN         node.down        (not in startup grace)
  JOINING      no heartbeat for startup_grace_s                 → DOWN                          (never came back)
  DOWN         dead_after_s since its last heartbeat            → DEAD         node.dead        (epoch++, fragments lost,
                                                                                                 incident, repair jobs)
Flags: slow (p50 RTT > slow_rtt_ms or ≥ 20% ping loss, from other participants' reach rows) → node.slow;
fenced (from the heartbeat) → node.fenced; route direct↔relay → link.relayed / link.restored.
"""
import asyncio
import logging
import statistics
import time
from typing import Any, Optional

from vault.common.config import CONTROL_DISPLAY_NAMES
from vault.common.models import JobKind, JobReason, NodeState, Policy, Reach
from vault.common.rpc import NetworkError
from vault.brain.jaiveer_incidents import open_incident_tx
from vault.metadata.jaiveer_db import all_rows

log = logging.getLogger("detector")

TICK_S = 0.1
EVIDENCE_WINDOW_S = 2.0
SLOW_LOSS = 0.20


class DetectorState:
    def __init__(self):
        self.prev_route: dict[str, str] = {}
        self.prev_fenced: dict[str, bool] = {}
        self.prev_slow: dict[str, bool] = {}


def _in_grace(ctx, now_mono: float) -> bool:
    return now_mono - ctx.started_mono < ctx.cfg.detector.startup_grace_s


def _name(ctx, pid: str) -> str:
    return CONTROL_DISPLAY_NAMES.get(pid) or ctx.membership.display_name(pid)


SLOW_WARMUP_S = 5.0          # a machine (or a reporter) that came up less than this ago isn't judged / trusted
SLOW_MIN_REPORTERS = 2       # the loss rule needs at least this many peers' pings


def _warm(ctx, pid: str, now_mono: float) -> bool:
    m = ctx.membership.members.get(pid)
    if m is None:                                   # gw / meta rows: trusted once metadata is past its grace
        return not _in_grace(ctx, now_mono)
    return m.view.state == NodeState.ALIVE and now_mono - m.state_since >= SLOW_WARMUP_S


def _slow(ctx, node_id: str, now_mono: float) -> tuple[bool, Optional[float], str]:
    """Other participants' pings to node_id: p50 RTT > slow_rtt_ms, or ≥ 20% loss seen by ≥ 2 warmed-up peers.
    → (slow, p50, why). Never during startup grace or the node's first SLOW_WARMUP_S (peers still booting)."""
    limit = ctx.cfg.detector.slow_rtt_ms
    if _in_grace(ctx, now_mono) or not _warm(ctx, node_id, now_mono):
        return False, None, ""
    rtts, total, lost = [], 0, 0
    for reporter, row in ctx.membership.reach_rows(now_mono).items():
        if reporter == node_id or not _warm(ctx, reporter, now_mono):
            continue
        r = row.get(node_id)
        if r is None:
            continue
        total += 1
        if not r.ok:
            lost += 1
        elif r.rtt_ms is not None:
            rtts.append(r.rtt_ms)
    if total == 0:
        return False, None, ""
    p50 = statistics.median(rtts) if rtts else None
    if p50 is not None and p50 > limit:
        return True, p50, f"p50 rtt {round(p50)}ms > {limit}ms"
    if total >= SLOW_MIN_REPORTERS and lost / total >= SLOW_LOSS:
        return True, p50, f"ping loss {lost}/{total} ≥ {int(SLOW_LOSS * 100)}%"
    return False, p50, ""


async def tick(ctx, now_mono: float, st: Optional[DetectorState] = None) -> None:
    st = st or getattr(ctx, "_detector_state", None) or DetectorState()
    ctx._detector_state = st
    ms, d = ctx.membership, ctx.cfg.detector
    grace = _in_grace(ctx, now_mono)
    for m in list(ms.members.values()):
        v = m.view
        state = v.state
        if m.last_hb is not None and state in (NodeState.ALIVE, NodeState.SUSPECT, NodeState.PARTITIONED,
                                               NodeState.DOWN):
            v.phi = round(m.phi.phi(now_mono), 2)
        since_hb = (now_mono - m.last_hb) if m.last_hb is not None else None

        if state == NodeState.ALIVE and v.phi >= d.phi_suspect:
            ms.set_state(m, NodeState.SUSPECT, now_mono)
            m.silent_since_wall = m.last_hb_wall
            await ctx.emit("node.suspect", {"node": v.id}, {"phi": v.phi, "threshold": d.phi_suspect,
                                                            "s": round(since_hb or 0.0, 1)})
        elif state == NodeState.SUSPECT:
            peer = ms.evidence(v.id, now_mono, EVIDENCE_WINDOW_S)
            if peer is not None:
                m.last_evidence = now_mono
                ms.set_state(m, NodeState.PARTITIONED, now_mono)
                await ctx.emit("node.partitioned", {"node": v.id}, {"peer_id": peer},
                               peer=_name(ctx, peer), src="meta")
            elif not grace and now_mono - m.state_since >= d.confirm_ms / 1000:
                await _mark_down(ctx, m, now_mono)
        elif state == NodeState.PARTITIONED:
            if ms.evidence(v.id, now_mono, EVIDENCE_WINDOW_S) is not None:
                m.last_evidence = now_mono
            elif not grace:
                await _mark_down(ctx, m, now_mono)
        elif state == NodeState.JOINING and m.last_hb is None and not grace \
                and now_mono - max(m.state_since, ctx.started_mono) >= d.startup_grace_s:
            m.silent_since_wall = m.silent_since_wall or ctx.started_wall
            await _mark_down(ctx, m, now_mono)
        elif state == NodeState.DOWN and not grace:
            # grace counts from the last sign of life: a heartbeat or a peer reaching it (a long partition that
            # ends in a real failure still gets the full dead_after_s)
            alive_at = max([t for t in (m.last_hb, m.last_evidence) if t is not None], default=m.state_since)
            if now_mono - alive_at >= d.dead_after_s:
                await mark_dead(ctx, m, now_mono)

        # flags (live machines only)
        if v.state in (NodeState.ALIVE, NodeState.PARTITIONED, NodeState.SUSPECT):
            slow, p50, why = _slow(ctx, v.id, now_mono)
            v.slow = slow
            if slow and not st.prev_slow.get(v.id):
                await ctx.emit("node.slow", {"node": v.id},
                               {"ms": round(p50) if p50 is not None else None, "limit": d.slow_rtt_ms, "why": why})
            st.prev_slow[v.id] = slow
            if v.fenced and not st.prev_fenced.get(v.id):
                await ctx.emit("node.fenced", {"node": v.id}, {"s": 0})
            st.prev_fenced[v.id] = v.fenced
            prev = st.prev_route.get(v.id, "direct")
            if v.route != prev:
                if v.route.startswith("relay:"):
                    via = v.route.split(":", 1)[1]
                    await ctx.emit("link.relayed", {"node": v.id}, {"relay_id": via},
                                   a=_name(ctx, "meta"), b=v.display_name, relay=_name(ctx, via), r=via, dir="both")
                elif prev.startswith("relay:"):
                    await ctx.emit("link.restored", {"node": v.id}, {}, a=_name(ctx, "meta"), b=v.display_name)
                st.prev_route[v.id] = v.route
        else:
            v.slow = False
        v.faults = ctx.faults.faults_for(v.id)


async def _mark_down(ctx, m, now_mono: float) -> None:
    ms = ctx.membership
    ms.set_state(m, NodeState.DOWN, now_mono)
    m.down_wall = time.time()
    if m.silent_since_wall is None:
        m.silent_since_wall = m.last_hb_wall or m.down_wall
    await ctx.emit("node.down", {"node": m.view.id}, {"phi": m.view.phi, "confirm": ctx.cfg.detector.confirm_ms / 1000})


async def mark_dead(ctx, m, now_mono: float) -> dict[str, Any]:
    """DOWN → DEAD: epoch++, fragments → lost, incident, repair jobs for fragments no longer durable anywhere."""
    ms, v = ctx.membership, m.view
    node_id, e1 = v.id, v.epoch
    now = time.time()
    not_durable = {n.id for n in ms.nodes() if n.state in (NodeState.DEAD, NodeState.RETIRED)} | {node_id}
    silent_since = m.silent_since_wall or m.last_hb_wall or m.down_wall or now
    fault_at = ctx.faults.fault_at(("node_dead", "power_cut"), node_id, not_before=m.down_wall or now) or silent_since
    detected_at = m.down_wall or now

    async def fn(c):
        e2 = e1 + 1
        await c.execute("UPDATE nodes SET persisted_state='DEAD', epoch=? WHERE id=?", (e2, node_id))
        lost = await all_rows(c, "SELECT f.chunk_id, f.frag_idx, v.policy, v.state AS vstate, v.kind FROM fragments f "
                                 "JOIN chunks ch ON ch.chunk_id = f.chunk_id "
                                 "JOIN versions v ON v.version_id = ch.version_id "
                                 "WHERE f.node_id = ? AND f.state IN ('ok','incoming')", (node_id,))
        await c.execute("UPDATE fragments SET state='lost', updated_at=? WHERE node_id=? AND state IN ('ok','incoming')",
                        (now, node_id))
        needs: list[tuple[str, int, int]] = []
        for r in lost:
            if r["vstate"] != "committed" or r["kind"] != "data":
                continue
            policy = Policy.model_validate_json(r["policy"])
            rows = await all_rows(c, "SELECT frag_idx, node_id FROM fragments WHERE chunk_id=? AND state='ok'",
                                  (r["chunk_id"],))
            durable = {x["frag_idx"] for x in rows if x["node_id"] not in not_durable}
            if r["frag_idx"] in durable:
                continue
            prio = 0 if len(durable) <= policy.needed else 1
            needs.append((r["chunk_id"], r["frag_idx"], prio))
        chunks = len({cid for cid, _, _ in needs})
        inc = await open_incident_tx(c, "node_dead", node_id, fault_at, detected_at, chunks)
        from vault.metadata.jaiveer_app import enqueue_job_tx
        for cid, fi, prio in needs:
            await enqueue_job_tx(c, JobKind.repair, JobReason.under_replicated, chunk_id=cid, frag_idx=fi,
                                 priority=prio, incident_id=inc)
        return {"e2": e2, "n": len(lost), "chunks": chunks, "inc": inc}

    res = await ctx.db.write(fn)
    v.epoch = res["e2"]
    m.persisted_state = NodeState.DEAD
    m.registered = False
    ms.set_state(m, NodeState.DEAD, now_mono)
    v.phi, v.slow, v.route = v.phi, False, "direct"
    await ctx.emit("node.dead", {"node": node_id}, {"s": round(now - silent_since), "n": res["n"], "e": e1,
                                                    "e2": res["e2"], "chunks": res["chunks"], "inc": res["inc"],
                                                    "fault_at": fault_at, "detected_at": detected_at})
    ctx.invalidate_snapshot()
    return res


async def ping_once(ctx) -> None:
    """Metadata's own reach row (§4.7): GET /v1/ping on every node, /_vault/health on the gateway."""
    ms = ctx.membership
    timeout = 0.5
    targets = [n.id for n in ms.nodes() if n.state not in (NodeState.DEAD, NodeState.RETIRED)]

    async def one(pid: str, path: str) -> tuple[str, Reach]:
        t0 = time.perf_counter()
        try:
            r = await ctx.rpc.request(pid, "GET", path, allow_relay=False, timeout=timeout)
            ok = r.status_code < 500
        except NetworkError:
            ok = False
        except Exception:                          # bad address, etc.
            ok = False
        return pid, Reach(ok=ok, rtt_ms=round((time.perf_counter() - t0) * 1000, 1) if ok else None)

    results = await asyncio.gather(*(one(p, "/v1/ping") for p in targets), one("gw", "/_vault/health"))
    ms.participant_report("meta", dict(results), time.monotonic())


async def _ping_loop(ctx) -> None:
    period = ctx.cfg.detector.ping_ms / 1000
    while True:
        try:
            await ping_once(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ping round failed")
        await asyncio.sleep(period)


async def run(ctx) -> None:
    pinger = asyncio.create_task(_ping_loop(ctx), name="meta-pinger")
    try:
        while True:
            try:
                await tick(ctx, time.monotonic())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("detector tick failed")
            await asyncio.sleep(TICK_S)
    finally:
        pinger.cancel()
