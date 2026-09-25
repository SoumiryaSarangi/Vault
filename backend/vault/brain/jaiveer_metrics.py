"""Metrics (PRD §8 exact definitions, ARCHITECTURE §8.3 `Metrics`). Task J8. Owner: Jaiveer.

availability_60s   successful ÷ all client operations at the gateway over the last 60 s (gateway classifies
                   404/412 as successes), from POST /v1/participants/gw/report stats. No traffic → 1.0.
readable_now_pct   % of live files with ≥ needed distinct pieces on reachable machines.
overhead           raw bytes of healthy pieces of live versions ÷ logical bytes of live objects; cluster + per policy.
last_incident      most recent incident that had something to repair: detect = detected − fault, grace =
                   repair_started − detected, repair = recovered − repair_started, mttr = recovered − fault
                   (the three parts add up to mttr). Open incidents show the parts known so far.
incidents_avg_mttr_s, unnecessary_repairs_avoided (DOWN → back before dead_after_s), repair bytes/MB/s,
rebalance.last (kv "rebalance_last", written by Soum's rebalancer), ifl histogram/min/at-risk, scrub totals.
"""
import json
import time
from typing import Optional

from vault.brain.jaiveer_summary import NOT_DURABLE, file_health
from vault.common.models import (IflMetric, LastIncident, Metrics, OverheadMetric, Policy, RebalanceLast,
                                 RebalanceMetric, RepairMetric, ScrubMetric)

AVAILABILITY_WINDOW_S = 60.0


def record_gateway_stats(ctx, stats) -> None:
    """Called for every gateway report: keep (monotonic, ok, failed) for the rolling window."""
    w = getattr(ctx, "_gw_window", None)
    if w is None:
        from collections import deque
        w = ctx._gw_window = deque()
    now = time.monotonic()
    w.append((now, stats.ok, stats.failed))
    while w and now - w[0][0] > AVAILABILITY_WINDOW_S:
        w.popleft()


def availability(ctx) -> float:
    w = getattr(ctx, "_gw_window", None) or []
    now = time.monotonic()
    ok = sum(o for t, o, f in w if now - t <= AVAILABILITY_WINDOW_S)
    total = ok + sum(f for t, o, f in w if now - t <= AVAILABILITY_WINDOW_S)
    return round(ok / total, 4) if total else 1.0


def incident_parts(inc: dict) -> LastIncident:
    fault = inc.get("fault_at") or inc.get("detected_at")
    det, rs, rec = inc.get("detected_at"), inc.get("repair_started_at"), inc.get("recovered_at")
    r1 = lambda a, b: round(b - a, 1) if a is not None and b is not None else None
    return LastIncident(id=inc["id"], detect_s=r1(fault, det), grace_s=r1(det, rs), repair_s=r1(rs, rec),
                        mttr_s=r1(fault, rec), bytes=inc.get("bytes_repaired") or 0)


async def build_metrics(ctx) -> Metrics:
    db = ctx.db
    files = await file_health(ctx)
    readable = 100.0 * sum(1 for f in files if not f.unreadable) / len(files) if files else 100.0

    # overhead, cluster and per policy
    states = {n.id: n.state for n in ctx.nodes()}
    raw_rows = await db.fetchall(
        "SELECT v.policy, f.node_id, ch.frag_size FROM fragments f JOIN chunks ch ON ch.chunk_id = f.chunk_id "
        "JOIN objects o ON o.current_version_id = ch.version_id JOIN versions v ON v.version_id = ch.version_id "
        "WHERE f.state='ok' AND v.kind='data' AND v.state='committed'")
    raw_by, logical_by = {}, {}
    for r in raw_rows:
        if r["node_id"] in states and states[r["node_id"]] not in NOT_DURABLE:
            name = json.loads(r["policy"])["name"]
            raw_by[name] = raw_by.get(name, 0) + r["frag_size"]
    for f in files:
        logical_by[f.policy.name] = logical_by.get(f.policy.name, 0) + f.size
    by_policy = {p: round(raw_by.get(p, 0) / lb, 2) for p, lb in logical_by.items() if lb}
    total_logical = sum(logical_by.values())
    overhead = OverheadMetric(cluster=round(sum(raw_by.values()) / total_logical, 2) if total_logical else 0.0,
                              by_policy=by_policy)

    # incidents
    incs = await db.fetchall("SELECT * FROM incidents WHERE affected_chunks > 0 ORDER BY id DESC")
    last = incident_parts(incs[0]) if incs else None
    mttrs = [incident_parts(i).mttr_s for i in incs if i["recovered_at"] is not None]
    avg = round(sum(mttrs) / len(mttrs), 1) if mttrs else None

    avoided = int(await db.kv_get("repairs_avoided", "0") or 0)
    bytes_total = (await db.fetchone("SELECT COALESCE(SUM(bytes), 0) AS b FROM jobs "
                                     "WHERE state='done' AND kind IN ('repair','move')"))["b"]
    reb = await db.kv_get("rebalance_last")
    rebalance = RebalanceMetric(last=RebalanceLast.model_validate_json(reb) if reb else None)

    hist: dict[str, int] = {}
    for f in files:
        hist[str(f.min_ifl)] = hist.get(str(f.min_ifl), 0) + 1
    ifl = IflMetric(histogram=dict(sorted(hist.items())), min=min((f.min_ifl for f in files), default=0),
                    at_risk_files=sum(1 for f in files if f.at_risk))
    scrub = ScrubMetric(checked_last_pass=sum(m.scrub.scanned for m in ctx.membership.members.values()),
                        corrupt_found_total=int(await db.kv_get("corrupt_found_total", "0") or 0))
    return Metrics(availability_60s=availability(ctx), readable_now_pct=round(readable, 1), overhead=overhead,
                   last_incident=last, incidents_avg_mttr_s=avg, unnecessary_repairs_avoided=avoided,
                   repair=RepairMetric(bytes_total=bytes_total, mbps_now=ctx.repair_mbps), rebalance=rebalance,
                   ifl=ifl, scrub=scrub)
