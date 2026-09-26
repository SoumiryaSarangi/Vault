"""Owner: Soum (S8). Reconciler (§4.10 table: missing, corrupt, lost→ok, adopt, orphan, pending untouched,
rejoin event with kept/trimmed) and GC (expire uploads, trim aborted + superseded after grace, over-replication
keeps the max-IFL copy, busy chunks skipped, trims re-queued, gc.cleaned once trims finish).
Runs against the real SQLite schema through Jaiveer's Database (test-only use) and a fake BrainContext."""
import time

import pytest

from vault.brain import soum_gc
from vault.brain.soum_gc import choose_trims, gc_pass, Placement
from vault.brain.soum_reconciler import reconcile_inventory
from vault.common import ids
from vault.common.config import load_config
from vault.common.models import Inventory, InventoryItem, JobKind, NodeState, NodeView
from vault.metadata.jaiveer_db import Database

SHA = "a" * 64


class FakeCtx:
    def __init__(self, cfg, db, t: float):
        self.cfg, self.db, self.rpc, self.t = cfg, db, None, t
        self._nodes = {nid: NodeView(id=nid, display_name=n.display_name, addr=cfg.addr(nid), state=NodeState.ALIVE,
                                     labels=dict(n.labels), capacity=cfg.cluster.node_capacity_bytes)
                       for nid, n in cfg.nodes.items()}
        self.jobs: list[dict] = []
        self.events: list[tuple] = []

    def now(self):
        return self.t

    def nodes(self):
        return list(self._nodes.values())

    def node(self, node_id):
        return self._nodes.get(node_id)

    def safety(self):
        return self.cfg.safety

    def set_state(self, node_id, state):
        self._nodes[node_id] = self._nodes[node_id].model_copy(update={"state": state})

    async def enqueue_job(self, kind, reason, *, chunk_id, frag_idx, priority, source_node=None, target_node=None,
                          incident_id=None):
        for j in self.jobs:
            if (j["kind"], j["chunk_id"], j["frag_idx"]) == (kind, chunk_id, frag_idx):
                return 0
        self.jobs.append({"kind": kind, "reason": reason, "chunk_id": chunk_id, "frag_idx": frag_idx,
                          "priority": priority, "source_node": source_node})
        return len(self.jobs)

    async def emit(self, type_, subject, data=None, **fields):
        self.events.append((type_, subject, data or {}, fields))


@pytest.fixture
async def ctx(tmp_path):
    cfg = load_config("vault.yaml")
    db = Database(tmp_path / "vault.db")
    await db.open()
    soum_gc._pending_trims.clear()
    yield FakeCtx(cfg, db, time.time())
    await db.close()


async def make_version(ctx, key: str, holders: dict[int, list[str]], *, state="committed", current=True,
                       policy="rep3", frag_state="ok", updated_ago=60.0, superseded_ago=None, created_ago=0.0,
                       sha=SHA) -> str:
    """One version with chunk 0; holders: frag_idx → nodes. Returns the chunk_id."""
    vid = ids.new_version_id()
    cid = ids.chunk_id(vid, 0)
    pol = ctx.cfg.policy(policy).model_dump_json()
    t = ctx.t

    async def fn(c):
        await c.execute("INSERT INTO versions (version_id, bucket, key, kind, state, seq, commit_seq, size, sha256, "
                        "policy, chunk_size, created_at, committed_at, superseded_at) VALUES "
                        "(?, 'clinic', ?, 'data', ?, 1, 1, 100, ?, ?, 1048576, ?, ?, ?)",
                        (vid, key, state, sha, pol, t - created_ago, t,
                         None if superseded_ago is None else t - superseded_ago))
        await c.execute("INSERT INTO chunks (chunk_id, version_id, idx, size, sha256, frag_size) VALUES (?,?,0,100,?,100)",
                        (cid, vid, sha))
        for fi, nodes in holders.items():
            for nid in nodes:
                await c.execute("INSERT INTO fragments (chunk_id, frag_idx, node_id, state, sha256, updated_at) "
                                "VALUES (?,?,?,?,?,?)", (cid, fi, nid, frag_state, sha, t - updated_ago))
        if current:
            await c.execute("INSERT INTO objects (bucket, key, current_version_id, last_seq) VALUES ('clinic', ?, ?, 1) "
                            "ON CONFLICT(bucket, key) DO UPDATE SET current_version_id=excluded.current_version_id",
                            (key, vid))
    await ctx.db.write(fn)
    return cid


