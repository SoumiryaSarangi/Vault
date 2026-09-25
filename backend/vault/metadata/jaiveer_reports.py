"""POST /v1/reports/fragment, POST /v1/participants/{pid}/report, POST /v1/incidents/fault, POST /v1/scrub (fan-out).
Task J7. Owner: Jaiveer.

J6 part (needed by the detector and the snapshot): /v1/incidents/fault (supervisor ground truth for fault_at and
the node fault chips) and /v1/participants/{pid}/report (gateway reach row + traffic stats).
J7 adds fragment reports, read.failover aggregation and scrub fan-out.
"""
import asyncio
import time
from typing import Optional

from fastapi import APIRouter, Request, Response

from vault.common import ids
from vault.common.models import (FaultReport, FragmentReport, GatewayStats, Job, JobKind, JobReason, NodeState,
                                 ParticipantReport, Policy, RepairStatus, ScrubRequest)
from vault.common.rpc import NetworkError
from vault.common.service import VaultHTTPError
from vault.brain.jaiveer_incidents import corruption_incident
from vault.metadata.jaiveer_db import all_rows


def enqueue_job_tx(*a, **kw):
    from vault.metadata.jaiveer_app import enqueue_job_tx as f
    return f(*a, **kw)

router = APIRouter()


def _brain(request: Request):
    return request.app.state.brain


@router.post("/v1/incidents/fault", status_code=204)
async def fault(request: Request, body: FaultReport) -> Response:
    brain = _brain(request)
    brain.faults.record(body.kind, body.subject, body.at)
    brain.invalidate_snapshot()
    return Response(status_code=204)


@router.post("/v1/participants/{pid}/report", status_code=204)
async def participant_report(request: Request, pid: str, body: ParticipantReport) -> Response:
    brain = _brain(request)
    now = time.monotonic()
    brain.membership.participant_report(pid, body.reach, now)
    if body.stats is not None:
        brain.gw_stats = (body.stats, now)
        from vault.brain.jaiveer_metrics import record_gateway_stats
        record_gateway_stats(brain, body.stats)
        await _aggregate_failover(brain, body.stats)
    return Response(status_code=204)


# ── J7 ──

FAILOVER_EVERY_S = 10.0


@router.post("/v1/reports/fragment", status_code=202)
async def fragment_report(request: Request, body: FragmentReport) -> Response:
    """Read / scrub / repair found a damaged or missing copy (§4.9 step 4): row → corrupt|missing, event, repair job
    (P0 if that leaves ≤ needed durable copies, else P1), attached to an open corruption incident."""
    brain = _brain(request)
    if body.problem not in ("corrupt", "missing"):
        return Response(status_code=202)              # unreachable/slow: routing info only, the detector handles it
    try:
        _, chunk_id, _, frag_idx = ids.parse_fid(body.fid)
    except ValueError:
        raise VaultHTTPError(400, "bad_fid", "That fragment id isn't valid.", {"fid": body.fid})
    info = await brain.db.fetchone(
        "SELECT v.key, v.policy, v.state FROM chunks ch JOIN versions v ON v.version_id = ch.version_id "
        "WHERE ch.chunk_id=?", (chunk_id,))
    row = await brain.db.fetchone("SELECT state FROM fragments WHERE chunk_id=? AND frag_idx=? AND node_id=?",
                                  (chunk_id, frag_idx, body.node_id))
    if info is None or row is None or row["state"] != "ok":
        return Response(status_code=202)              # unknown, or already known bad / being replaced
    policy = Policy.model_validate_json(info["policy"])
    live = info["state"] == "committed"
    inc = await corruption_incident(brain, body.node_id) if live else None
    not_durable = {n.id for n in brain.nodes() if n.state in (NodeState.DEAD, NodeState.RETIRED)} | {body.node_id}
    now = time.time()

    async def fn(c):
        await c.execute("UPDATE fragments SET state=?, updated_at=? WHERE chunk_id=? AND frag_idx=? AND node_id=?",
                        (body.problem, now, chunk_id, frag_idx, body.node_id))
        if not live:
            return
        rows = await all_rows(c, "SELECT frag_idx, node_id FROM fragments WHERE chunk_id=? AND state='ok'", (chunk_id,))
        durable = {r["frag_idx"] for r in rows if r["node_id"] not in not_durable}
        if frag_idx in durable:
            return                                     # another good copy of this piece exists (e.g. mid-move)
        prio = 0 if len(durable) <= policy.needed else 1
        reason = JobReason.corrupt if body.problem == "corrupt" else JobReason.missing
        await enqueue_job_tx(c, JobKind.repair, reason, chunk_id=chunk_id, frag_idx=frag_idx, priority=prio,
                             incident_id=inc)
    await brain.db.write(fn)
    if body.problem == "corrupt":
        async def count(c):
            from vault.metadata.jaiveer_db import kv_incr
            await kv_incr(c, "corrupt_found_total")
        await brain.db.write(count)
    ev = "fragment.corrupt" if body.problem == "corrupt" else "fragment.missing"
    await brain.emit(ev, {"node": body.node_id, "key": info["key"]},
                     {"fid": body.fid, "observed_by": body.observed_by},
                     file=info["key"], fid=ids.short_fid(body.fid), ctx=body.context)
    brain.invalidate_snapshot()
    return Response(status_code=202)


async def _aggregate_failover(brain, stats: GatewayStats) -> None:
    """read.failover is aggregated: at most one event per node per 10 s (DESIGN §6.3)."""
    st = getattr(brain, "_failover", None)
    if st is None:
        st = brain._failover = {"counts": {}, "at": time.monotonic()}
    for node, n in (stats.failover_reads or {}).items():
        st["counts"][node] = st["counts"].get(node, 0) + n
    if time.monotonic() - st["at"] < FAILOVER_EVERY_S:
        return
    counts, st["counts"], st["at"] = st["counts"], {}, time.monotonic()
    for node, n in counts.items():
        if n > 0:
            await brain.emit("read.failover", {"node": node}, {"n": n})


@router.post("/v1/scrub", status_code=202)
async def scrub_all(request: Request, body: Optional[ScrubRequest] = None) -> Response:
    """Fan out POST /v1/scrub to the given nodes (default: every live one)."""
    brain = _brain(request)
    targets = (body.nodes if body and body.nodes else
               [n.id for n in brain.nodes() if n.state in (NodeState.ALIVE, NodeState.PARTITIONED, NodeState.SUSPECT)])

    async def one_node(nid: str) -> None:
        try:
            await brain.rpc.request(nid, "POST", "/v1/scrub")
        except NetworkError:
            pass
    await asyncio.gather(*(one_node(n) for n in targets))
    return Response(status_code=202)


@router.get("/v1/repair")
async def repair_status(request: Request) -> RepairStatus:
    brain = _brain(request)
    q = {f"p{p}": 0 for p in range(5)}
    for r in await brain.db.fetchall("SELECT priority, COUNT(*) AS n FROM jobs WHERE state='queued' GROUP BY priority"):
        q[f"p{min(max(r['priority'], 0), 4)}"] += r["n"]
    active = await brain.db.fetchall("SELECT * FROM jobs WHERE state='running' ORDER BY id")
    recent = await brain.db.fetchall("SELECT * FROM jobs WHERE state IN ('done','failed','cancelled') "
                                     "ORDER BY finished_at DESC, id DESC LIMIT 20")
    return RepairStatus(queued=q, active=[Job(**r) for r in active], recent=[Job(**r) for r in recent])
