"""Durability Oracle, :7090 (ARCHITECTURE §5, §7.5). Owner: Urooz. Tasks U1, U3, U5.

Talks ONLY to the gateway (client traffic), metadata (/v1/inspect/objects, /v1/mode, /v1/events)
and the supervisor (reset, chaos script). Never writes to Vault's DB. Never killed by chaos.
"""
import asyncio
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from vault.common.config import VaultConfig
from vault.common.models import RunCreated, RunList, RunRequest, RunStatus
from vault.common.rpc import get_rpc
from vault.common.service import VaultHTTPError, make_app
from vault.oracle.urooz_runs import (
    get_run,
    get_run_list,
    is_run_active,
    start_run,
    stop_run,
    subscribe,
    unsubscribe,
)


def create_app(cfg: VaultConfig, pid: str = "oracle") -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield  # no background tasks beyond the run tasks themselves

    app = make_app(cfg, pid, lifespan=lifespan, title="vault-oracle")

    # ── POST /runs ────────────────────────────────────────────────────────────
    @app.post("/runs", status_code=201)
    async def create_run(req: RunRequest) -> RunCreated:
        if is_run_active():
            raise VaultHTTPError(409, "run_active",
                                 "A run is already in progress. Stop it first.")
        rpc = get_rpc()
        run_id = start_run(req, cfg, rpc)
        return RunCreated(run_id=run_id)

    # ── GET /runs ─────────────────────────────────────────────────────────────
    @app.get("/runs")
    async def list_runs() -> RunList:
        return get_run_list()

    # ── GET /runs/{id} ────────────────────────────────────────────────────────
    @app.get("/runs/{run_id}")
    async def get_run_status(run_id: str) -> RunStatus:
        run = get_run(run_id)
        if run is None:
            raise VaultHTTPError(404, "not_found", f"Run {run_id!r} not found.")
        return run

    # ── GET /runs/{id}/stream  (SSE) ──────────────────────────────────────────
    @app.get("/runs/{run_id}/stream")
    async def stream_run(run_id: str, request: Request) -> StreamingResponse:
        if get_run(run_id) is None:
            raise VaultHTTPError(404, "not_found", f"Run {run_id!r} not found.")

        queue = subscribe(run_id)

        async def event_gen():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=0.6)
                        yield f"event: status\ndata: {data}\n\n"
                    except asyncio.TimeoutError:
                        # Send keep-alive comment
                        yield ": keep-alive\n\n"
                    run = get_run(run_id)
                    if run and run.state in ("done", "failed"):
                        # Send one final event then close
                        yield f"event: status\ndata: {run.model_dump_json()}\n\n"
                        break
            finally:
                unsubscribe(run_id, queue)

        return StreamingResponse(event_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── POST /runs/{id}/stop ─────────────────────────────────────────────────
    @app.post("/runs/{run_id}/stop")
    async def stop_run_endpoint(run_id: str) -> RunStatus:
        run = stop_run(run_id)
        if run is None:
            raise VaultHTTPError(404, "not_found", f"Run {run_id!r} not found.")
        return run

    return app