async def frag_rows(ctx, cid):
    return {(r["frag_idx"], r["node_id"]): r["state"] for r in
            await ctx.db.fetchall("SELECT * FROM fragments WHERE chunk_id=?", (cid,))}


def inv(node, items: list[tuple[str, str]], mtime_ago=0.0, t=None):
    now = t or time.time()
    return Inventory(node_id=node, epoch=2, fragments=[InventoryItem(fid=f, sha256=s, size=100, mtime=now - mtime_ago)
                                                        for f, s in items])


# ── reconciler ──

async def test_missing_only_when_row_is_old(ctx):
    old = await make_version(ctx, "old.png", {0: ["n1"], 1: ["n2"], 2: ["n3"]}, updated_ago=60)
    new = await make_version(ctx, "new.png", {0: ["n1"], 1: ["n2"], 2: ["n3"]}, updated_ago=1)
    res = await reconcile_inventory(ctx, inv("n1", [], t=ctx.t))
    assert res.missing == 1
    assert (await frag_rows(ctx, old))[(0, "n1")] == "missing"
    assert (await frag_rows(ctx, new))[(0, "n1")] == "ok"
    assert [e[0] for e in ctx.events] == ["fragment.missing"] and ctx.events[0][3]["file"] == "old.png"


async def test_corrupt_when_sha_differs(ctx):
    cid = await make_version(ctx, "x.png", {0: ["n1"], 1: ["n2"], 2: ["n3"]})
    res = await reconcile_inventory(ctx, inv("n2", [(ids.fid(cid, 1), "b" * 64)], t=ctx.t))
    assert res.corrupt == 1 and (await frag_rows(ctx, cid))[(1, "n2")] == "corrupt"


async def test_rejoin_restores_lost_and_trims_extra_copy_keeping_max_ifl(ctx):
    # {n1,n3,n5} is the fully independent triple (IFL 3). n5 died, repair put frag 2 on n2 (IFL 2), n5 is back.
    cid = await make_version(ctx, "scan.png", {0: ["n1"], 1: ["n3"], 2: ["n2"]})

    async def fn(c):
        await c.execute("INSERT INTO fragments VALUES (?, 2, 'n5', 'lost', ?, ?)", (cid, SHA, ctx.t - 30))
    await ctx.db.write(fn)
    ctx.set_state("n5", NodeState.REJOINING)
    res = await reconcile_inventory(ctx, inv("n5", [(ids.fid(cid, 2), SHA)], t=ctx.t))
    rows = await frag_rows(ctx, cid)
    assert rows[(2, "n5")] == "ok" and rows[(2, "n2")] == "trim"
    assert ctx.jobs == [{"kind": JobKind.trim, "reason": "over_replicated", "chunk_id": cid, "frag_idx": 2,
                         "priority": 4, "source_node": "n2"}]
    ev = [e for e in ctx.events if e[0] == "node.rejoined"][0]
    assert ev[2] == {"kept": 1, "trim": 1} and res.missing == 0


async def test_adopt_current_version_file_without_row(ctx):
    cid = await make_version(ctx, "late.png", {0: ["n1"], 1: ["n2"]})      # the gateway gave up on frag 2
    res = await reconcile_inventory(ctx, inv("n6", [(ids.fid(cid, 2), SHA)], t=ctx.t))
    assert res.adopted == 1 and (await frag_rows(ctx, cid))[(2, "n6")] == "ok"


