"""Storage node, :7101+ (ARCHITECTURE §7.3). Owner: Soum. Tasks S3, S4, S6.

STUB: /v1/ping + /_vault/health + /_chaos (from make_app) so `python -m vault up` boots.
"""
import time

from fastapi import FastAPI

from vault.common.config import VaultConfig
from vault.common.models import Ping
from vault.common.service import make_app


def create_app(cfg: VaultConfig, pid: str) -> FastAPI:
    app = make_app(cfg, pid)

    @app.get("/v1/ping")
    async def ping() -> Ping:
        return Ping(pid=pid, epoch=0, ts=time.time())

    # TODO S3: fragment PUT/GET/HEAD/DELETE, /v1/health; S4: heartbeat, pinger, pull, scrubber; S6: relay, node chaos
    return app
