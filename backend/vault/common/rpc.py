"""The ONE way services talk to each other (ARCHITECTURE §4.7, §11). Owner: Anushka (shared).

Never create another httpx client (TECH_STACK §5). Use:

    rpc = init_rpc(pid, cfg, netsim)          # once, inside the FastAPI lifespan
    r = await get_rpc().request("n3", "GET", "/v1/ping")
    r = await get_rpc().request("n3", "PUT", f"/v1/fragments/{fid}", content=blob,
                                headers={...}, timeout=cfg.gateway.timeout_data_s)

Behaviour:
  * Adds X-Vault-From: <pid> and X-Vault-Hop: 0 to every request.
  * Client-side netsim: a blocked target raises NetworkError at once; a frozen process waits.
  * 599 responses, connect errors and timeouts all raise NetworkError (callers can't tell them apart).
  * Other HTTP statuses (404, 409, 503, …) are returned as normal responses: check r.status_code.
  * Routing: direct first; on NetworkError (or a link known to be bad) try ONE relay node r that
    is not self/target, not DOWN/DEAD, with self→r and r→target not known-bad:
        METHOD http://r/v1/relay/{target}{path}   with X-Vault-Hop: 1
    The self→target link is then marked bad for 3 s. With safety.relay off, relays are skipped.
  * The response of a relayed call has r.extensions["vault_route"] == "relay:<r>" (else "direct").

Relay endpoint implementers (node relay.py) call rpc.forward(...), which rewrites headers so the
target's netsim judges the relay→target link:
    X-Vault-From: <relay>, X-Vault-Origin: <original sender>, X-Vault-Via: <relay>, X-Vault-Hop: 1
"""
import asyncio
import time
from typing import Any, Iterable, Optional

import httpx

from vault.common.config import VaultConfig
from vault.common.netsim import NetSim

BAD_LINK_TTL_S = 3.0
_NET_EXC = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
            httpx.PoolTimeout, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError)


class NetworkError(Exception):
    def __init__(self, target: str, reason: str):
        super().__init__(f"{target}: {reason}")
        self.target = target
        self.reason = reason


