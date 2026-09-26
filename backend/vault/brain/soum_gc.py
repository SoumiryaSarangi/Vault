"""run(ctx): every 5 s abort expired uploads, trim aborted + superseded fragments, trim over-replication keeping max IFL. §4.10. Task S8.
Owner: Soum. Uses only vault.common.brain_api.BrainContext (never import jaiveer_* privates except jaiveer_fate/jaiveer_placement).

One pass (gc.interval_s):
  1. pending versions older than gc.upload_timeout_s → aborted
  2. fragment rows of aborted versions, and of superseded versions older than gc.superseded_grace_s → trim (P4)
  3. over-replication of current data: a frag_idx with ok copies on more than one durable node keeps the copy
     that maximizes the chunk's effective copies (IFL; tie: earlier in the ring's desired placement, then id)
     and trims the rest (P4). Chunks with a queued/running repair or move are skipped (a move is briefly
     over-replicated on purpose: make-before-break).
  4. rows already in state 'trim' get their trim job (re)queued (jobs dedupe on kind+chunk+frag_idx, so a
     trim whose job was lost, or that shared a frag_idx with another trim, is picked up again)
  5. gc.cleaned (one aggregated event) for leftover trims (aborted, superseded, orphan) that FINISHED since
     the last pass (their rows are gone)

Trim jobs follow Jaiveer's executor (jaiveer_repair): kind=trim, source_node = the node to delete from,
chunk_id + frag_idx set; the row is put in state 'trim' first (not durable any more, so it never counts).
Never call ctx.enqueue_job / ctx.emit inside ctx.db.write (the writer lock isn't reentrant).
"""
import asyncio
import json
import logging
from typing import Any, Iterable, Optional

from vault.common.events import fmt_bytes
from vault.common.jaiveer_fate import build_domains, ifl
from vault.common.jaiveer_placement import Ring, active_fate_keys, place
from vault.common.models import JobKind, JobReason, NodeState, Policy

log = logging.getLogger("brain.gc")

NOT_DURABLE = {NodeState.DEAD.value, NodeState.RETIRED.value}
TRIM_PRIORITY = 4

# (chunk_id, frag_idx, node_id) → bytes, for leftover rows we put in 'trim' (aborted, superseded, orphan);
# reported in gc.cleaned once they're gone. Over-replication trims aren't "leftovers": node.rejoined (or the
# move that caused them) already tells that story, so they're not counted here.
_pending_trims: dict[tuple[str, int, str], int] = {}
LEFTOVER = {JobReason.aborted, JobReason.superseded, JobReason.orphan}


def _state(ctx, node_id: str) -> Optional[str]:
    v = ctx.node(node_id)
    return (v.state.value if hasattr(v.state, "value") else str(v.state)) if v else None


def _in(ids: list[str]) -> str:
    return ",".join("?" * len(ids))


def _batches(items: list, size: int = 400) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def mark_and_trim(ctx, trims: list[tuple[str, int, str, int]], reason: JobReason) -> int:
    """trims: (chunk_id, frag_idx, node_id, bytes). Row → 'trim', then a P4 trim job each. Returns count."""
    if not trims:
        return 0

    async def fn(c):
        await c.executemany("UPDATE fragments SET state='trim', updated_at=? WHERE chunk_id=? AND frag_idx=? "
                            "AND node_id=? AND state != 'trim'",
                            [(ctx.now(), cid, fi, nid) for cid, fi, nid, _ in trims])
    await ctx.db.write(fn)
    for cid, fi, nid, size in trims:
        if reason in LEFTOVER:
            _pending_trims[(cid, fi, nid)] = size
        await ctx.enqueue_job(JobKind.trim, reason, chunk_id=cid, frag_idx=fi, priority=TRIM_PRIORITY,
                              source_node=nid)
    return len(trims)


# ── over-replication ──

