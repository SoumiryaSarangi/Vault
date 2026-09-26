"""Register, heartbeat every 500 ms (via relay if needed), lease → fenced, re-register on DEAD, inventory push every 30 s. §4.5–4.6, §4.10. Task S4.
Owner: Soum.

Lease rule: the lease runs from when the request was SENT (never from when the reply arrived), so a
slow reply can only shorten it. Only RegisterReply and a non-DEAD HeartbeatReply grant a lease.
A DEAD reply means "re-register" (unknown/dead node, stale epoch, or metadata restarted: Jaiveer J4).

GET /v1/config (Jaiveer J4) = the VaultConfig fields flat + config_version + mode: pop those two, validate.
Re-fetched after every (re-)register and whenever a reply's config_version differs from ours.
"""
import asyncio
import logging
import time
from typing import Any, Optional

from pydantic import ValidationError

from vault.common.config import SafetyCfg, VaultConfig, node_addr, node_identity
from vault.common.models import (Heartbeat, HeartbeatReply, Inventory, NodeState, RegisterReply,
                                 RegisterRequest)
from vault.common.rpc import NetworkError, get_rpc

log = logging.getLogger("node.heartbeat")


def parse_config(body: dict[str, Any]) -> tuple[Optional[VaultConfig], SafetyCfg, int]:
    """GET /v1/config body → (full config or None, safety, config_version)."""
    body = dict(body)
    version = int(body.pop("config_version", 0))
    body.pop("mode", None)
    try:
        cfg = VaultConfig.model_validate(body)
        return cfg, cfg.safety, version
    except ValidationError:
        log.warning("config didn't validate as VaultConfig; applying only safety")
        return None, SafetyCfg.model_validate(body.get("safety", {})), version


