"""Incidents: open on DEAD / corruption burst / power cut / fate violation; detect/grace/repair/MTTR. §4.8. Task J8.
Owner: Jaiveer.

J6 part: ground-truth fault store (from POST /v1/incidents/fault) and open_incident_tx, used by the detector when a
node becomes DEAD. J7 fills repair_started_at / remaining_chunks / recovered_at; J8 adds GET /v1/incidents + metrics.
"""
import time
from typing import Optional

import aiosqlite

GROUND_TRUTH_MAX_AGE_S = 300.0     # a supervisor fault report older than this isn't used for fault_at


class FaultLog:
    """Latest supervisor ground truth per (kind, subject): POST /v1/incidents/fault {kind, subject, at}."""

    def __init__(self):
        self.latest: dict[tuple[str, str], float] = {}
        self.node_faults: dict[str, set[str]] = {}     # node → active chaos faults shown on its card (slow, disk_full)

    def record(self, kind: str, subject: str, at: float) -> None:
        self.latest[(kind, subject)] = at
        if kind in ("slow", "disk_full"):
            self.node_faults.setdefault(subject, set()).add(kind)
        elif kind == "clear":
            if subject in ("cluster", "all", ""):
                self.node_faults.clear()
            else:
                self.node_faults.pop(subject, None)
        elif kind == "node_start":
            self.node_faults.get(subject, set()).discard("disk_full")

    def fault_at(self, kinds: tuple[str, ...], subject: str, not_before: float) -> Optional[float]:
        """Most recent ground-truth time for subject among kinds, if it's recent and not after `not_before`."""
        now = time.time()
        best = None
        for k in kinds:
            at = self.latest.get((k, subject))
            if at is not None and now - at <= GROUND_TRUTH_MAX_AGE_S and at <= not_before + 0.5:
                best = at if best is None else max(best, at)
        return best

    def faults_for(self, node_id: str) -> list[str]:
        return sorted(self.node_faults.get(node_id, ()))


async def open_incident_tx(c: aiosqlite.Connection, kind: str, subject: Optional[str], fault_at: Optional[float],
                           detected_at: Optional[float], affected_chunks: int) -> int:
    """Inside a write fn. An incident with nothing to repair is recovered at once."""
    now = time.time()
    recovered = now if affected_chunks == 0 else None
    cur = await c.execute(
        "INSERT INTO incidents (kind, subject, fault_at, detected_at, repair_started_at, recovered_at, "
        "affected_chunks, remaining_chunks, bytes_repaired) VALUES (?,?,?,?,NULL,?,?,?,0)",
        (kind, subject, fault_at, detected_at, recovered, affected_chunks, affected_chunks))
    return cur.lastrowid


# ── J7: repair progress on incidents ──

def fmt_size(n: float) -> str:
    from vault.common.events import fmt_bytes
    return fmt_bytes(n)


async def mark_repair_started(ctx, inc_id: int) -> None:
    """First job of an incident starts running → repair_started_at + repair.started (once)."""
    now = time.time()

    async def fn(c):
        cur = await c.execute("UPDATE incidents SET repair_started_at=? WHERE id=? AND repair_started_at IS NULL",
                              (now, inc_id))
        return cur.rowcount
    if not await ctx.db.write(fn):
        return
    inc = await ctx.db.fetchone("SELECT * FROM incidents WHERE id=?", (inc_id,))
    jobs = await ctx.db.fetchall("SELECT priority, bytes FROM jobs WHERE incident_id=?", (inc_id,))
    total = sum(j["bytes"] for j in jobs)
    subject = {"node": inc["subject"]} if inc["subject"] and ctx.node(inc["subject"]) else {}
    await ctx.emit("repair.started", subject,
                   {"n": len(jobs), "size": fmt_size(total), "inc": inc_id,
                    "p0": sum(1 for j in jobs if j["priority"] == 0), "p1": sum(1 for j in jobs if j["priority"] == 1),
                    "mbps": int(ctx.cfg.repair.bandwidth_mbps), "bytes": total},
                   **({} if subject else {"node": "a damaged machine"}))