class Placement:
    """Ring + fate domains for one pass (both depend only on membership and labels)."""

    def __init__(self, ctx):
        nodes = ctx.nodes()
        self.by_id = {n.id: n for n in nodes}
        self.ring = Ring(ctx.cfg.placement.vnodes_per_node)
        self.ring.rebuild(nodes)
        self.fate_keys = active_fate_keys(nodes, ctx.cfg.fate.keys) if ctx.safety().fate_aware_placement else []
        self.domains = build_domains(nodes, ctx.cfg.fate.keys)[0]

    def desired(self, chunk_id: str, n: int) -> list[str]:
        return place(self.ring, self.by_id, chunk_id, n, fate_keys=self.fate_keys)


def choose_trims(chunk_id: str, rows: list[dict[str, Any]], policy: Policy, pl: Placement) -> list[tuple[int, str]]:
    """rows: ok fragment rows of one chunk on durable nodes. → [(frag_idx, node_id)] to trim.
    Each over-replicated frag_idx keeps the copy that maximizes IFL of the whole chunk (never lowers it)."""
    holders: dict[str, set[int]] = {}
    for r in rows:
        holders.setdefault(r["node_id"], set()).add(r["frag_idx"])
    by_idx: dict[int, list[str]] = {}
    for r in rows:
        by_idx.setdefault(r["frag_idx"], []).append(r["node_id"])
    desired = pl.desired(chunk_id, policy.n)
    rank = {nid: i for i, nid in enumerate(desired)}
    trims: list[tuple[int, str]] = []
    for fi in sorted(by_idx):
        cands = sorted(set(by_idx[fi]))
        if len(cands) < 2:
            continue

        def score(keep: str) -> tuple:
            h = {nid: set(s) for nid, s in holders.items()}
            for other in cands:
                if other != keep:
                    h[other].discard(fi)
                    if not h[other]:
                        del h[other]
            level = ifl(h, policy.needed, pl.domains)[0]
            return (-level, rank.get(keep, len(rank)), keep)
        keep = min(cands, key=score)
        for other in cands:
            if other != keep:
                trims.append((fi, other))
                holders[other].discard(fi)
                if not holders[other]:
                    del holders[other]
    return trims


async def over_replicated(ctx, chunk_ids: Optional[list[str]] = None) -> list[tuple[str, int, str, int]]:
    """Find over-replicated frag_idx on current committed data (optionally only these chunks).
    → trims (chunk_id, frag_idx, node_id, bytes), not yet applied."""
    base = ("SELECT f.chunk_id, f.frag_idx, f.node_id, c.frag_size, v.policy FROM fragments f "
            "JOIN chunks c ON c.chunk_id = f.chunk_id JOIN versions v ON v.version_id = c.version_id "
            "JOIN objects o ON o.current_version_id = v.version_id "
            "WHERE f.state = 'ok' AND v.state = 'committed' AND v.kind = 'data'")
    if chunk_ids is None:
        dup = await ctx.db.fetchall(
            "SELECT DISTINCT f.chunk_id FROM fragments f WHERE f.state = 'ok' "
            "GROUP BY f.chunk_id, f.frag_idx HAVING COUNT(DISTINCT f.node_id) > 1")
        chunk_ids = [r["chunk_id"] for r in dup]
    if not chunk_ids:
        return []
    busy: set[str] = set()
    rows: list[dict[str, Any]] = []
    for batch in _batches(list(chunk_ids)):
        busy |= {r["chunk_id"] for r in await ctx.db.fetchall(
            f"SELECT DISTINCT chunk_id FROM jobs WHERE kind IN ('repair','move') AND state IN ('queued','running') "
            f"AND chunk_id IN ({_in(batch)})", batch)}
        rows += await ctx.db.fetchall(f"{base} AND f.chunk_id IN ({_in(batch)})", batch)
    by_chunk: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["chunk_id"] in busy or _state(ctx, r["node_id"]) in NOT_DURABLE or _state(ctx, r["node_id"]) is None:
            continue
        by_chunk.setdefault(r["chunk_id"], []).append(r)
    if not by_chunk:
        return []
    pl = Placement(ctx)
    out = []
    for cid, crow in by_chunk.items():
        policy = Policy.model_validate_json(crow[0]["policy"])
        size = crow[0]["frag_size"] or 0
        out += [(cid, fi, nid, size) for fi, nid in choose_trims(cid, crow, policy, pl)]
    return out


