"""on_node_added(ctx, node_id), on_drain(ctx, node_id), run(ctx). Move primitive via ctx.enqueue_job(kind=move). §4.13. Task S9.
Owner: Soum. Uses only vault.common.brain_api.BrainContext (never import jaiveer_* privates except jaiveer_fate/jaiveer_placement).

Node added (§4.13): for every current chunk whose ring placement (place(chunk, n)) now includes the new node
and that has no fragment there, move ONE fragment from a holder that is not in the desired set to the new
node, if that doesn't lower the chunk's effective copies (IFL). ≈ 1/N of the bytes move. rebalance.started
when planned; rebalance.completed (moved/total vs ideal 1/N) when every move job has finished, and the same
numbers go to kv "rebalance_last" (RebalanceLast), which Jaiveer's /v1/metrics reads.

Drain (§4.13): every fragment of current data on a DRAINING node moves off (target chosen by Jaiveer's
executor with choose_additional). node.drained once nothing is left. Setting DRAINING (the decommission
route) and RETIRED are membership changes on Jaiveer's side (handoff); is_drained() tells him when.

Moves are P3 (below repair), make-before-break (Jaiveer's executor: pull to target, verify, then delete
source). Rule for every mover: never lower IFL.

run(ctx) notices machines that appear after metadata started (e.g. "Add machine" → n7) once they are ALIVE
and calls on_node_added; it also starts drains for DRAINING nodes and reports completion.
"""
import asyncio
import logging
from typing import Any, Optional

from vault.brain.soum_gc import NOT_DURABLE, Placement, _batches, _in
from vault.common.events import fmt_bytes
from vault.common.jaiveer_fate import ifl
from vault.common.models import JobKind, JobReason, NodeState, Policy, RebalanceLast

log = logging.getLogger("brain.rebalancer")

MOVE_PRIORITY = 3
TICK_S = 2.0

_rebalances: dict[str, dict[str, Any]] = {}     # node_id → {jobs, moves, planned_bytes, total_bytes, ideal, started}
_drains: dict[str, dict[str, Any]] = {}         # node_id → {jobs, started, announced}


async def _current_rows(ctx) -> list[dict[str, Any]]:
    """ok fragment rows of current committed data, with size and policy."""
    return await ctx.db.fetchall(
        "SELECT f.chunk_id, f.frag_idx, f.node_id, c.frag_size, v.policy FROM fragments f "
        "JOIN chunks c ON c.chunk_id = f.chunk_id JOIN versions v ON v.version_id = c.version_id "
        "JOIN objects o ON o.current_version_id = v.version_id "
        "WHERE f.state = 'ok' AND v.state = 'committed' AND v.kind = 'data'")


def _live_count(ctx) -> int:
    return sum(1 for n in ctx.nodes() if n.state != NodeState.RETIRED)


async def _write_kv(ctx, k: str, v: str) -> None:
    async def fn(c):
        await c.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, v))
    await ctx.db.write(fn)


def plan_moves(rows: list[dict[str, Any]], node_id: str, pl: Placement, durable: set[str]) \
        -> list[tuple[str, int, str, int]]:
    """→ [(chunk_id, frag_idx, source_node, bytes)]: one move per chunk whose desired placement gained node_id."""
    by_chunk: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["node_id"] in durable:
            by_chunk.setdefault(r["chunk_id"], []).append(r)
    moves = []
    for cid, crow in by_chunk.items():
        if any(r["node_id"] == node_id for r in crow):
            continue
        policy = Policy.model_validate_json(crow[0]["policy"])
        desired = pl.desired(cid, policy.n)
        if node_id not in desired:
            continue
        holders: dict[str, set[int]] = {}
        for r in crow:
            holders.setdefault(r["node_id"], set()).add(r["frag_idx"])
        before = ifl(holders, policy.needed, pl.domains)[0]
        best: Optional[tuple] = None
        for src in sorted(h for h in holders if h not in desired):
            for fi in sorted(holders[src]):
                h = {n: set(s) for n, s in holders.items()}
                h[src].discard(fi)
                if not h[src]:
                    del h[src]
                h.setdefault(node_id, set()).add(fi)
                after = ifl(h, policy.needed, pl.domains)[0]
                if after < before:
                    continue                                    # never lower IFL
                disk = pl.by_id[src].disk_used if src in pl.by_id else 0
                key = (-after, -disk, src, fi)                   # best IFL, then relieve the fullest node
                if best is None or key < best[0]:
                    best = (key, src, fi)
        if best is not None:
            moves.append((cid, best[2], best[1], crow[0]["frag_size"] or 0))
    return moves


