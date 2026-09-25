"""Metadata service + health brain host, :7000 (ARCHITECTURE §7.2). Owner: Jaiveer. Tasks J4–J8.

Builds the BrainContext (vault.common.brain_api) and starts every brain loop in the lifespan:
Jaiveer's detector/scheduler/repair/auditor/metrics and Soum's reconciler/gc/rebalancer `run(ctx)`.
Registers events.set_sink(...) and events.set_name_resolver(...).

J4: DB open + schema + node seeding, BrainContext, event sink, startup recovery (meta.recovered),
routes for buckets, node register/heartbeat/inventory/labels, config, mode, events.
Brain loops (J6+) are started from BRAIN_LOOPS once they exist.
"""
import asyncio
import copy
import importlib
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import aiosqlite
from fastapi import FastAPI
from pydantic import ValidationError

from vault.common import events
from vault.common.config import CONTROL_DISPLAY_NAMES, SafetyCfg, VaultConfig
from vault.common.models import JobKind, JobReason, NodeView
from vault.common.service import make_app
from vault.brain.jaiveer_membership import Membership
from vault.metadata.jaiveer_db import Database, kv_set, one
from vault.metadata.jaiveer_stream import EventBus

log = logging.getLogger("meta")

# (module, function) started as asyncio tasks in the lifespan when the module defines it (J6+, Soum S8/S9).
BRAIN_LOOPS: list[tuple[str, str]] = [
    ("vault.brain.jaiveer_detector", "run"),
    ("vault.brain.jaiveer_scheduler", "run"),
    ("vault.brain.jaiveer_repair", "run"),
    ("vault.brain.jaiveer_auditor", "run"),
    ("vault.brain.jaiveer_metrics", "run"),
    ("vault.brain.soum_gc", "run"),
    ("vault.brain.soum_rebalancer", "run"),
]


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Brain:
    """The BrainContext every brain module receives (brain_api.BrainContext)."""

    def __init__(self, base_cfg: VaultConfig, db: Database, membership: Membership, bus: EventBus, app: FastAPI):
        self.base_cfg = base_cfg
        self.cfg = base_cfg
        self.db = db
        self.membership = membership
        self.bus = bus
        self.app = app
        self.rpc = None                    # set in the lifespan (make_app creates it)
        self.mode = "vault"
        self.config_version = 0
        self.overrides: dict[str, Any] = {}
        self.started_mono = time.monotonic()
        self.started_wall = time.time()
        # J6: snapshot cache, ground truth, gateway stats; J7 fills repair_mbps / job_progress
        from vault.brain.jaiveer_incidents import FaultLog
        self.faults = FaultLog()
        self._snap = None
        self._snap_at = 0.0
        self.gw_stats: tuple[Any, float] = (None, 0.0)
        self.repair_mbps = 0.0
        self.job_progress: dict[int, float] = {}

    def invalidate_snapshot(self) -> None:
        self._snap_at = 0.0

    # ── BrainContext ──
    def now(self) -> float:
        return time.time()

    def nodes(self) -> list[NodeView]:
        return self.membership.nodes()

    def node(self, node_id: str) -> Optional[NodeView]:
        return self.membership.node(node_id)

    def safety(self) -> SafetyCfg:
        return self.cfg.safety

    async def emit(self, type_: str, subject: dict[str, Any], data: Optional[dict[str, Any]] = None,
                   **fields: Any) -> None:
        try:
            await events.emit(type_, subject, data, **fields)
        except Exception:                       # an event must never break the caller
            log.exception("emit %s failed", type_)

    async def enqueue_job(self, kind: JobKind, reason: JobReason, *, chunk_id: Optional[str], frag_idx: Optional[int],
                          priority: int, source_node: Optional[str] = None, target_node: Optional[str] = None,
                          incident_id: Optional[int] = None) -> int:
        async def fn(c):
            return await enqueue_job_tx(c, kind, reason, chunk_id=chunk_id, frag_idx=frag_idx, priority=priority,
                                        source_node=source_node, target_node=target_node, incident_id=incident_id)
        return await self.db.write(fn)

    # ── config / mode ──
    def config_body(self) -> dict[str, Any]:
        """GET /v1/config: the full effective config plus config_version and mode (flat)."""
        return {**self.cfg.model_dump(mode="json"), "config_version": self.config_version, "mode": self.mode}

    def _effective(self, overrides: dict[str, Any]) -> VaultConfig:
        return VaultConfig.model_validate(_deep_merge(self.base_cfg.model_dump(mode="json"), overrides))

    async def apply_config(self, patch: dict[str, Any], mode: Optional[str] = None) -> None:
        """Validate base ⊕ overrides ⊕ patch, persist overrides + config_version, apply side effects.
        Raises ValidationError / ValueError on a bad patch (nothing changes)."""
        overrides = _deep_merge(self.overrides, patch)
        new_cfg = self._effective(overrides)
        new_mode = mode or self.mode

        async def fn(c):
            await kv_set(c, "config_overrides", overrides)
            await kv_set(c, "mode", new_mode)
            row = await one(c, "SELECT v FROM kv WHERE k='config_version'")
            v = (int(row["v"]) if row else 0) + 1
            await kv_set(c, "config_version", str(v))
            return v
        self.config_version = await self.db.write(fn)
        self.overrides, self.cfg, self.mode = overrides, new_cfg, new_mode
        await self._apply_side_effects()

    async def _apply_side_effects(self) -> None:
        self.app.state.cfg = self.cfg
        self.app.state.config_version = self.config_version
        if self.rpc is not None:
            self.rpc.relay_enabled = self.cfg.safety.relay
        if self.db.durable != self.cfg.safety.durable_writes:
            await self.db.set_durable(self.cfg.safety.durable_writes)

    async def load_config_state(self) -> None:
        raw = await self.db.kv_get("config_overrides")
        self.overrides = json.loads(raw) if raw else {}
        try:
            self.cfg = self._effective(self.overrides)
        except (ValidationError, ValueError):
            log.error("stored config overrides are invalid; ignoring them")
            self.overrides, self.cfg = {}, self.base_cfg
        self.mode = await self.db.kv_get("mode", "vault") or "vault"
        self.config_version = int(await self.db.kv_get("config_version", "0") or 0)
        await self._apply_side_effects()


