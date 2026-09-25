"""Supervisor + chaos controller API (ARCHITECTURE §6, §7.4). Owner: Anushka.

Done: /procs, /procs/{pid}/kill|start|restart.
TODO (tasks/anushka_tasks.md A2/A3): /power/*, /chaos/*, /nodes/add, /cluster/reset, /demo/seed, /chaos/script.
"""
from fastapi import FastAPI

from vault.common.config import VaultConfig
from vault.common.models import Proc, ProcList
from vault.common.service import VaultHTTPError, make_app
from vault.supervisor.anushka_procs import Procs


def create_app(cfg: VaultConfig, pid: str = "sup") -> FastAPI:
    procs = Procs(cfg)

    async def lifespan(app: FastAPI):
        await procs.start_all()
        yield
        await procs.kill_all()

    app = make_app(cfg, pid, lifespan)
    app.state.procs = procs

    def _known(p: str) -> None:
        if p not in procs.children:
            raise VaultHTTPError(404, "unknown_proc", f"No process called {p}.")

    @app.get("/procs")
    async def list_procs() -> ProcList:
        return ProcList(procs=procs.list())

    @app.post("/procs/{p}/kill")
    async def kill(p: str) -> Proc:
        _known(p)
        return await procs.kill(p)

    @app.post("/procs/{p}/start")
    async def start(p: str) -> Proc:
        _known(p)
        return await procs.start(p)

    @app.post("/procs/{p}/restart")
    async def restart(p: str) -> Proc:
        _known(p)
        return await procs.restart(p)

    return app
