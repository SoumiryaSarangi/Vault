"""Supervisor + chaos controller API (ARCHITECTURE §6, §7.4). Owner: Anushka.

Processes: GET /procs, POST /procs/{pid}/kill|start|restart
Chaos:     POST /chaos/link, /chaos/node/{pid}, /chaos/corrupt, GET /chaos, POST /chaos/clear_all
Power:     POST /power/cut, /power/restore
Scripts:   POST /chaos/script, /chaos/script/{id}/stop
Cluster:   POST /nodes/add, /cluster/reset, /demo/seed
LAN mode:  POST /nodes/join (node agents), POST /nodes/{id}/rename, GET /cluster/info   (Soum, docs/soum_lan_demo.md)
"""
import asyncio

from fastapi import FastAPI

from vault.common.config import VaultConfig
from vault.common.models import (AddNodeRequest, ClusterInfo, CorruptRequest, CorruptResult, FaultList, JoinRequest,
                                 JoinResult, LinkRequest, NodeChaosRequest, PowerCutRequest, PowerCutResult,
                                 PowerRestoreRequest, PowerRestoreResult, Proc, ProcList, RenameRequest, ResetRequest,
                                 ResetResult, ScriptRequest, ScriptStarted, SeedRequest, SeedResult)
from vault.common.service import VaultHTTPError, make_app
from vault.supervisor.anushka_chaos_ctl import ChaosController
from vault.supervisor.anushka_cluster import ClusterOps
from vault.supervisor.anushka_procs import Procs


def create_app(cfg: VaultConfig, pid: str = "sup") -> FastAPI:
    procs = Procs(cfg)
    chaos = ChaosController(cfg, procs)
    cluster = ClusterOps(cfg, procs, chaos)

    async def lifespan(app: FastAPI):
        await procs.start_all()
        poll = asyncio.create_task(procs.poll_agents())      # machines on other laptops (LAN mode)
        yield
        poll.cancel()
        chaos.forget_all()
        await procs.kill_all()

    app = make_app(cfg, pid, lifespan)
    app.state.procs, app.state.chaos, app.state.cluster = procs, chaos, cluster

    def _known(p: str) -> None:
        if p not in procs.children:
            raise VaultHTTPError(404, "unknown_proc", f"No process called {p}.")

    # ── processes ──
    @app.get("/procs")
    async def list_procs() -> ProcList:
        return ProcList(procs=procs.list())

    @app.post("/procs/{p}/kill")
    async def kill(p: str) -> Proc:
        _known(p)
        return await chaos.kill_proc(p)

    @app.post("/procs/{p}/start")
    async def start(p: str) -> Proc:
        _known(p)
        return await chaos.start_proc(p)

    @app.post("/procs/{p}/restart")
    async def restart(p: str) -> Proc:
        _known(p)
        await chaos.kill_proc(p)
        return await chaos.start_proc(p)

    # ── chaos ──
    @app.get("/chaos")
    async def list_faults() -> FaultList:
        return FaultList(faults=chaos.active())

    @app.post("/chaos/link")
    async def link(req: LinkRequest) -> FaultList:
        return FaultList(faults=await chaos.link(req))

    @app.post("/chaos/node/{p}")
    async def node(p: str, req: NodeChaosRequest) -> FaultList:
        return FaultList(faults=await chaos.node(p, req))

    @app.post("/chaos/corrupt")
    async def corrupt_random(req: CorruptRequest) -> CorruptResult:
        """Dashboard "Damage copies": 1 / 10 / 50 random copies across running nodes."""
        return CorruptResult(corrupted=await chaos.corrupt_random(req.count or 10, req.mode))

    @app.post("/chaos/clear_all")
    async def clear_all() -> FaultList:
        return FaultList(faults=await chaos.clear_all())

    # ── power ──
    @app.post("/power/cut")
    async def power_cut(req: PowerCutRequest) -> PowerCutResult:
        return await chaos.power_cut(req)

    @app.post("/power/restore")
    async def power_restore(req: PowerRestoreRequest) -> PowerRestoreResult:
        return await chaos.power_restore(req)

    # ── scripts ──
    @app.post("/chaos/script")
    async def script(req: ScriptRequest) -> ScriptStarted:
        return ScriptStarted(script_id=await chaos.run_script(req))

    @app.post("/chaos/script/{sid}/stop")
    async def script_stop(sid: str) -> FaultList:
        if not chaos.stop_script(sid):
            raise VaultHTTPError(404, "no_script", f"No running script {sid}.")
        return FaultList(faults=chaos.active())

    # ── cluster ──
    @app.post("/nodes/add")
    async def add_node(req: AddNodeRequest) -> Proc:
        return await cluster.add_node(req)

    @app.post("/nodes/join")
    async def join(req: JoinRequest) -> JoinResult:
        return await cluster.join(req)

    @app.post("/nodes/{p}/rename")
    async def rename(p: str, req: RenameRequest) -> Proc:
        return await cluster.rename(p, req.display_name)

    @app.get("/cluster/info")
    async def info() -> ClusterInfo:
        return cluster.info()

    @app.post("/cluster/reset")
    async def reset(req: ResetRequest) -> ResetResult:
        return await cluster.reset(req)

    @app.post("/demo/seed")
    async def seed(req: SeedRequest) -> SeedResult:
        return await cluster.seed(req)

    return app
