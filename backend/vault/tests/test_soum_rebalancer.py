"""Owner: Soum (S9). Rebalancer: adding n7 plans one move per chunk whose ring placement now includes n7
(≈ 1/7 of bytes, never lowering IFL), completion writes kv rebalance_last + rebalance.completed; drain moves
everything off a DRAINING node and announces node.drained once it's empty. Real schema + fake BrainContext."""
import pytest

from vault.brain import soum_rebalancer
from vault.brain.soum_gc import Placement
from vault.brain.soum_rebalancer import is_drained, plan_moves, tick
from vault.common.jaiveer_fate import ifl
from vault.common.jaiveer_placement import place
from vault.common.models import NodeState, NodeView, RebalanceLast
from vault.tests.test_soum_reconcile_gc import ctx, make_version  # noqa: F401  (fixture + helper)

N_OBJECTS = 140


def add_n7(ctx):
    ctx._nodes["n7"] = NodeView(id="n7", display_name="New PC", addr="127.0.0.1:7107", state=NodeState.ALIVE,
                                labels={"power": "D", "switch": "S4", "disk_batch": "D4", "version": "1.0"},
                                capacity=ctx.cfg.cluster.node_capacity_bytes)


async def seed_ring_placed(ctx, count: int = N_OBJECTS) -> list[str]:
    """Objects placed exactly like the gateway would with n1–n6 (fate-aware ring order)."""
    pl = Placement(ctx)
    cids = []
    for i in range(count):
        cid = await make_version(ctx, f"f{i}.png", {})
        nodes = place(pl.ring, pl.by_id, cid, 3, fate_keys=pl.fate_keys)

        async def fn(c, cid=cid, nodes=nodes):
            await c.executemany("INSERT INTO fragments VALUES (?,?,?, 'ok', ?, ?)",
                                [(cid, fi, n, "a" * 64, ctx.t) for fi, n in enumerate(nodes)])
        await ctx.db.write(fn)
        cids.append(cid)
    return cids


@pytest.mark.parametrize("fate_aware,low,high", [
    (False, 0.10, 0.19),     # pure ring order: ≈ 1/N = 0.143 (§4.13, D5)
    (True, 0.10, 3 / 7),     # fate-aware: a machine that shares nothing is preferred, so MORE than 1/N moves;
])                           # still at most one fragment per chunk (≤ n/N of chunks → ≤ 3/7 of fragments)
async def test_add_machine_moves_and_never_lowers_ifl(ctx, fate_aware, low, high):  # noqa: F811
    soum_rebalancer._rebalances.clear()
    ctx.cfg.safety.fate_aware_placement = fate_aware
    await seed_ring_placed(ctx)
    add_n7(ctx)
    rows = await soum_rebalancer._current_rows(ctx)
    pl = Placement(ctx)
    moves = plan_moves(rows, "n7", pl, {n.id for n in ctx.nodes()})
    fraction = len(moves) * 100 / (len(rows) * 100)                    # every fragment is 100 bytes
    assert low <= fraction <= high, fraction
    assert len({m[0] for m in moves}) == len(moves)                     # at most one move per chunk
    by_chunk: dict[str, dict[str, set[int]]] = {}
    for r in rows:
        by_chunk.setdefault(r["chunk_id"], {}).setdefault(r["node_id"], set()).add(r["frag_idx"])
    policy = ctx.cfg.policy("rep3")
    for cid, fi, src, _ in moves:
        assert src != "n7" and "n7" in pl.desired(cid, 3) and src not in pl.desired(cid, 3)
        before = ifl(by_chunk[cid], policy.needed, pl.domains)[0]
        after_h = {n: set(s) for n, s in by_chunk[cid].items()}
        after_h[src].discard(fi)
        after_h = {n: s for n, s in after_h.items() if s}
        after_h.setdefault("n7", set()).add(fi)
        assert ifl(after_h, policy.needed, pl.domains)[0] >= before


async def test_rebalance_events_and_metrics_kv(ctx):  # noqa: F811
    soum_rebalancer._rebalances.clear()
    await seed_ring_placed(ctx, 40)
    known = {n.id for n in ctx.nodes()}
    add_n7(ctx)
    await tick(ctx, known)                                             # notices n7, plans moves
    started = [e for e in ctx.events if e[0] == "rebalance.started"]
    assert len(started) == 1 and started[0][1] == {"node": "n7"}
    moves = soum_rebalancer._rebalances["n7"]["moves"]
    assert len(ctx.jobs) == len(moves) > 0
    assert all(j["kind"] == "move" and j["reason"] == "rebalance" and j["priority"] == 3 for j in ctx.jobs)

    await tick(ctx, known)
    assert [e for e in ctx.events if e[0] == "rebalance.completed"] == []    # jobs still queued

    # the executor did every move: rows now on n7, sources gone, jobs done
    async def done(c):
        for cid, fi, src, _ in moves:
            await c.execute("UPDATE fragments SET node_id='n7' WHERE chunk_id=? AND frag_idx=? AND node_id=?",
                            (cid, fi, src))
        await c.execute("UPDATE jobs SET state='done' WHERE kind='move'")
    await ctx.db.write(done)
    await tick(ctx, known)
    completed = [e for e in ctx.events if e[0] == "rebalance.completed"]
    assert len(completed) == 1
    last = RebalanceLast.model_validate_json(await ctx.db.kv_get("rebalance_last"))
    assert last.moved_fraction == pytest.approx(len(moves) / 120, abs=1e-3) and last.ideal_fraction == pytest.approx(1 / 7, abs=1e-3)
    await tick(ctx, known)
    assert len([e for e in ctx.events if e[0] == "rebalance.completed"]) == 1      # once


async def test_drain_moves_everything_then_announces(ctx):  # noqa: F811
    soum_rebalancer._drains.clear()
    a = await make_version(ctx, "a.png", {0: ["n1"], 1: ["n3"], 2: ["n5"]})
    b = await make_version(ctx, "b.png", {0: ["n5"], 1: ["n2"], 2: ["n4"]})
    await make_version(ctx, "c.png", {0: ["n1"], 1: ["n2"], 2: ["n3"]})
    ctx.set_state("n5", NodeState.DRAINING)
    known = {n.id for n in ctx.nodes()}
    await tick(ctx, known)
    assert sorted((j["chunk_id"], j["frag_idx"], j["source_node"], j["reason"]) for j in ctx.jobs) == sorted(
        [(a, 2, "n5", "drain"), (b, 0, "n5", "drain")])
    assert all(j["priority"] == 3 for j in ctx.jobs)
    assert not await is_drained(ctx, "n5")

    async def moved(c):
        await c.execute("UPDATE fragments SET node_id='n6' WHERE node_id='n5'")
        await c.execute("UPDATE jobs SET state='done' WHERE kind='move'")
    await ctx.db.write(moved)
    await tick(ctx, known)
    assert await is_drained(ctx, "n5")
    assert [e[0] for e in ctx.events].count("node.drained") == 1
    await tick(ctx, known)
    assert [e[0] for e in ctx.events].count("node.drained") == 1
