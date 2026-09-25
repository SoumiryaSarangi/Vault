"""Metadata service + health brain host, :7000 (ARCHITECTURE §7.2). Owner: Jaiveer. Tasks J4–J8.

Builds the BrainContext (vault.common.brain_api) and starts every brain loop in the lifespan:
Jaiveer's detector/scheduler/repair/auditor/metrics and Soum's reconciler/gc/rebalancer `run(ctx)`.
Registers events.set_sink(...) and events.set_name_resolver(...).

STUB: only /_vault/health works (from make_app) so `python -m vault up` boots.
"""
from fastapi import FastAPI

from vault.common.config import VaultConfig
from vault.common.service import make_app


def create_app(cfg: VaultConfig, pid: str = "meta") -> FastAPI:
    app = make_app(cfg, pid)
    # TODO J4: open DB, register routers (uploads, objects, cluster, stream, reports, nodes), start brain loops
    return app
