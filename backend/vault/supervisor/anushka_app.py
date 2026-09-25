"""Supervisor + chaos controller API (ARCHITECTURE §6, §7.4). Owner: Anushka.

Done: /procs, /procs/{pid}/kill|start|restart (A0); /chaos/link, /chaos/node/{pid}, /chaos/corrupt,
      GET /chaos, /chaos/clear_all (A2).
TODO (tasks/anushka_tasks.md A3): /power/cut, /power/restore, /nodes/add, /cluster/reset, /demo/seed, /chaos/script.
"""
from fastapi import FastAPI

from vault.common.config import VaultConfig
from vault.common.models import CorruptRequest, CorruptResult, FaultList, LinkRequest, NodeChaosRequest, Proc, ProcList
from vault.common.service import VaultHTTPError, make_app
from vault.supervisor.anushka_chaos_ctl import ChaosController
from vault.supervisor.anushka_procs import Procs


def create_app(cfg: VaultConfig, pid: str = "sup") -> FastAPI:
    procs = Procs(cfg)
    chaos = ChaosController(cfg, procs)

    async def lifespan(app: FastAPI):
        await procs.start_all()
        yield
        await procs.kill_all()

    app = make_app(cfg, pid, lifespan)
    app.state.procs = procs
    app.state.chaos = chaos

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
        was_running = procs.children[p].running
        view = await procs.kill(p)
        if was_running:
            await chaos.on_kill(p)
        return view

    @app.post("/procs/{p}/start")
    async def start(p: str) -> Proc:
        _known(p)
        was_running = procs.children[p].running
        view = await procs.start(p)
        if not was_running:
            await chaos.on_start(p)
        return view

    @app.post("/procs/{p}/restart")
    async def restart(p: str) -> Proc:
        _known(p)
        await procs.kill(p)
        await chaos.on_kill(p)
        view = await procs.start(p)
        await chaos.on_start(p)
        return view

    # ── chaos (A2) ──
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
        """Dashboard "Damage copies" (1 / 10 / 50 random copies across running nodes)."""
        return CorruptResult(corrupted=await chaos.corrupt_random(req.count or 10, req.mode))

    @app.post("/chaos/clear_all")
    async def clear_all() -> FaultList:
        return FaultList(faults=await chaos.clear_all())

    return app
