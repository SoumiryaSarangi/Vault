"""Repair dispatcher: runs queued jobs (repair / move / trim) against the storage nodes. §4.8, §4.9 step 4.
Task J7. Owner: Jaiveer. Entry point for loops: async def run(ctx) -> None.

Limits (vault.yaml `repair`): at most max_concurrent (8) running jobs, at most per_node (2) per source or target
node, a global token bucket of bandwidth_mbps (50 MB/s: a job of b bytes waits for b tokens), max_attempts (3)
with 1 s / 3 s / 9 s backoff, then `repair.failed` (the scheduler re-queues it later: "Vault will keep trying").
safety.repair off (Naive) → nothing is dispatched.

repair (chunk, frag_idx):
  target  = job.target_node if still eligible, else choose_additional(chunk, holders = durable ∪ in-flight nodes)
            (ALIVE, unfenced, not full; fate-aware unless Naive). None → wait + `repair.blocked`.
  sources = available copies, best first (ALIVE before SUSPECT/PARTITIONED, direct before relay, not slow).
            Replication: any copy (mode "copy"). EC: every other fragment, ≥ k needed (mode "ec_rebuild").
  row (chunk, frag_idx, target) = incoming → POST target /v1/fragments/{fid}/pull → verify the returned sha
  → row ok, stale corrupt/missing rows for that frag_idx dropped, job done. Failure → row removed, retry.
move (make-before-break, auditor/rebalancer): same pull to the target, then DELETE on the source + drop its row.
trim: DELETE /v1/fragments/{fid} on the node (X-Vault-Epoch), then drop the row. A DEAD node's row is just dropped.
"""
import asyncio
import logging
import time
from collections import deque
from typing import Any, Optional

from vault.common import ids
from vault.common.jaiveer_placement import choose_additional
from vault.common.models import (FragmentHeader, JobState, NodeState, PullEc, PullRequest, PullResult, PullSource,
                                 Policy)
from vault.common.rpc import NetworkError
from vault.brain.jaiveer_incidents import mark_repair_started
from vault.metadata.jaiveer_db import all_rows, one

log = logging.getLogger("repair")

LOOP_S = 0.2
BACKOFF_S = (1.0, 3.0, 9.0)
BLOCKED_EVERY_S = 10.0
SOURCE_STATES = (NodeState.ALIVE, NodeState.PARTITIONED, NodeState.SUSPECT, NodeState.DRAINING)
NOT_DURABLE = (NodeState.DEAD, NodeState.RETIRED)


class TokenBucket:
    def __init__(self, rate_bytes_s: float):
        self.rate = max(rate_bytes_s, 1.0)
        self.capacity = self.rate                       # 1 s of burst
        self.tokens = self.capacity
        self.at = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.at) * self.rate)
        self.at = now

    async def take(self, n: float) -> None:
        n = min(n, self.capacity)                        # a job bigger than the bucket waits for a full bucket
        while True:
            self._refill()
            if self.tokens >= n:
                self.tokens -= n
                return
            await asyncio.sleep((n - self.tokens) / self.rate)