async def enqueue_job_tx(c: aiosqlite.Connection, kind: JobKind, reason: JobReason, *, chunk_id: Optional[str],
                         frag_idx: Optional[int], priority: int, source_node: Optional[str] = None,
                         target_node: Optional[str] = None, incident_id: Optional[int] = None,
                         bytes_: int = 0) -> int:
    """Inside a write fn: insert a queued job unless the same kind+chunk_id+frag_idx is queued/running.
    Returns the (new or existing) job id. A lower priority number on a duplicate upgrades the queued job."""
    kind_v = kind.value if hasattr(kind, "value") else kind
    reason_v = reason.value if hasattr(reason, "value") else reason
    existing = await one(c, "SELECT id, priority, state FROM jobs WHERE kind=? AND chunk_id IS ? AND frag_idx IS ? "
                            "AND state IN ('queued','running') ORDER BY id LIMIT 1", (kind_v, chunk_id, frag_idx))
    if existing:
        if existing["state"] == "queued" and priority < existing["priority"]:
            await c.execute("UPDATE jobs SET priority=? WHERE id=?", (priority, existing["id"]))
        return existing["id"]
    cur = await c.execute(
        "INSERT INTO jobs (kind, reason, chunk_id, frag_idx, source_node, target_node, priority, state, bytes, "
        "incident_id, created_at) VALUES (?,?,?,?,?,?,?, 'queued', ?, ?, ?)",
        (kind_v, reason_v, chunk_id, frag_idx, source_node, target_node, priority, bytes_, incident_id, time.time()))
    return cur.lastrowid