# ── the pass ──

async def expire_uploads(ctx) -> int:
    cutoff = ctx.now() - ctx.cfg.gc.upload_timeout_s

    async def fn(c):
        cur = await c.execute("UPDATE versions SET state='aborted' WHERE state='pending' AND created_at < ?", (cutoff,))
        return cur.rowcount
    return await ctx.db.write(fn)


async def dead_version_trims(ctx) -> tuple[list[tuple[str, int, str, int]], list[tuple[str, int, str, int]]]:
    """Rows of aborted versions, and of superseded versions past the grace → (aborted, superseded) trims."""
    grace_cut = ctx.now() - ctx.cfg.gc.superseded_grace_s
    rows = await ctx.db.fetchall(
        "SELECT f.chunk_id, f.frag_idx, f.node_id, c.frag_size, v.state AS vstate FROM fragments f "
        "JOIN chunks c ON c.chunk_id = f.chunk_id JOIN versions v ON v.version_id = c.version_id "
        "WHERE f.state != 'trim' AND (v.state = 'aborted' OR (v.state = 'superseded' AND v.superseded_at < ?))",
        (grace_cut,))
    aborted = [(r["chunk_id"], r["frag_idx"], r["node_id"], r["frag_size"] or 0) for r in rows if r["vstate"] == "aborted"]
    superseded = [(r["chunk_id"], r["frag_idx"], r["node_id"], r["frag_size"] or 0) for r in rows
                  if r["vstate"] == "superseded"]
    return aborted, superseded


async def requeue_trims(ctx) -> None:
    for r in await ctx.db.fetchall("SELECT chunk_id, frag_idx, node_id FROM fragments WHERE state = 'trim'"):
        await ctx.enqueue_job(JobKind.trim, JobReason.over_replicated, chunk_id=r["chunk_id"], frag_idx=r["frag_idx"],
                              priority=TRIM_PRIORITY, source_node=r["node_id"])


async def report_cleaned(ctx) -> None:
    if not _pending_trims:
        return
    keys = list(_pending_trims)
    still = set()
    for batch in _batches(keys, 300):
        where = " OR ".join("(chunk_id=? AND frag_idx=? AND node_id=?)" for _ in batch)
        params = [x for k in batch for x in k]
        still |= {(r["chunk_id"], r["frag_idx"], r["node_id"])
                  for r in await ctx.db.fetchall(f"SELECT chunk_id, frag_idx, node_id FROM fragments WHERE {where}",
                                                 params)}
    done = [k for k in keys if k not in still]
    if not done:
        return
    size = sum(_pending_trims.pop(k) for k in done)
    await ctx.emit("gc.cleaned", {}, {"n": len(done), "bytes": size}, size=fmt_bytes(size))


async def gc_pass(ctx) -> dict[str, int]:
    await report_cleaned(ctx)
    expired = await expire_uploads(ctx)
    aborted, superseded = await dead_version_trims(ctx)
    n_ab = await mark_and_trim(ctx, aborted, JobReason.aborted)
    n_sup = await mark_and_trim(ctx, superseded, JobReason.superseded)
    n_over = await mark_and_trim(ctx, await over_replicated(ctx), JobReason.over_replicated)
    await requeue_trims(ctx)
    stats = {"expired": expired, "aborted": n_ab, "superseded": n_sup, "over_replicated": n_over}
    if any(stats.values()):
        log.info("gc pass: %s", json.dumps(stats))
    return stats


async def run(ctx) -> None:
    while True:
        try:
            await gc_pass(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("gc pass failed")
        await asyncio.sleep(ctx.cfg.gc.interval_s)
