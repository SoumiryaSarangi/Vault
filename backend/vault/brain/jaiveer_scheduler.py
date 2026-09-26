"""Repair scheduler: scan every repair.scan_ms (500 ms) over current committed data versions (§4.8).
Task J7. Owner: Jaiveer. Entry point for loops: async def run(ctx) -> None.

  chunk with distinct durable frag_idx < n  → one repair job per missing frag_idx
  fragment rows corrupt / missing            → repair for that frag_idx (reason corrupt / missing)
Priority: P0 when durable ≤ needed (one failure from loss), else P1. P2–P4 come from the auditor / GC.
Chunks that already lost more than they can afford (durable < needed) are not queued: nothing can rebuild them.
Jobs are deduplicated on kind+chunk_id+frag_idx while queued/running (enqueue_job_tx), so re-scanning is cheap.
The scan also keeps open incidents' remaining_chunks current and closes them (repair.completed).
Enqueuing happens even with safety.repair off (Naive); the dispatcher just doesn't run them.
"""
import asyncio
import logging

from vault.common.models import JobKind, JobReason, NodeState, Policy
from vault.brain.jaiveer_incidents import update_open_incidents

log = logging.getLogger("scheduler")

NOT_DURABLE = (NodeState.DEAD, NodeState.RETIRED)


async def scan(ctx) -> dict:
    """One pass. → {"below_target": set(chunk_id), "queued": int, "unrecoverable": int}."""
    rows = await ctx.db.fetchall(
        "SELECT ch.chunk_id, v.policy, f.frag_idx, f.node_id, f.state FROM chunks ch "
        "JOIN objects o ON o.current_version_id = ch.version_id "
        "JOIN versions v ON v.version_id = ch.version_id AND v.kind = 'data' AND v.state = 'committed' "
        "LEFT JOIN fragments f ON f.chunk_id = ch.chunk_id")
    states = {n.id: n.state for n in ctx.nodes()}
    chunks: dict[str, dict] = {}
    for r in rows:
        ch = chunks.setdefault(r["chunk_id"], {"policy": r["policy"], "durable": set(), "bad": {}})
        if r["node_id"] is None:
            continue
        st = states.get(r["node_id"])
        if r["state"] == "ok" and st is not None and st not in NOT_DURABLE:
            ch["durable"].add(r["frag_idx"])
        elif r["state"] in ("corrupt", "missing"):
            ch["bad"][r["frag_idx"]] = r["state"]

    todo, below, unrecoverable = [], set(), 0
    for cid, ch in chunks.items():
        policy = Policy.model_validate_json(ch["policy"])
        durable = ch["durable"]
        if len(durable) >= policy.n:
            continue
        below.add(cid)
        if len(durable) < policy.needed:
            unrecoverable += 1
            continue
        prio = 0 if len(durable) <= policy.needed else 1
        for fi in range(policy.n):
            if fi not in durable:
                reason = {"corrupt": JobReason.corrupt, "missing": JobReason.missing}.get(ch["bad"].get(fi),
                                                                                         JobReason.under_replicated)
                todo.append((cid, fi, prio, reason))

    if todo:                                   # skip what's already queued/running: no write txn (fsync) per scan
        active = {(r["chunk_id"], r["frag_idx"]) for r in await ctx.db.fetchall(
            "SELECT chunk_id, frag_idx FROM jobs WHERE kind='repair' AND state IN ('queued','running')")}
        todo = [t for t in todo if (t[0], t[1]) not in active]
    if todo:
        from vault.metadata.jaiveer_app import enqueue_job_tx

        async def fn(c):
            for cid, fi, prio, reason in todo:
                await enqueue_job_tx(c, JobKind.repair, reason, chunk_id=cid, frag_idx=fi, priority=prio)
        await ctx.db.write(fn)
    await update_open_incidents(ctx, below)
    return {"below_target": below, "queued": len(todo), "unrecoverable": unrecoverable}


async def run(ctx) -> None:
    period = ctx.cfg.repair.scan_ms / 1000
    while True:
        try:
            await scan(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scan failed")
        await asyncio.sleep(period)