class Rpc:
    def __init__(self, pid: str, cfg: VaultConfig, netsim: NetSim):
        self.pid = pid
        self.cfg = cfg
        self.ns = netsim
        self.relay_enabled = cfg.safety.relay
        self.client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            timeout=httpx.Timeout(cfg.gateway.timeout_control_s),
        )
        self._addr_override: dict[str, str] = {}
        # Topology (refreshed from the metadata snapshot or local pings):
        self._bad_links: set[tuple[str, str]] = set()        # directed (a, b) known blocked
        self._bad_until: dict[str, float] = {}               # self→target marked bad locally
        self._node_ids: list[str] = list(cfg.node_ids())
        self._unavailable: set[str] = set()                   # nodes DOWN/DEAD/RETIRED

    # ── addresses ──
    def set_addr(self, pid: str, addr: str) -> None:
        """Register an address for a participant not in vault.yaml (e.g. an added machine n7)."""
        self._addr_override[pid] = addr
        if pid.startswith("n") and pid not in self._node_ids:
            self._node_ids.append(pid)

    def addr(self, pid: str) -> str:
        return self._addr_override.get(pid) or self.cfg.addr(pid)

    # ── topology ──
    def update_topology(self, nodes: Iterable[tuple[str, str]], links: Iterable[tuple[str, str, bool, bool]]) -> None:
        """nodes: (id, state). links: (a, b, a_to_b_ok, b_to_a_ok) for abnormal links only (Snapshot.links)."""
        ids, unavailable = [], set()
        for node_id, state in nodes:
            ids.append(node_id)
            if state in ("DOWN", "DEAD", "RETIRED"):
                unavailable.add(node_id)
        if ids:
            self._node_ids = ids
        self._unavailable = unavailable
        bad = set()
        for a, b, a_to_b, b_to_a in links:
            if not a_to_b:
                bad.add((a, b))
            if not b_to_a:
                bad.add((b, a))
        self._bad_links = bad

    def mark_bad(self, target: str) -> None:
        self._bad_until[target] = time.time() + BAD_LINK_TTL_S

    def link_ok(self, a: str, b: str) -> bool:
        if (a, b) in self._bad_links:
            return False
        if a == self.pid and self._bad_until.get(b, 0) > time.time():
            return False
        return True

    def relay_candidates(self, target: str) -> list[str]:
        return [r for r in self._node_ids
                if r not in (self.pid, target) and r not in self._unavailable
                and self.link_ok(self.pid, r) and self.link_ok(r, target)]

    # ── requests ──
    def _headers(self, extra: Optional[dict[str, str]], hop: int) -> dict[str, str]:
        h = {"X-Vault-From": self.pid, "X-Vault-Hop": str(hop)}
        if extra:
            h.update(extra)
        return h

    async def _send(self, target: str, url: str, method: str, headers: dict[str, str], timeout: Optional[float],
                    **kw: Any) -> httpx.Response:
        wait = self.ns.frozen_for()
        if wait > 0:
            await asyncio.sleep(wait)
        if self.ns.is_blocked_out(target):
            raise NetworkError(target, "netsim blocked_out")
        try:
            r = await self.client.request(method, url, headers=headers,
                                          timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT, **kw)
        except _NET_EXC as e:
            raise NetworkError(target, type(e).__name__) from e
        if r.status_code == 599:
            raise NetworkError(target, "netsim blocked_in")
        return r

    async def request(self, target: str, method: str, path: str, *, json: Any = None, content: Optional[bytes] = None,
                      params: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None,
                      timeout: Optional[float] = None, allow_relay: bool = True) -> httpx.Response:
        kw: dict[str, Any] = {}
        if json is not None:
            kw["json"] = json
        if content is not None:
            kw["content"] = content
        if params is not None:
            kw["params"] = params
        async def direct() -> httpx.Response:
            r = await self._send(target, f"http://{self.addr(target)}{path}", method,
                                 self._headers(headers, 0), timeout, **kw)
            r.extensions["vault_route"] = "direct"
            return r

        relay_ok = allow_relay and self.relay_enabled and target.startswith(("n", "meta", "gw"))
        direct_err: Optional[NetworkError] = None
        direct_tried = False
        if self.link_ok(self.pid, target) or not relay_ok:
            direct_tried = True
            try:
                return await direct()
            except NetworkError as e:
                direct_err = e
                self.mark_bad(target)
        if relay_ok:
            for relay in self.relay_candidates(target):
                try:
                    r = await self._send(relay, f"http://{self.addr(relay)}/v1/relay/{target}{path}", method,
                                         self._headers(headers, 1), timeout, **kw)
                    r.extensions["vault_route"] = f"relay:{relay}"
                    return r
                except NetworkError:
                    continue
        # A "bad link" mark is only a hint (one slow ping under load sets it). If no relay could help,
        # still try the direct route instead of failing every request to that target for 3 s.
        if not direct_tried:
            try:
                return await direct()
            except NetworkError as e:
                direct_err = e
        raise direct_err or NetworkError(target, "no relay available")

    async def forward(self, target: str, method: str, path: str, *, origin: str, content: bytes,
                      headers: dict[str, str], params: Optional[dict[str, Any]] = None,
                      timeout: Optional[float] = None) -> httpx.Response:
        """Used by the relay endpoint: forward once, directly, never relaying again."""
        h = {k: v for k, v in headers.items()
             if k.lower() not in ("host", "content-length", "x-vault-from", "x-vault-hop", "connection")}
        h.update({"X-Vault-From": self.pid, "X-Vault-Origin": origin, "X-Vault-Via": self.pid, "X-Vault-Hop": "1"})
        return await self._send(target, f"http://{self.addr(target)}{path}", method, h, timeout,
                                content=content, params=params)

    async def get_json(self, target: str, path: str, **kw: Any) -> Any:
        r = await self.request(target, "GET", path, **kw)
        r.raise_for_status()
        return r.json()

    async def post_json(self, target: str, path: str, body: Any = None, **kw: Any) -> httpx.Response:
        return await self.request(target, "POST", path, json=body, **kw)

    async def close(self) -> None:
        await self.client.aclose()


_RPC: Optional[Rpc] = None


def init_rpc(pid: str, cfg: VaultConfig, netsim: NetSim) -> Rpc:
    global _RPC
    _RPC = Rpc(pid, cfg, netsim)
    return _RPC


def get_rpc() -> Rpc:
    assert _RPC is not None, "call init_rpc(pid, cfg, netsim) first (in the FastAPI lifespan)"
    return _RPC
