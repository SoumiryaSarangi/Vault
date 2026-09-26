"""reconcile_inventory(ctx, inv: Inventory) -> InventoryResult: missing/corrupt/lost→ok/adopt/orphan table in §4.10. Task S8.
Owner: Soum. Uses only vault.common.brain_api.BrainContext (never import jaiveer_* privates except jaiveer_fate/jaiveer_placement).

Called by Jaiveer's POST /v1/nodes/{id}/inventory (every 30 s per node, and right after (re-)register).
Compares the node's list of .blk files with its fragment rows (ARCHITECTURE §4.10):

  row ok, file absent                          → row 'missing'  (the scheduler queues the repair)
  row ok, file present, sha ≠ row sha          → row 'corrupt'  (repaired elsewhere; the stale file then
                                                  has no row → orphan below → trimmed)
  row lost (node was dead), file present+sha ok→ row 'ok' (the copy is back; extra copies → trimmed now)
  no row, version committed & current, sha ok  → ADOPT as 'ok' (e.g. a write the gateway gave up on)
  no row, version aborted/superseded/unknown,
      or current but sha doesn't match         → ORPHAN: trimmed once the file is older than gc.orphan_grace_s
  version pending (upload in flight)           → left alone

Inventory sha256 is the fragment header's expected hash (the node doesn't re-hash its disk every 30 s);
damaged bytes are caught by read/scrub, which quarantine the file, so it then shows up here as missing.
Rows updated in the last RACE_S seconds are not marked missing: a fragment written or repaired just now may
have landed after the node built its list.

A REJOINING node (it was DEAD) also gets `node.rejoined` with how many copies were kept and trimmed.
"""
import logging
from typing import Any

from vault.common import ids
from vault.common.models import FragState, Inventory, InventoryResult, JobReason, NodeState, Policy
from vault.brain.soum_gc import _batches, _in, mark_and_trim, over_replicated

log = logging.getLogger("brain.reconciler")

RACE_S = 10.0
MAX_EVENTS = 3          # per inventory, per kind: a big disk failure shouldn't flood the timeline


