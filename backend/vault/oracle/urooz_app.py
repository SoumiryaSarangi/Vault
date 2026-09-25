"""Durability Oracle, :7090 (ARCHITECTURE §5, §7.5). Owner: Urooz. Tasks U1, U3, U5.

Talks ONLY to the gateway (client traffic), metadata (/v1/inspect/objects, /v1/mode, /v1/events)
and the supervisor (reset, chaos script). Never writes to Vault's DB. Never killed by chaos.

STUB: only /_vault/health works (from make_app) so `python -m vault up` boots.
"""
from fastapi import FastAPI

from vault.common.config import VaultConfig
from vault.common.service import make_app


def create_app(cfg: VaultConfig, pid: str = "oracle") -> FastAPI:
    app = make_app(cfg, pid)
    # TODO U5: POST /runs, GET /runs, GET /runs/{id}, GET /runs/{id}/stream, POST /runs/{id}/stop
    return app