async def _recover(brain: Brain) -> None:
    """Metadata startup (§4.14): seed nodes, load membership, expire stale uploads, resume jobs, meta.recovered."""
    t0 = time.perf_counter()
    cfg, db = brain.base_cfg, brain.db
    now = time.time()
    cutoff = now - cfg.gc.upload_timeout_s

    async def fn(c):
        await c.executemany("INSERT OR IGNORE INTO nodes (id, addr, display_name, labels, capacity_bytes, "
                            "persisted_state, epoch, created_at) VALUES (?,?,?,?,?,?,?,?)",
                            Membership.seed_rows(cfg, now))
        cur = await c.execute("UPDATE versions SET state='aborted' WHERE state='pending' AND created_at < ?",
                              (cutoff,))
        expired = cur.rowcount
        await c.execute("UPDATE jobs SET state='queued', started_at=NULL WHERE state='running'")
        if await one(c, "SELECT v FROM kv WHERE k='commit_seq'") is None:
            await kv_set(c, "commit_seq", "0")
        return expired
    expired = await db.write(fn)

    brain.membership.load(await db.fetchall("SELECT * FROM nodes ORDER BY created_at, id"))
    await brain.load_config_state()
    files = (await db.fetchone(
        "SELECT COUNT(*) AS n FROM objects o JOIN versions v ON v.version_id = o.current_version_id "
        "WHERE v.kind = 'data' AND v.state = 'committed'"))["n"]
    versions = (await db.fetchone("SELECT COUNT(*) AS n FROM versions WHERE state IN ('committed','superseded')"))["n"]
    ms = round((time.perf_counter() - t0) * 1000 + brain.db_open_ms)
    await brain.emit("meta.recovered", {}, {"files": files, "versions": versions, "pending": expired, "ms": ms,
                                             "first_start": db.created})


def _start_brain_loops(brain: Brain) -> list[asyncio.Task]:
    tasks = []
    for mod_name, fn_name in BRAIN_LOOPS:
        try:
            fn = getattr(importlib.import_module(mod_name), fn_name, None)
        except Exception:
            log.exception("could not import %s", mod_name)
            continue
        if fn is None or not asyncio.iscoroutinefunction(fn):
            continue

        async def guarded(fn=fn, name=mod_name):
            try:
                await fn(brain)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("brain loop %s crashed", name)
        tasks.append(asyncio.create_task(guarded(), name=mod_name))
    return tasks


def create_app(cfg: VaultConfig, pid: str = "meta", db_path: Optional[str] = None,
               start_loops: bool = True) -> FastAPI:
    """start_loops=False: no detector/scheduler/etc. background loops (tests drive them by hand)."""
    db = Database(db_path or cfg.data_path("meta", "vault.db"), durable=cfg.safety.durable_writes)
    membership = Membership(cfg)
    bus = EventBus(db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        brain: Brain = app.state.brain
        brain.rpc = app.state.rpc
        brain.started_mono, brain.started_wall = time.monotonic(), time.time()
        brain.membership.started_at = brain.started_mono
        t0 = time.perf_counter()
        await db.open()
        brain.db_open_ms = (time.perf_counter() - t0) * 1000
        await bus.load_recent()
        events.set_sink(bus.sink)
        events.set_name_resolver(lambda i: CONTROL_DISPLAY_NAMES.get(i) or membership.display_name(i))
        await _recover(brain)
        for n in membership.nodes():                    # added machines (n7+) the rpc doesn't know yet
            if n.id not in cfg.nodes:
                brain.rpc.set_addr(n.id, n.addr)
        tasks = _start_brain_loops(brain) if start_loops else []
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            events.set_sink(None)   # type: ignore[arg-type]
            await db.close()

    app = make_app(cfg, pid, lifespan, title="vault-metadata")
    brain = Brain(cfg, db, membership, bus, app)
    brain.db_open_ms = 0.0
    app.state.brain, app.state.bus, app.state.db, app.state.membership = brain, bus, db, membership

    from vault.metadata import jaiveer_cluster, jaiveer_objects, jaiveer_reports, jaiveer_stream
    app.include_router(jaiveer_cluster.router)
    app.include_router(jaiveer_objects.router)
    app.include_router(jaiveer_stream.router)
    app.include_router(jaiveer_reports.router)
    try:
        from vault.metadata import jaiveer_uploads
        if hasattr(jaiveer_uploads, "router"):
            app.include_router(jaiveer_uploads.router)
    except ImportError:
        pass
    return app