async def update_open_incidents(ctx, below_target: set[str]) -> None:
    """Called by the scheduler scan. below_target = chunk_ids currently below n durable copies.
    remaining = affected chunks (from the incident's jobs) still below target; 0 → recovered + repair.completed."""
    open_incs = await ctx.db.fetchall("SELECT * FROM incidents WHERE recovered_at IS NULL")
    if not open_incs:
        return
    now = time.time()
    st = _progress_state(ctx)
    for inc in open_incs:
        rows = await ctx.db.fetchall("SELECT DISTINCT chunk_id FROM jobs WHERE incident_id=? AND chunk_id IS NOT NULL",
                                     (inc["id"],))
        affected = {r["chunk_id"] for r in rows}
        if not affected and inc["detected_at"] and now - inc["detected_at"] < 5.0:
            continue                          # just opened; its jobs are being attached
        if len(affected) > inc["affected_chunks"]:
            async def grow(c, a=len(affected), i=inc["id"]):
                await c.execute("UPDATE incidents SET affected_chunks=? WHERE id=?", (a, i))
            await ctx.db.write(grow)
        remaining = len(affected & below_target)
        if remaining != inc["remaining_chunks"]:
            async def upd(c, r=remaining, i=inc["id"]):
                await c.execute("UPDATE incidents SET remaining_chunks=? WHERE id=?", (r, i))
            await ctx.db.write(upd)
        total = max(inc["affected_chunks"], len(affected), 1)
        if remaining == 0:
            await _complete(ctx, inc, now)
        elif inc["repair_started_at"] and now - st.get(inc["id"], 0) >= 1.0 and remaining < total:
            st[inc["id"]] = now
            mbps = max(ctx.repair_mbps, 0.1)
            left_bytes = (inc["bytes_repaired"] / max(total - remaining, 1)) * remaining
            done_jobs = (await ctx.db.fetchone(
                "SELECT COUNT(*) AS n FROM jobs WHERE incident_id=? AND state='done'", (inc["id"],)))["n"]
            all_jobs = (await ctx.db.fetchone("SELECT COUNT(*) AS n FROM jobs WHERE incident_id=?", (inc["id"],)))["n"]
            await ctx.emit("repair.progress", {"incident": inc["id"]},
                           {"pct": round(100 * (total - remaining) / total), "eta": round(left_bytes / (mbps * 1e6), 1),
                            "done": done_jobs, "total": all_jobs, "mbps": ctx.repair_mbps})


def _progress_state(ctx) -> dict[int, float]:
    if not hasattr(ctx, "_progress_emitted"):
        ctx._progress_emitted = {}
    return ctx._progress_emitted


async def _complete(ctx, inc: dict, now: float) -> None:
    started = inc["repair_started_at"] or now

    async def fn(c):
        await c.execute("UPDATE incidents SET recovered_at=?, repair_started_at=COALESCE(repair_started_at, ?), "
                        "remaining_chunks=0 WHERE id=? AND recovered_at IS NULL", (now, now, inc["id"]))
    await ctx.db.write(fn)
    fault = inc["fault_at"] or inc["detected_at"] or started
    detected = inc["detected_at"] or fault
    d, g, r = detected - fault, started - detected, now - started
    await ctx.emit("repair.completed", {"incident": inc["id"]},
                   {"mttr": now - fault, "d": d, "g": g, "r": r, "bytes": fmt_size(inc["bytes_repaired"]),
                    "bytes_raw": inc["bytes_repaired"], "kind": inc["kind"], "subject_id": inc["subject"]})


async def corruption_incident(ctx, node_id: str) -> int:
    """Group corruption reports: reuse an open corruption incident, else open one (fault_at from ground truth)."""
    row = await ctx.db.fetchone("SELECT id FROM incidents WHERE kind='corruption' AND recovered_at IS NULL "
                                "ORDER BY id DESC LIMIT 1")
    if row:
        return row["id"]
    now = time.time()
    fault_at = (ctx.faults.fault_at(("corruption",), node_id, now) or ctx.faults.fault_at(("corruption",), "cluster", now)
                or now)

    async def fn(c):
        cur = await c.execute("INSERT INTO incidents (kind, subject, fault_at, detected_at, affected_chunks, "
                              "remaining_chunks) VALUES ('corruption', ?, ?, ?, 0, 0)", (node_id, fault_at, now))
        return cur.lastrowid
    return await ctx.db.write(fn)
