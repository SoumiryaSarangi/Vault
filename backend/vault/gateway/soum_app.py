"""Gateway: public object API, :7080 (ARCHITECTURE §4.1–4.3, §7.1). Owner: Soum. Tasks S5, S7.

STUB: only /_vault/health works (from make_app) so `python -m vault up` boots.
"""
from fastapi import FastAPI

from vault.common.config import VaultConfig
from vault.common.service import make_app


def create_app(cfg: VaultConfig, pid: str = "gw") -> FastAPI:
    app = make_app(cfg, pid)
    # TODO S5: PUT/GET/HEAD/DELETE /{bucket}/{key}, PUT/GET /{bucket}; S7: routing + stats report loop
    return app
