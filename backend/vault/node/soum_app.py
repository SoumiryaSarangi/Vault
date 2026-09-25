"""Storage node, :7101+ (ARCHITECTURE §7.3). Owner: Soum. Tasks S3, S4, S6.

S3: fragment PUT/GET/HEAD/DELETE with epoch + lease fencing, /v1/ping, /v1/health, /v1/scrub.
S4 adds heartbeat/pinger/pull/scrub loop, S6 relay + node chaos; they reach the node through app.state.node.

Fencing (§4.6, decided by Anushka): fenced = safety.fencing and (no lease yet or lease expired).
A fenced node refuses PUT/DELETE (503 fenced) but still serves verified GETs.
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Response

from vault.common.config import SafetyCfg, VaultConfig
from vault.common.hashing import sha256_hex
from vault.common.models import FragmentPutResult, FragmentReport, NodeHealth, Ping, ScrubStatus
from vault.common.rpc import NetworkError, get_rpc
from vault.common.service import VaultHTTPError, make_app
from vault.node.soum_storage import Corrupt, NotFound, Storage, decode_meta, encode_meta

log = logging.getLogger("node")


class Node:
    """Per-process node state shared by the routes and the background loops."""

    def __init__(self, cfg: VaultConfig, pid: str):
        self.cfg = cfg
        self.pid = pid
        self.safety: SafetyCfg = cfg.safety
        self.storage = Storage(cfg.data_path(pid), pid, safety=lambda: self.safety)
        self.capacity = cfg.cluster.node_capacity_bytes
        self.epoch = 0
        self.lease_expiry: Optional[float] = None
        self.discarded = 0              # tmp files removed at startup → RegisterRequest.discarded_on_startup
        self.disk_full = False          # /_chaos/disk_full (S6)
        self.scrub = ScrubStatus()
        self._scrub_task: Optional[asyncio.Task] = None
        self._bg: set[asyncio.Task] = set()

    async def startup(self) -> None:
        self.discarded = await self.storage.startup()
        saved = await self.storage.read_node_json()
        self.epoch = int(saved.get("epoch") or 0)
        lease = saved.get("lease_expiry")
        self.lease_expiry = float(lease) if lease is not None else None
        log.info("startup: %d fragments, discarded %d tmp, epoch %d", self.storage.count(), self.discarded, self.epoch)

    async def close(self) -> None:
        for t in list(self._bg):
            t.cancel()
        await asyncio.gather(*self._bg, return_exceptions=True)

    # ── epoch, lease, fencing (§4.6) ──
    def fenced(self) -> bool:
        return self.safety.fencing and (self.lease_expiry is None or time.time() > self.lease_expiry)

    async def save_state(self, epoch: int, lease_expiry: Optional[float]) -> None:
        """Adopt an epoch/lease and persist it in node.json (called by the heartbeat loop, S4)."""
        self.epoch, self.lease_expiry = epoch, lease_expiry
        await self.storage.write_node_json({"epoch": epoch, "lease_expiry": lease_expiry})

    def check_write(self, epoch_header: Optional[str]) -> None:
        if not self.safety.fencing:
            return
        if self.fenced():
            raise VaultHTTPError(503, "fenced", "This machine lost contact with Vault and isn't accepting writes.",
                                 {"lease_expiry": self.lease_expiry})
        if epoch_header is None or not epoch_header.strip().isdigit() or int(epoch_header) != self.epoch:
            raise VaultHTTPError(409, "stale_epoch", "The write was meant for an older generation of this machine.",
                                 {"expected": self.epoch, "got": epoch_header})

    # ── background work ──
    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    def report(self, fid: str, problem: str, context: str) -> None:
        """Tell metadata about a bad fragment (read repair / scrub), in the background."""
        body = FragmentReport(fid=fid, node_id=self.pid, problem=problem, observed_by=self.pid, context=context)
        self.spawn(self._report(body))

    async def _report(self, body: FragmentReport) -> None:
        try:
            r = await get_rpc().request("meta", "POST", "/v1/reports/fragment", json=body.model_dump())
            if r.status_code >= 300:
                log.info("report %s %s -> HTTP %d", body.problem, body.fid, r.status_code)
        except NetworkError as e:
            log.info("report %s %s not delivered: %s", body.problem, body.fid, e)

    def trigger_scrub(self) -> None:
        if self._scrub_task is None or self._scrub_task.done():
            self._scrub_task = self.spawn(self.scrub_pass())

    async def scrub_pass(self) -> None:
        """One full verification pass, recently written files first (§4.9). S4 adds pacing and the loop."""
        self.scrub.running, self.scrub.scanned, self.scrub.corrupt_found = True, 0, 0
        try:
            for fid in self.storage.scrub_order(self.cfg.scrub.recent_first_minutes * 60):
                try:
                    ok = await self.storage.verify(fid)
                except NotFound:
                    continue
                self.scrub.scanned += 1
                if not ok:
                    self.scrub.corrupt_found += 1
                    log.warning("scrub: %s corrupt, quarantined", fid)
                    self.report(fid, "corrupt", "scrub")
            self.scrub.last_pass_at = time.time()
        finally:
            self.scrub.running = False

    def health(self) -> NodeHealth:
        return NodeHealth(pid=self.pid, epoch=self.epoch, fenced=self.fenced(), lease_expiry=self.lease_expiry,
                          disk_used=self.storage.disk_used(), capacity=self.capacity,
                          fragments=self.storage.count(), scrub=self.scrub)


def create_app(cfg: VaultConfig, pid: str) -> FastAPI:
    node = Node(cfg, pid)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await node.startup()
        try:
            yield
        finally:
            await node.close()

    app = make_app(cfg, pid, lifespan)
    app.state.node = node
    st = node.storage

    @app.put("/v1/fragments/{fid}", status_code=201)
    async def put_fragment(fid: str, request: Request) -> FragmentPutResult:
        try:
            header = decode_meta(request.headers["x-vault-meta"])
        except (KeyError, ValueError):
            raise VaultHTTPError(400, "bad_request", "X-Vault-Meta is missing or unreadable.") from None
        if header.fid != fid:
            raise VaultHTTPError(400, "bad_request", "X-Vault-Meta is for a different fragment.",
                                 {"path": fid, "meta": header.fid})
        node.check_write(request.headers.get("x-vault-epoch"))
        payload = await request.body()
        sha = sha256_hex(payload)
        if node.safety.verify_on_receive:
            claimed = request.headers.get("x-vault-sha256")
            if sha != header.frag_sha256 or (claimed is not None and claimed != sha):
                raise VaultHTTPError(400, "checksum_mismatch", "The copy arrived damaged.",
                                     {"expected": header.frag_sha256, "got": sha})
        if node.disk_full or st.disk_used() + len(payload) > node.capacity:
            raise VaultHTTPError(507, "disk_full", "This machine's disk is full.",
                                 {"disk_used": st.disk_used(), "capacity": node.capacity})
        await st.put(header, payload)
        return FragmentPutResult(fid=fid, sha256=sha, size=len(payload), durable=node.safety.durable_writes)

    @app.get("/v1/fragments/{fid}")
    async def get_fragment(fid: str) -> Response:
        try:
            header, payload = await st.get(fid)
        except NotFound:
            raise VaultHTTPError(404, "not_found", "No such fragment on this machine.") from None
        except Corrupt as e:
            log.warning("read: %s corrupt (%s), quarantined", fid, e.reason)
            node.report(fid, "corrupt", "read")
            raise VaultHTTPError(409, "corrupt", "This copy is damaged and has been set aside.") from None
        return Response(payload, media_type="application/octet-stream",
                        headers={"X-Vault-Sha256": header.frag_sha256, "X-Vault-Meta": encode_meta(header)})

    @app.head("/v1/fragments/{fid}")
    async def head_fragment(fid: str) -> Response:
        e = st.entry(fid)
        if e is None:
            return Response(status_code=404)
        return Response(status_code=200, headers={"X-Vault-Sha256": e.header.frag_sha256,
                                                  "X-Vault-Meta": encode_meta(e.header)})

    @app.delete("/v1/fragments/{fid}", status_code=204)
    async def delete_fragment(fid: str, request: Request) -> Response:
        node.check_write(request.headers.get("x-vault-epoch"))
        await st.delete(fid)        # deleting an absent fragment is a no-op (trims are idempotent)
        return Response(status_code=204)

    @app.get("/v1/ping")
    async def ping() -> Ping:
        return Ping(pid=pid, epoch=node.epoch, ts=time.time())

    @app.get("/v1/health")
    async def health() -> NodeHealth:
        return node.health()

    @app.post("/v1/scrub", status_code=202)
    async def scrub_now() -> dict:
        node.trigger_scrub()
        return {}

    @app.get("/v1/scrub")
    async def scrub_status() -> ScrubStatus:
        return node.scrub

    # TODO S4: heartbeat, pinger, pull, scrub loop; S6: relay, node chaos
    return app