class HeartbeatLoop:
    def __init__(self, node):
        self.node = node
        # Our own address, fixed at startup: node.cfg is replaced by metadata's config after registering,
        # and on another laptop (LAN mode) only VAULT_NODE_ADDR knows where we really are.
        self.addr = node_addr(node.cfg, node.pid)
        self.registered = False
        self._meta_ok: Optional[bool] = None       # for logging state changes only
        self._was_fenced: Optional[bool] = None
        self._inventory_due = 0.0

    # ── lease bookkeeping ──
    async def grant_lease(self, epoch: int, lease_ttl_ms: int, sent_at: float) -> None:
        await self.node.save_state(epoch, sent_at + lease_ttl_ms / 1000)

    def _log_fencing(self) -> None:
        fenced = self.node.fenced()
        if fenced != self._was_fenced:
            if fenced:
                log.warning("fenced: writes refused")
            else:
                log.info("lease valid: writes accepted")
            self._was_fenced = fenced

    def _log_meta(self, ok: bool, why: str = "") -> None:
        if ok != self._meta_ok:
            if ok:
                log.info("metadata reachable")
            else:
                log.warning("metadata unreachable: %s", why)
            self._meta_ok = ok

    # ── calls ──
    async def register(self) -> bool:
        n = self.node
        name, labels = node_identity(n.cfg, n.pid)
        req = RegisterRequest(node_id=n.pid, addr=self.addr, capacity_bytes=n.capacity,
                              display_name=name, labels=labels, discarded_on_startup=n.discarded)
        sent_at = time.time()
        try:
            r = await get_rpc().request("meta", "POST", "/v1/nodes/register", json=req.model_dump())
        except NetworkError as e:
            self._log_meta(False, str(e))
            return False
        if r.status_code != 200:
            # e.g. 404 from a relay node while metadata is down: no lease, try again next tick
            self._log_meta(False, f"register -> HTTP {r.status_code} {r.text[:120]}")
            return False
        self._log_meta(True)
        reply = RegisterReply.model_validate(r.json())
        await self.grant_lease(reply.epoch, reply.lease_ttl_ms, sent_at)
        n.discarded = 0                     # reported once
        self.registered = True
        log.info("registered: epoch %d, state %s", reply.epoch, reply.state.value)
        # Always re-read config after (re-)registering: a restarted metadata may restart config_version.
        await self.maybe_fetch_config(reply.config_version, force=True)
        await self.send_inventory()         # REJOINING → ALIVE happens when metadata sees the inventory
        return True

    async def maybe_fetch_config(self, version: int, force: bool = False) -> None:
        """Fetch when the version differs from ours (newer, or lower after a metadata restart)."""
        if not force and version == self.node.config_version:
            return
        try:
            r = await get_rpc().request("meta", "GET", "/v1/config")
        except NetworkError as e:
            log.info("config fetch failed: %s", e)
            return
        if r.status_code != 200:
            log.info("config fetch -> HTTP %d", r.status_code)
            return
        cfg, safety, v = parse_config(r.json())
        self.apply_config(cfg, safety, v)

    def apply_config(self, cfg: Optional[VaultConfig], safety: SafetyCfg, version: int) -> None:
        n = self.node
        if cfg is not None:
            n.cfg = cfg                     # tunables (scrub rate, intervals); cluster/nodes can't change
        n.safety = safety
        n.config_version = version
        if n.app_state is not None:
            n.app_state.config_version = version
        get_rpc().relay_enabled = safety.relay
        log.info("config v%d applied (safety: %s)", version,
                 ",".join(k for k, on in safety.model_dump().items() if not on) or "all on")

    async def send_inventory(self) -> None:
        n = self.node
        inv = Inventory(node_id=n.pid, epoch=n.epoch, fragments=n.storage.inventory(), sent_at=time.time())
        try:
            r = await get_rpc().request("meta", "POST", f"/v1/nodes/{n.pid}/inventory", json=inv.model_dump(),
                                        timeout=n.cfg.gateway.timeout_data_s)
            if r.status_code != 200:
                log.info("inventory -> HTTP %d", r.status_code)
        except NetworkError as e:
            log.info("inventory not delivered: %s", e)
        self._inventory_due = time.time() + n.cfg.inventory.interval_s

    def build_heartbeat(self) -> Heartbeat:
        n = self.node
        return Heartbeat(node_id=n.pid, epoch=n.epoch, disk_used=n.storage.disk_used(), capacity=n.capacity,
                         fragments=n.storage.count(), fenced=n.fenced(), reach=dict(n.reach), scrub=n.scrub)

    async def handle_reply(self, reply: HeartbeatReply, sent_at: float) -> None:
        if reply.state == NodeState.DEAD:
            # Never extend the lease here: metadata wants us to register again (maybe with a new epoch).
            log.info("heartbeat reply DEAD: re-registering")
            self.registered = False
            return
        await self.grant_lease(reply.epoch, reply.lease_ttl_ms, sent_at)
        await self.maybe_fetch_config(reply.config_version)

    async def beat(self) -> None:
        n = self.node
        hb = self.build_heartbeat()
        sent_at = time.time()
        try:
            r = await get_rpc().request("meta", "POST", f"/v1/nodes/{n.pid}/heartbeat", json=hb.model_dump())
        except NetworkError as e:
            self._log_meta(False, str(e))
            return
        if r.status_code != 200:
            self._log_meta(False, f"heartbeat -> HTTP {r.status_code}")
            return
        self._log_meta(True)
        await self.handle_reply(HeartbeatReply.model_validate(r.json()), sent_at)

    # ── loop ──
    async def run(self) -> None:
        n = self.node
        interval = n.cfg.detector.heartbeat_ms / 1000
        # Stagger the periodic inventory push across nodes (§4.10): n1 at 0 s, n2 at 5 s, … of a 30 s period.
        digits = "".join(ch for ch in n.pid if ch.isdigit()) or "0"
        stagger = (int(digits) % 6) * n.cfg.inventory.interval_s / 6
        self._inventory_due = time.time() + stagger
        while True:
            started = time.monotonic()
            try:
                if not self.registered:
                    await self.register()
                else:
                    await self.beat()
                    if not self.registered:          # reply said DEAD → register right away
                        await self.register()
                    elif time.time() >= self._inventory_due:
                        n.spawn(self.send_inventory())
                        self._inventory_due = time.time() + n.cfg.inventory.interval_s
            except asyncio.CancelledError:
                raise
            except Exception:                         # never let the heartbeat die
                log.exception("heartbeat loop error")
            self._log_fencing()
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))
