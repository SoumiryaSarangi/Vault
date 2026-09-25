"""Application-level network simulation (ARCHITECTURE §4.7, D7). Owner: Anushka (shared).

One NetSim per Python process. Server side is an ASGI middleware; client side is enforced in rpc.py.

    Client side (rpc.py):  target ∈ blocked_out → NetworkError immediately; frozen → wait until thawed
    Server middleware:     X-Vault-From ∈ blocked_in → 599 + X-Vault-Netsim: blocked
                           sleep(delay_ms); frozen → hang until frozen_until (callers time out)

Requests without X-Vault-From (the browser dashboard) and every /_chaos path are never touched,
so the supervisor can always clear faults.

Usage in a service's create_app():
    ns = init_netsim(pid)
    install_netsim(app, ns, enabled=cfg.chaos.enabled)
Nodes add their own /_chaos/corrupt and /_chaos/disk_full routes and register
ns.on_clear(...) and ns.add_state(...) so /_chaos/clear and GET /_chaos include them.
"""
import asyncio
import time
from typing import Any, Callable, Optional

from fastapi import APIRouter, FastAPI

from vault.common.models import BlockRequest, ChaosState, FreezeRequest, SlowRequest, UnblockRequest


class NetSim:
    def __init__(self, pid: str):
        self.pid = pid
        self.blocked_out: set[str] = set()
        self.blocked_in: set[str] = set()
        self.delay_ms: int = 0
        self.frozen_until: float = 0.0
        self._clear_hooks: list[Callable[[], Any]] = []
        self._state_hooks: list[Callable[[], dict[str, Any]]] = []

    # ── mutations ──
    def block(self, peers: list[str], direction: str = "both") -> None:
        if direction in ("out", "both"):
            self.blocked_out.update(peers)
        if direction in ("in", "both"):
            self.blocked_in.update(peers)

    def unblock(self, peers: Optional[list[str]] = None) -> None:
        if peers is None:
            self.blocked_out.clear()
            self.blocked_in.clear()
        else:
            self.blocked_out.difference_update(peers)
            self.blocked_in.difference_update(peers)

    def slow(self, ms: int) -> None:
        self.delay_ms = max(0, int(ms))

    def freeze(self, seconds: float) -> None:
        self.frozen_until = time.time() + max(0.0, seconds)

    def clear(self) -> None:
        self.unblock(None)
        self.delay_ms = 0
        self.frozen_until = 0.0
        for hook in self._clear_hooks:
            hook()

    # ── hooks for node-specific faults ──
    def on_clear(self, fn: Callable[[], Any]) -> None:
        self._clear_hooks.append(fn)

    def add_state(self, fn: Callable[[], dict[str, Any]]) -> None:
        self._state_hooks.append(fn)

    # ── queries ──
    def is_blocked_out(self, target: str) -> bool:
        return target in self.blocked_out

    def frozen_for(self) -> float:
        return max(0.0, self.frozen_until - time.time())

    def state(self) -> ChaosState:
        extra: dict[str, Any] = {}
        for fn in self._state_hooks:
            extra.update(fn())
        return ChaosState(pid=self.pid, blocked_out=sorted(self.blocked_out), blocked_in=sorted(self.blocked_in),
                          delay_ms=self.delay_ms, frozen_until=self.frozen_until, node_faults=extra)


class NetSimMiddleware:
    """Pure ASGI middleware (safe with streaming and SSE responses)."""

    def __init__(self, app, netsim: NetSim):
        self.app = app
        self.ns = netsim

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith("/_chaos"):
            return await self.app(scope, receive, send)
        frm = ""
        for k, v in scope.get("headers", []):
            if k == b"x-vault-from":
                frm = v.decode()
                break
        if frm:
            if frm in self.ns.blocked_in:
                await send({"type": "http.response.start", "status": 599,
                            "headers": [(b"content-type", b"application/json"), (b"x-vault-netsim", b"blocked")]})
                await send({"type": "http.response.body",
                            "body": b'{"error":"netsim_blocked","message":"link blocked by chaos"}'})
                return
            if self.ns.delay_ms:
                await asyncio.sleep(self.ns.delay_ms / 1000)
            wait = self.ns.frozen_for()
            if wait > 0:
                await asyncio.sleep(wait)
        return await self.app(scope, receive, send)


def chaos_router(ns: NetSim) -> APIRouter:
    r = APIRouter(prefix="/_chaos", tags=["chaos"])

    @r.get("")
    async def get_state() -> ChaosState:
        return ns.state()

    @r.post("/block")
    async def block(req: BlockRequest) -> ChaosState:
        ns.block(req.peers, req.direction)
        return ns.state()

    @r.post("/unblock")
    async def unblock(req: UnblockRequest) -> ChaosState:
        ns.unblock(req.peers)
        return ns.state()

    @r.post("/slow")
    async def slow(req: SlowRequest) -> ChaosState:
        ns.slow(req.ms)
        return ns.state()

    @r.post("/freeze")
    async def freeze(req: FreezeRequest) -> ChaosState:
        ns.freeze(req.seconds)
        return ns.state()

    @r.post("/clear")
    async def clear() -> ChaosState:
        ns.clear()
        return ns.state()

    return r


_NETSIM: Optional[NetSim] = None


def init_netsim(pid: str) -> NetSim:
    global _NETSIM
    _NETSIM = NetSim(pid)
    return _NETSIM


def get_netsim() -> NetSim:
    assert _NETSIM is not None, "call init_netsim(pid) first"
    return _NETSIM


def install_netsim(app: FastAPI, ns: NetSim, enabled: bool = True) -> None:
    """Add the middleware and the /_chaos router. With chaos disabled, nothing is installed."""
    if not enabled:
        return
    app.add_middleware(NetSimMiddleware, netsim=ns)
    app.include_router(chaos_router(ns))