async def reconcile_inventory(ctx, inv: Inventory) -> InventoryResult:
    node_id = inv.node_id
    now = ctx.now()
    files = {f.fid: f for f in inv.fragments}
    view = ctx.node(node_id)
    rejoining = view is not None and view.state == NodeState.REJOINING

    rows = await ctx.db.fetchall(
        "SELECT f.chunk_id, f.frag_idx, f.state, f.sha256, f.updated_at, v.bucket, v.key FROM fragments f "
        "JOIN chunks c ON c.chunk_id = f.chunk_id JOIN versions v ON v.version_id = c.version_id "
        "WHERE f.node_id = ?", (node_id,))
    by_fid = {ids.fid(r["chunk_id"], r["frag_idx"]): r for r in rows}

    missing, corrupt, restored = [], [], []
    for fid, r in by_fid.items():
        f = files.get(fid)
        if r["state"] == FragState.ok.value:
            if f is None:
                if r["updated_at"] < now - RACE_S:
                    missing.append(r)
            elif r["sha256"] and f.sha256 != r["sha256"]:
                corrupt.append(r)
        elif r["state"] == FragState.lost.value and f is not None and r["sha256"] and f.sha256 == r["sha256"]:
            restored.append(r)

    # files with no row on this node: adopt or orphan (a name that isn't a fid is ignored)
    unknown = []
    for fid in files:
        if fid not in by_fid:
            try:
                ids.parse_fid(fid)
                unknown.append(fid)
            except ValueError:
                log.warning("inventory %s: ignoring unparseable fid %r", node_id, fid)
    chunk_info: dict[str, dict[str, Any]] = {}
    frag_sha: dict[tuple[str, int], str] = {}
    chunk_ids = sorted({ids.parse_fid(fid)[1] for fid in unknown})
    for batch in _batches(chunk_ids):
        for r in await ctx.db.fetchall(
                "SELECT c.chunk_id, c.sha256, v.state, v.kind, v.policy, "
                "(o.current_version_id = v.version_id) AS current FROM chunks c "
                "JOIN versions v ON v.version_id = c.version_id "
                f"LEFT JOIN objects o ON o.bucket = v.bucket AND o.key = v.key WHERE c.chunk_id IN ({_in(batch)})",
                batch):
            chunk_info[r["chunk_id"]] = r
        for r in await ctx.db.fetchall(
                f"SELECT chunk_id, frag_idx, sha256 FROM fragments WHERE chunk_id IN ({_in(batch)}) AND sha256 IS NOT NULL",
                batch):
            frag_sha.setdefault((r["chunk_id"], r["frag_idx"]), r["sha256"])

    adopt, orphans, young_orphans = [], [], 0
    for fid in unknown:
        f = files[fid]
        _, cid, _, fi = ids.parse_fid(fid)
        info = chunk_info.get(cid)
        if info is not None and info["state"] == "pending":
            continue                                               # upload in flight
        if info is not None and info["state"] == "committed" and info["kind"] == "data" and info["current"]:
            policy = Policy.model_validate_json(info["policy"])
            expected = info["sha256"] if policy.type == "replication" else frag_sha.get((cid, fi))
            if 0 <= fi < policy.n and expected and f.sha256 == expected:
                adopt.append((cid, fi, expected))
                continue
        if f.mtime < now - ctx.cfg.gc.orphan_grace_s:
            orphans.append((cid, fi, node_id, f.size))
        else:
            young_orphans += 1

    async def fn(c):
        ts = ctx.now()
        await c.executemany("UPDATE fragments SET state='missing', updated_at=? WHERE chunk_id=? AND frag_idx=? "
                            "AND node_id=? AND state='ok'", [(ts, r["chunk_id"], r["frag_idx"], node_id) for r in missing])
        await c.executemany("UPDATE fragments SET state='corrupt', updated_at=? WHERE chunk_id=? AND frag_idx=? "
                            "AND node_id=? AND state='ok'", [(ts, r["chunk_id"], r["frag_idx"], node_id) for r in corrupt])
        await c.executemany("UPDATE fragments SET state='ok', updated_at=? WHERE chunk_id=? AND frag_idx=? "
                            "AND node_id=? AND state='lost'", [(ts, r["chunk_id"], r["frag_idx"], node_id) for r in restored])
        await c.executemany("INSERT OR IGNORE INTO fragments (chunk_id, frag_idx, node_id, state, sha256, updated_at) "
                            "VALUES (?,?,?, 'ok', ?, ?)", [(cid, fi, node_id, sha, ts) for cid, fi, sha in adopt])
    if missing or corrupt or restored or adopt:
        await ctx.db.write(fn)

    # after the write (never emit/enqueue inside it)
    await mark_and_trim(ctx, orphans, JobReason.orphan)
    touched = sorted({r["chunk_id"] for r in restored} | {cid for cid, _, _ in adopt})
    extra = await over_replicated(ctx, touched) if touched else []
    trimmed = await mark_and_trim(ctx, extra, JobReason.over_replicated)

    for r in missing[:MAX_EVENTS]:
        await ctx.emit("fragment.missing", {"node": node_id, "bucket": r["bucket"], "key": r["key"]},
                       {"fid": ids.fid(r["chunk_id"], r["frag_idx"])}, file=r["key"],
                       fid=ids.short_fid(ids.fid(r["chunk_id"], r["frag_idx"])))
    for r in corrupt[:MAX_EVENTS]:
        await ctx.emit("fragment.corrupt", {"node": node_id, "bucket": r["bucket"], "key": r["key"]},
                       {"fid": ids.fid(r["chunk_id"], r["frag_idx"]), "ctx": "inventory"}, file=r["key"],
                       fid=ids.short_fid(ids.fid(r["chunk_id"], r["frag_idx"])), ctx="inventory")
    if rejoining:
        await ctx.emit("node.rejoined", {"node": node_id}, {"kept": len(restored) + len(adopt), "trim": trimmed},
                       n=trimmed, e=inv.epoch, kept=len(restored) + len(adopt), trim=trimmed)

    result = InventoryResult(missing=len(missing), adopted=len(adopt), orphans=len(orphans) + young_orphans,
                             corrupt=len(corrupt))
    if any(result.model_dump().values()) or restored:
        log.info("inventory %s: %s restored=%d trimmed=%d", node_id, result.model_dump(), len(restored), trimmed)
    return result