async def test_orphans_trimmed_after_grace_pending_left_alone(ctx):
    grace = ctx.cfg.gc.orphan_grace_s
    aborted = await make_version(ctx, "a.png", {}, state="aborted", current=False)
    pending = await make_version(ctx, "p.png", {}, state="pending", current=False)
    stale_current = await make_version(ctx, "s.png", {0: ["n1"]})             # file on n4 has the wrong sha
    unknown = ids.chunk_id(ids.new_version_id(), 0)                            # wiped metadata / junk
    items = [(ids.fid(aborted, 0), SHA), (ids.fid(pending, 0), SHA), (ids.fid(stale_current, 1), "c" * 64),
             (ids.fid(unknown, 0), SHA)]
    res = await reconcile_inventory(ctx, inv("n4", items, mtime_ago=grace + 5, t=ctx.t))
    assert res.orphans == 3 and res.adopted == 0
    assert sorted((j["chunk_id"], j["reason"]) for j in ctx.jobs) == sorted(
        [(aborted, "orphan"), (stale_current, "orphan"), (unknown, "orphan")])
    ctx.jobs.clear()
    res = await reconcile_inventory(ctx, inv("n4", items, mtime_ago=1, t=ctx.t))    # too young: not yet
    assert res.orphans == 3 and ctx.jobs == []


# ── choose_trims (pure) ──

async def test_choose_trims_never_lowers_ifl(ctx):
    pl = Placement(ctx)
    rows = [{"node_id": "n1", "frag_idx": 0}, {"node_id": "n3", "frag_idx": 1},
            {"node_id": "n5", "frag_idx": 2}, {"node_id": "n2", "frag_idx": 2}]
    assert choose_trims("v_x_00000", rows, ctx.cfg.policy("rep3"), pl) == [(2, "n2")]


# ── GC ──

async def test_gc_pass(ctx):
    grace = ctx.cfg.gc.superseded_grace_s
    expired = await make_version(ctx, "e.png", {0: ["n1"]}, state="pending", current=False, frag_state="pending",
                                 created_ago=ctx.cfg.gc.upload_timeout_s + 5)
    old_sup = await make_version(ctx, "o.png", {0: ["n1"], 1: ["n2"]}, state="superseded", current=False,
                                 superseded_ago=grace + 5)
    new_sup = await make_version(ctx, "n.png", {0: ["n1"]}, state="superseded", current=False, superseded_ago=1)
    over = await make_version(ctx, "over.png", {0: ["n1"], 1: ["n3"], 2: ["n5", "n2"]})
    busy = await make_version(ctx, "busy.png", {0: ["n1"], 1: ["n3"], 2: ["n5", "n2"]})

    async def fn(c):
        await c.execute("INSERT INTO jobs (kind, reason, chunk_id, frag_idx, priority, state, created_at) "
                        "VALUES ('move', 'fate', ?, 2, 3, 'running', ?)", (busy, ctx.t))
    await ctx.db.write(fn)

    stats = await gc_pass(ctx)
    assert stats == {"expired": 1, "aborted": 1, "superseded": 2, "over_replicated": 1}
    assert (await ctx.db.fetchone("SELECT v.state FROM versions v JOIN chunks c ON c.version_id=v.version_id "
                                  "WHERE c.chunk_id=?", (expired,)))["state"] == "aborted"
    trims = {(j["chunk_id"], j["frag_idx"], j["source_node"], j["reason"]) for j in ctx.jobs}
    assert trims == {(expired, 0, "n1", "aborted"), (old_sup, 0, "n1", "superseded"), (old_sup, 1, "n2", "superseded"),
                     (over, 2, "n2", "over_replicated")}
    assert (await frag_rows(ctx, new_sup))[(0, "n1")] == "ok" and (await frag_rows(ctx, busy))[(2, "n2")] == "ok"

    # the executor deletes two trimmed rows → the next pass reports the leftover one (not the extra copy)
    async def done(c):
        await c.execute("DELETE FROM fragments WHERE chunk_id=? AND node_id='n2'", (over,))
        await c.execute("DELETE FROM fragments WHERE chunk_id=? AND node_id='n1'", (old_sup,))
    await ctx.db.write(done)
    ctx.jobs.clear()
    await gc_pass(ctx)
    cleaned = [e for e in ctx.events if e[0] == "gc.cleaned"]
    assert len(cleaned) == 1 and cleaned[0][2] == {"n": 1, "bytes": 100}
    # still-pending trims were re-queued (their jobs were "lost" when we cleared the list)
    assert {(j["chunk_id"], j["source_node"]) for j in ctx.jobs} == {(expired, "n1"), (old_sup, "n2")}
