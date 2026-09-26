"""Service boilerplate shared by every Python process. Owner: Anushka (shared).

Contract for every service module (node, metadata, gateway, oracle, supervisor):

    def create_app(cfg: VaultConfig, pid: str) -> FastAPI

Build it with make_app(); start background loops in the lifespan you pass in. The entry-point
shims (`python -m vault.node --id n3`, etc.) call run_service(), so owners never touch uvicorn.
"""
import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from vault.common.config import VaultConfig, load_config
from vault.common.log import setup_logging
from vault.common.models import ErrorBody, ServiceHealth
from vault.common.netsim import NetSim, init_netsim, install_netsim
from vault.common.rpc import init_rpc

EXPOSE_HEADERS = ["ETag", "X-Vault-Seq", "X-Vault-Commit-Seq", "X-Vault-Read-Path"]
# localhost, plus private LAN addresses so the dashboard can be opened from any laptop in LAN mode
LOCAL_ORIGINS = (r"http://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
                 r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?")

Lifespan = Callable[[FastAPI], "AsyncIterator[None]"]


class VaultHTTPError(Exception):
    """raise VaultHTTPError(503, "quorum_not_met", "Only 1 of 2 machines confirmed.", {"got": 1})"""

    def __init__(self, status: int, error: str, message: str = "", detail: Optional[dict] = None):
        self.status, self.body = status, ErrorBody(error=error, message=message, detail=detail or {})


def make_app(cfg: VaultConfig, pid: str, lifespan: Optional[Lifespan] = None, title: str = "") -> FastAPI:
    """FastAPI app with: netsim + /_chaos, CORS for the dashboard, error shape, GET /_vault/health.

    Also creates the process-wide NetSim and Rpc (rpc client opened/closed around your lifespan).
    """
    ns: NetSim = init_netsim(pid)

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        rpc = init_rpc(pid, cfg, ns)
        app.state.rpc = rpc
        try:
            if lifespan is None:
                yield
            else:
                cm = lifespan(app)   # accepts a plain async generator or an @asynccontextmanager function
                if not hasattr(cm, "__aenter__"):
                    cm = asynccontextmanager(lambda _a: cm)(app)
                async with cm:
                    yield
        finally:
            await rpc.close()

    app = FastAPI(title=title or f"vault-{pid}", lifespan=_lifespan)
    app.state.cfg, app.state.pid, app.state.netsim = cfg, pid, ns
    app.state.config_version = 0
    install_netsim(app, ns, enabled=cfg.chaos.enabled)
    # The dashboard (web.origin, plus localhost/LAN addresses on any port) may call us.
    app.add_middleware(CORSMiddleware, allow_origins=[cfg.web.origin], allow_origin_regex=LOCAL_ORIGINS,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=EXPOSE_HEADERS)

    @app.exception_handler(VaultHTTPError)
    async def _vault_error(_: Request, exc: VaultHTTPError):
        return JSONResponse(exc.body.model_dump(), status_code=exc.status)

    @app.get("/_vault/health")
    async def health() -> ServiceHealth:
        return ServiceHealth(pid=pid, ok=True, config_version=app.state.config_version)

    return app


def run_service(default_pid: str, factory: Callable[[VaultConfig, str], FastAPI], needs_id: bool = False) -> None:
    """Entry point used by the `python -m vault.<service>` shims."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default=default_pid, required=needs_id, help="participant id, e.g. n3")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    if args.config:
        os.environ["VAULT_CONFIG"] = args.config
    cfg = load_config()
    setup_logging(args.id, cfg.cluster.logs_dir)
    if sys.platform == "win32":   # subprocesses and sockets need the Proactor loop on Windows
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    app = factory(cfg, args.id)
    uvicorn.run(app, host=cfg.listen_host(), port=cfg.port(args.id), workers=1, log_level="warning",
                access_log=False, timeout_graceful_shutdown=1)