async def on_node_added(ctx, node_id: str) -> None:
    """Plan and enqueue the moves onto a new machine (idempotent: a node is rebalanced once)."""
    if node_id in _rebalances:
        return
    rows = await _current_rows(ctx)
    durable = {n.id for n in ctx.nodes() if n.state.value not in NOT_DURABLE}
    moves = plan_moves(rows, node_id, Placement(ctx), durable)
    total = sum(r["frag_size"] or 0 for r in rows if r["node_id"] in durable)
    ideal = 1 / max(1, _live_count(ctx))
    jobs = []
    for cid, fi, src, _ in moves:
        jobs.append(await ctx.enqueue_job(JobKind.move, JobReason.rebalance, chunk_id=cid, frag_idx=fi,
                                          priority=MOVE_PRIORITY, source_node=src, target_node=node_id))
    planned = sum(b for *_, b in moves)
    _rebalances[node_id] = {"jobs": jobs, "moves": moves, "planned_bytes": planned, "total_bytes": total,
                            "ideal": ideal, "started": ctx.now()}
    log.info("rebalance onto %s: %d moves, %s of %s (ideal %.1f%%)", node_id, len(moves), fmt_bytes(planned),
             fmt_bytes(total), ideal * 100)
    await ctx.emit("rebalance.started", {"node": node_id},
                   {"moves": len(moves), "planned_bytes": planned, "total_bytes": total, "ideal": ideal},
                   pct=round(100 * planned / total) if total else 0, moves=len(moves), ideal=ideal)
    if not moves:
        await _finish_rebalance(ctx, node_id)


async def _jobs_active(ctx, job_ids: list[int]) -> bool:
    ids_ = [j for j in job_ids if j]
    for batch in _batches(ids_):
        row = await ctx.db.fetchone(f"SELECT COUNT(*) AS n FROM jobs WHERE id IN ({_in(batch)}) "
                                    "AND state IN ('queued','running')", batch)
        if row and row["n"]:
            return True
    return False


async def _finish_rebalance(ctx, node_id: str) -> None:
    r = _rebalances[node_id]
    moved = 0
    for cid, fi, _, size in r["moves"]:
        if await ctx.db.fetchone("SELECT 1 FROM fragments WHERE chunk_id=? AND frag_idx=? AND node_id=? AND state='ok'",
                                 (cid, fi, node_id)):
            moved += size
    actual = moved / r["total_bytes"] if r["total_bytes"] else 0.0
    last = RebalanceLast(moved_fraction=round(actual, 4), ideal_fraction=round(r["ideal"], 4))
    await _write_kv(ctx, "rebalance_last", last.model_dump_json())
    await ctx.emit("rebalance.completed", {"node": node_id},
                   {"moved_bytes": moved, "total_bytes": r["total_bytes"], "moved_fraction": last.moved_fraction,
                    "ideal_fraction": last.ideal_fraction},
                   actual=actual, ideal=r["ideal"], bytes=fmt_bytes(moved), s=ctx.now() - r["started"])
    r["done"] = True
    log.info("rebalance onto %s done: moved %.1f%% (ideal %.1f%%)", node_id, actual * 100, r["ideal"] * 100)


# ── drain ──

async def on_drain(ctx, node_id: str) -> None:
    """Move every current fragment off a DRAINING node (targets chosen by the executor). Safe to call again:
    jobs dedupe on kind+chunk+frag_idx."""
    rows = [r for r in await _current_rows(ctx) if r["node_id"] == node_id]
    d = _drains.setdefault(node_id, {"jobs": [], "started": ctx.now(), "announced": False})
    for r in rows:
        d["jobs"].append(await ctx.enqueue_job(JobKind.move, JobReason.drain, chunk_id=r["chunk_id"],
                                               frag_idx=r["frag_idx"], priority=MOVE_PRIORITY, source_node=node_id))
    if rows:
        log.info("drain %s: %d moves queued", node_id, len(rows))


async def is_drained(ctx, node_id: str) -> bool:
    """True when no durable copy of current data is left on the node (safe to mark RETIRED)."""
    return not any(r["node_id"] == node_id for r in await _current_rows(ctx))


# ── loop ──

async def tick(ctx, known: set[str]) -> None:
    for n in ctx.nodes():
        if n.id not in known and n.state == NodeState.ALIVE:
            known.add(n.id)
            await on_node_added(ctx, n.id)
    for node_id, r in list(_rebalances.items()):
        if not r.get("done") and not await _jobs_active(ctx, r["jobs"]):
            await _finish_rebalance(ctx, node_id)
    for n in ctx.nodes():
        if n.state == NodeState.DRAINING:
            d = _drains.get(n.id)
            if d is None or not await _jobs_active(ctx, d["jobs"]):
                if await is_drained(ctx, n.id):
                    if d is not None and not d["announced"]:
                        d["announced"] = True
                        await ctx.emit("node.drained", {"node": n.id}, {"s": ctx.now() - d["started"]})
                else:
                    await on_drain(ctx, n.id)       # first time, or moves that failed/were cancelled: again


async def run(ctx) -> None:
    known = {n.id for n in ctx.nodes()}      # machines present when metadata started aren't "new"
    while True:
        try:
            await tick(ctx, known)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("rebalancer tick failed")
        await asyncio.sleep(TICK_S)