class Dispatcher:
    def __init__(self, ctx):
        self.ctx = ctx
        self.running: dict[int, asyncio.Task] = {}
        self.per_node: dict[str, int] = {}
        self.not_before: dict[int, float] = {}
        self.bucket = TokenBucket(ctx.cfg.repair.bandwidth_mbps * 1e6)
        self.done_bytes: deque[tuple[float, int]] = deque()
        self.blocked_at: dict[str, float] = {}
        self.wake = asyncio.Event()                     # set when a job finishes → start the next one at once

    # ── bookkeeping ──
    def _take_nodes(self, nodes: list[str]) -> None:
        for n in nodes:
            self.per_node[n] = self.per_node.get(n, 0) + 1

    def _free_nodes(self, nodes: list[str]) -> None:
        for n in nodes:
            self.per_node[n] = max(0, self.per_node.get(n, 0) - 1)

    def _nodes_ok(self, nodes: list[str]) -> bool:
        cap = self.ctx.cfg.repair.per_node
        return all(self.per_node.get(n, 0) < cap for n in nodes)

    def mbps(self) -> float:
        now = time.monotonic()
        while self.done_bytes and now - self.done_bytes[0][0] > 2.0:
            self.done_bytes.popleft()
        return sum(b for _, b in self.done_bytes) / 2.0 / 1e6

    # ── main pass ──
    async def dispatch_once(self) -> int:
        """Start as many eligible queued jobs as the limits allow. → number started."""
        ctx = self.ctx
        ctx.repair_mbps = round(self.mbps(), 1)
        if not ctx.safety().repair:
            return 0
        free = ctx.cfg.repair.max_concurrent - len(self.running)
        if free <= 0:
            return 0
        jobs = await ctx.db.fetchall("SELECT * FROM jobs WHERE state='queued' ORDER BY priority, created_at, id "
                                     "LIMIT 200")
        now = time.monotonic()
        started, blocked = 0, []
        planned_targets: dict[str, set[str]] = {}         # chunk → targets chosen in this pass
        cap = ctx.cfg.repair.per_node
        alive = [n.id for n in ctx.nodes() if n.state == NodeState.ALIVE]
        planned = 0
        for job in jobs:
            if started >= free or planned >= 3 * free + 10:
                break
            if alive and all(self.per_node.get(n, 0) >= cap for n in alive):
                break                                   # every machine is at its limit: nothing else can start
            if job["id"] in self.running or self.not_before.get(job["id"], 0) > now:
                continue
            planned += 1
            plan = await self.plan(job, planned_targets.get(job["chunk_id"], set()))
            if plan["action"] == "finish":
                await self._finish(job, plan["state"], plan.get("error"))
                continue
            if plan["action"] == "blocked":
                blocked.append(plan["reason"])
                continue
            nodes = [n for n in (plan.get("target"), plan.get("source")) if n]
            if not self._nodes_ok(nodes):
                continue
            if plan.get("target"):
                planned_targets.setdefault(job["chunk_id"], set()).add(plan["target"])
            self._take_nodes(nodes)
            self.running[job["id"]] = asyncio.create_task(self._run(job, plan, nodes), name=f"job-{job['id']}")
            started += 1
        if blocked:
            await self._blocked(blocked)
        return started

    async def _blocked(self, reasons: list[str]) -> None:
        now = time.monotonic()
        reason = max(set(reasons), key=reasons.count)
        if now - self.blocked_at.get(reason, -1e9) < BLOCKED_EVERY_S:
            return
        self.blocked_at[reason] = now
        await self.ctx.emit("repair.blocked", {}, {"n": len(reasons), "reason": reason, "why": reason})

    # ── planning ──
    async def plan(self, job: dict, also_holders: set[str]) -> dict[str, Any]:
        ctx = self.ctx
        kind = job["kind"]
        info = await ctx.db.fetchone(
            "SELECT ch.chunk_id, ch.idx, ch.size, ch.sha256 AS chunk_sha, ch.frag_size, v.version_id, v.bucket, v.key, "
            "v.policy, v.state AS vstate, v.kind AS vkind FROM chunks ch JOIN versions v ON v.version_id = ch.version_id "
            "WHERE ch.chunk_id = ?", (job["chunk_id"],)) if job["chunk_id"] else None
        if kind == "trim":
            return await self._plan_trim(job)
        if info is None or info["vstate"] != "committed" or info["vkind"] != "data":
            return {"action": "finish", "state": JobState.cancelled, "error": "version no longer current"}
        policy = Policy.model_validate_json(info["policy"])
        rows = await ctx.db.fetchall("SELECT * FROM fragments WHERE chunk_id=?", (job["chunk_id"],))
        states = {n.id: n for n in ctx.nodes()}

        def durable(r) -> bool:
            n = states.get(r["node_id"])
            return r["state"] == "ok" and n is not None and n.state not in NOT_DURABLE

        fi = job["frag_idx"]
        durable_rows = [r for r in rows if durable(r)]
        holders = {r["node_id"] for r in durable_rows}
        in_flight = {r["node_id"] for r in rows if r["state"] == "incoming"}
        if kind == "repair" and any(r["frag_idx"] == fi for r in durable_rows):
            return {"action": "finish", "state": JobState.done, "error": None}
        if kind == "move":
            src = job["source_node"]
            if not any(r["frag_idx"] == fi and r["node_id"] == src for r in durable_rows):
                return {"action": "finish", "state": JobState.cancelled, "error": "source no longer holds it"}

        # target
        target = job["target_node"]
        t = states.get(target) if target else None
        if not (t and t.state == NodeState.ALIVE and not t.fenced and target not in holders | in_flight | also_holders):
            from vault.metadata.jaiveer_uploads import fate_keys_for, get_ring
            exclude = {job["source_node"]} if kind == "move" and job["source_node"] else set()
            target = choose_additional(get_ring(ctx), states, job["chunk_id"], list(holders | in_flight | also_holders),
                                       exclude=exclude, fate_keys=fate_keys_for(ctx))
        if target is None:
            alive = sum(1 for n in states.values() if n.state == NodeState.ALIVE)
            reason = (f"only {alive} machines are on" if alive < policy.n + (1 if kind == "move" else 0)
                      else "no machine can take another copy right now")
            return {"action": "blocked", "reason": reason}

        # sources
        def rank(r):
            n = states[r["node_id"]]
            return (n.state != NodeState.ALIVE, n.route != "direct", n.slow, self.per_node.get(r["node_id"], 0),
                    r["node_id"])
        avail = sorted((r for r in durable_rows if states[r["node_id"]].state in SOURCE_STATES), key=rank)
        if kind == "move":
            avail.sort(key=lambda r: r["node_id"] != job["source_node"])        # prefer the copy being moved
        if policy.type == "erasure":
            others, seen = [], set()
            for r in avail:
                if r["frag_idx"] != fi and r["frag_idx"] not in seen:
                    seen.add(r["frag_idx"])
                    others.append(r)
            if kind == "move":
                others = [r for r in avail if r["frag_idx"] == fi][:1] + others
            if len({r["frag_idx"] for r in others}) < policy.needed and not (kind == "move" and others):
                return {"action": "blocked", "reason": "not enough healthy pieces are reachable"}
            sources, mode = others, ("copy" if kind == "move" else "ec_rebuild")
            expected = next((r["sha256"] for r in rows if r["frag_idx"] == fi and r["sha256"]), None)
        else:
            sources, mode, expected = avail, "copy", info["chunk_sha"]
        if not sources or not expected:
            return {"action": "blocked", "reason": "no healthy copy is reachable"}
        tv = states[target]
        fid = ids.fid(job["chunk_id"], fi)
        header = FragmentHeader(fid=fid, chunk_id=job["chunk_id"], frag_idx=fi, version_id=info["version_id"],
                                bucket=info["bucket"], key=info["key"], policy=policy.name,
                                chunk_sha256=info["chunk_sha"] or "", frag_sha256=expected, chunk_size=info["size"],
                                frag_size=info["frag_size"], epoch=tv.epoch)
        req = PullRequest(fid=fid, header=header, expected_sha256=expected, mode=mode,
                          sources=[PullSource(node_id=r["node_id"], addr=states[r["node_id"]].addr,
                                              fid=ids.fid(job["chunk_id"], r["frag_idx"])) for r in sources],
                          ec=PullEc(k=policy.k or 0, n=policy.n, frag_idx=fi) if mode == "ec_rebuild" else None)
        return {"action": "run", "kind": kind, "target": target, "source": sources[0]["node_id"], "req": req,
                "bytes": info["frag_size"], "fid": fid, "key": info["key"],
                "move_from": job["source_node"] if kind == "move" else None}

    async def _plan_trim(self, job: dict) -> dict[str, Any]:
        node_id = job["source_node"] or job["target_node"]
        if not node_id or job["chunk_id"] is None or job["frag_idx"] is None:
            return {"action": "finish", "state": JobState.cancelled, "error": "trim needs node, chunk and frag_idx"}
        n = self.ctx.node(node_id)
        if n is None or n.state in NOT_DURABLE:
            return {"action": "run", "kind": "trim", "source": None, "node": node_id, "drop_only": True,
                    "fid": ids.fid(job["chunk_id"], job["frag_idx"]), "bytes": 0}
        if n.state != NodeState.ALIVE:
            return {"action": "blocked", "reason": f"{n.display_name} is not answering"}
        return {"action": "run", "kind": "trim", "source": node_id, "node": node_id, "drop_only": False,
                "fid": ids.fid(job["chunk_id"], job["frag_idx"]), "bytes": 0}

    # ── execution ──
    async def _run(self, job: dict, plan: dict, nodes: list[str]) -> None:
        try:
            if plan["kind"] == "trim":
                await self._trim(job, plan)
            else:
                await self._pull(job, plan)
        except asyncio.CancelledError:
            raise
        except Exception as e:                       # never let one job kill the dispatcher
            log.exception("job %s crashed", job["id"])
            await self._failed(job, plan, f"internal: {e}")
        finally:
            self._free_nodes(nodes)
            self.running.pop(job["id"], None)
            self.wake.set()

    async def _pull(self, job: dict, plan: dict) -> None:
        ctx = self.ctx
        req: PullRequest = plan["req"]
        target, fi, cid = plan["target"], job["frag_idx"], job["chunk_id"]
        now = time.time()

        async def start(c):
            await c.execute("UPDATE jobs SET state='running', started_at=?, attempts=attempts+1, source_node=?, "
                            "target_node=?, bytes=?, error=NULL WHERE id=?",
                            (now, plan["move_from"] or plan["source"], target, plan["bytes"], job["id"]))
            await c.execute("INSERT OR REPLACE INTO fragments (chunk_id, frag_idx, node_id, state, sha256, updated_at) "
                            "VALUES (?,?,?, 'incoming', ?, ?)", (cid, fi, target, req.expected_sha256, now))
        await ctx.db.write(start)
        if job["incident_id"]:
            await mark_repair_started(ctx, job["incident_id"])
        await self.bucket.take(plan["bytes"])
        error = None
        try:
            r = await ctx.rpc.request(target, "POST", f"/v1/fragments/{req.fid}/pull",
                                      json=req.model_dump(mode="json"),
                                      headers={"X-Vault-Epoch": str(req.header.epoch)},
                                      timeout=ctx.cfg.gateway.timeout_data_s * 2)
            if r.status_code in (200, 201):
                res = PullResult.model_validate(r.json())
                if res.sha256 != req.expected_sha256:
                    error = f"target stored sha {res.sha256[:12]}… ≠ expected {req.expected_sha256[:12]}…"
            else:
                try:
                    error = f"{r.status_code} {r.json().get('error', '')}"
                except Exception:
                    error = f"{r.status_code}"
        except NetworkError as e:
            error = f"unreachable: {e.reason}"
        if error:
            async def undo(c):
                await c.execute("DELETE FROM fragments WHERE chunk_id=? AND frag_idx=? AND node_id=? AND state='incoming'",
                                (cid, fi, target))
            await ctx.db.write(undo)
            await self._failed(job, plan, error)
            return

        done_at = time.time()

        async def finish(c):
            await c.execute("UPDATE fragments SET state='ok', updated_at=? WHERE chunk_id=? AND frag_idx=? AND node_id=?",
                            (done_at, cid, fi, target))
            # the damaged/missing copies of this frag_idx were quarantined by their nodes: forget them
            await c.execute("DELETE FROM fragments WHERE chunk_id=? AND frag_idx=? AND state IN ('corrupt','missing')",
                            (cid, fi))
            await c.execute("UPDATE jobs SET state='done', finished_at=?, error=NULL WHERE id=?", (done_at, job["id"]))
            if job["incident_id"]:
                await c.execute("UPDATE incidents SET bytes_repaired = bytes_repaired + ? WHERE id=?",
                                (plan["bytes"], job["incident_id"]))
        await ctx.db.write(finish)
        self.done_bytes.append((time.monotonic(), plan["bytes"]))
        self.not_before.pop(job["id"], None)
        ctx.invalidate_snapshot()
        if plan["move_from"]:
            await self._delete_copy(plan["move_from"], cid, fi, plan["fid"])

    async def _delete_copy(self, node_id: str, cid: str, fi: int, fid: str) -> bool:
        """DELETE a fragment on a node and drop its row. Unreachable → a trim job does it later (P4)."""
        ctx = self.ctx
        n = ctx.node(node_id)
        ok = False
        if n is not None and n.state not in NOT_DURABLE:
            try:
                r = await ctx.rpc.request(node_id, "DELETE", f"/v1/fragments/{fid}",
                                          headers={"X-Vault-Epoch": str(n.epoch)})
                ok = r.status_code in (200, 204, 404)
            except NetworkError:
                ok = False
        if ok or n is None or n.state in NOT_DURABLE:
            async def drop(c):
                await c.execute("DELETE FROM fragments WHERE chunk_id=? AND frag_idx=? AND node_id=?", (cid, fi, node_id))
            await ctx.db.write(drop)
            return True
        from vault.common.models import JobKind, JobReason
        from vault.metadata.jaiveer_app import enqueue_job_tx

        async def later(c):
            await c.execute("UPDATE fragments SET state='trim' WHERE chunk_id=? AND frag_idx=? AND node_id=?",
                            (cid, fi, node_id))
            await enqueue_job_tx(c, JobKind.trim, JobReason.over_replicated, chunk_id=cid, frag_idx=fi, priority=4,
                                 source_node=node_id)
        await ctx.db.write(later)
        return False

    async def _trim(self, job: dict, plan: dict) -> None:
        ctx = self.ctx
        now = time.time()

        async def start(c):
            await c.execute("UPDATE jobs SET state='running', started_at=?, attempts=attempts+1 WHERE id=?",
                            (now, job["id"]))
        await ctx.db.write(start)
        if plan["drop_only"]:
            ok = True
        else:
            n = ctx.node(plan["node"])
            try:
                r = await ctx.rpc.request(plan["node"], "DELETE", f"/v1/fragments/{plan['fid']}",
                                          headers={"X-Vault-Epoch": str(n.epoch)})
                ok = r.status_code in (200, 204, 404)
                err = None if ok else f"{r.status_code}"
            except NetworkError as e:
                ok, err = False, f"unreachable: {e.reason}"
        if not ok:
            await self._failed(job, plan, err)
            return

        async def finish(c):
            await c.execute("DELETE FROM fragments WHERE chunk_id=? AND frag_idx=? AND node_id=?",
                            (job["chunk_id"], job["frag_idx"], plan["node"]))
            await c.execute("UPDATE jobs SET state='done', finished_at=? WHERE id=?", (time.time(), job["id"]))
        await ctx.db.write(finish)

    async def _finish(self, job: dict, state: JobState, error: Optional[str]) -> None:
        async def fn(c):
            await c.execute("UPDATE jobs SET state=?, finished_at=?, error=? WHERE id=? AND state='queued'",
                            (state.value, time.time(), error, job["id"]))
        await self.ctx.db.write(fn)
        self.not_before.pop(job["id"], None)

    async def _failed(self, job: dict, plan: dict, error: Optional[str]) -> None:
        ctx = self.ctx
        row = await ctx.db.fetchone("SELECT attempts FROM jobs WHERE id=?", (job["id"],))
        attempts = row["attempts"] if row else 1
        final = attempts >= ctx.cfg.repair.max_attempts

        async def fn(c):
            await c.execute("UPDATE jobs SET state=?, error=?, finished_at=? WHERE id=?",
                            ("failed" if final else "queued", (error or "")[:300], time.time() if final else None,
                             job["id"]))
        await ctx.db.write(fn)
        if final:
            self.not_before.pop(job["id"], None)
            await ctx.emit("repair.failed", {"key": plan.get("key") or job["chunk_id"]},
                           {"job": job["id"], "error": error or "unknown", "attempts": attempts},
                           file=plan.get("key") or job["chunk_id"])
        else:
            self.not_before[job["id"]] = time.monotonic() + BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
        log.info("job %s attempt %s failed: %s", job["id"], attempts, error)


def dispatcher(ctx) -> Dispatcher:
    d = getattr(ctx, "_dispatcher", None)
    if d is None:
        d = ctx._dispatcher = Dispatcher(ctx)
    return d


async def run(ctx) -> None:
    d = dispatcher(ctx)
    try:
        while True:
            d.wake.clear()
            try:
                await d.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("dispatch failed")
            try:
                await asyncio.wait_for(d.wake.wait(), timeout=LOOP_S)
            except asyncio.TimeoutError:
                pass
    finally:
        for t in list(d.running.values()):
            t.cancel()
