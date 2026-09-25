"""Cluster operations: reset, add machine, seed (ARCHITECTURE §6, §7.4). Owner: Anushka. Task A3.

reset   kill metadata + gateway + nodes (never the oracle: its runs call reset), forget added machines
        and chaos, wipe data/, start everything, wait for health, create default buckets, set the mode,
        wait for nodes to be ALIVE, seed via Urooz's urooz_seed.seed(). Target ≤ 20 s (PRD F16).
add     spawn n7, n8, … with a display name and labels (passed via env, see config.node_identity).
seed    urooz_seed.seed(rpc, bucket, count).

Everything that depends on a teammate's code degrades gracefully: a missing endpoint or module is
logged and skipped, so reset still gives a clean, running cluster.
"""
import asyncio
import logging
import shutil
import time
from pathlib import Path

from vault.common.config import VaultConfig
from vault.common.models import (AddNodeRequest, BucketCreate, ExternalEvent, ModeRequest, Proc, ResetRequest,
                                 ResetResult, SeedRequest, SeedResult)
from vault.common.rpc import NetworkError, get_rpc
from vault.common.service import VaultHTTPError
from vault.supervisor.anushka_chaos_ctl import ChaosController

log = logging.getLogger("sup.cluster")


def _rmtree_retry(path: Path, attempts: int = 15) -> None:
    """Windows can hold a file lock for a moment after a process is killed."""
    for i in range(attempts):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(0.2)


class ClusterOps:
    def __init__(self, cfg: VaultConfig, procs, chaos: ChaosController):
        self.cfg, self.procs, self.chaos = cfg, procs, chaos
        self._reset_lock = asyncio.Lock()

    async def _meta(self, method: str, path: str, body=None, timeout: float = 2.0):
        """Best-effort call to metadata; returns the response or None (down / not built yet)."""
        try:
            r = await get_rpc().request("meta", method, path, json=body, allow_relay=False, timeout=timeout)
            if r.status_code in (404, 405, 501):
                log.warning("metadata %s %s -> %s (not built yet?)", method, path, r.status_code)
                return None
            return r
        except NetworkError as e:
            log.warning("metadata unreachable for %s: %s", path, e.reason)
            return None

    async def _wait_nodes_alive(self, want: int, timeout: float) -> int:
        end, alive = time.time() + timeout, 0
        while time.time() < end:
            r = await self._meta("GET", "/v1/cluster")
            if r is None:
                return -1          # no snapshot endpoint yet: nothing to wait for
            if r.status_code == 200:
                alive = sum(1 for n in r.json().get("nodes", []) if n.get("state") == "ALIVE")
                if alive >= want:
                    return alive
            await asyncio.sleep(0.3)
        return alive

    # ── seed ──
    async def seed(self, req: SeedRequest) -> SeedResult:
        try:
            from vault.supervisor.urooz_seed import seed
        except ImportError:
            raise VaultHTTPError(501, "seed_not_ready", "Demo seeding isn't built yet (Urooz, task U2).")
        try:
            n = await seed(get_rpc(), bucket=req.bucket, count=req.count)
        except NotImplementedError:
            raise VaultHTTPError(501, "seed_not_ready", "Demo seeding isn't built yet (Urooz, task U2).")
        return SeedResult(uploaded=n)

    # ── add machine ──
    async def add_node(self, req: AddNodeRequest) -> Proc:
        node_id = self.procs.add_node(req.display_name, req.labels)
        get_rpc().set_addr(node_id, self.cfg.addr(node_id))
        view = await self.procs.start(node_id)
        await self.chaos.report("node_added", node_id, "chaos.add_node", f"You added {req.display_name}.",
                                f"spawn {node_id} port {view.port} labels={req.labels}", {"node": node_id},
                                {"labels": req.labels})
        return view

    # ── reset ──
    async def reset(self, req: ResetRequest) -> ResetResult:
        if self._reset_lock.locked():
            raise VaultHTTPError(409, "reset_running", "A reset is already running.")
        async with self._reset_lock:
            t0 = time.time()
            self.chaos.forget_all()
            cluster = ["meta", "gw", *self.procs.node_ids()]
            await self.procs.kill_many(cluster)
            self.procs.drop_added_nodes()
            await asyncio.to_thread(_rmtree_retry, Path(self.cfg.cluster.data_dir))
            await self.procs.start_many(["meta", "gw", *self.procs.node_ids()])

            if not await self.chaos.wait_healthy("meta") or not await self.chaos.wait_healthy("gw"):
                raise VaultHTTPError(503, "boot_failed", "Vault didn't come back up. Check logs/meta.log and logs/gw.log.")
            for name, policy in self.cfg.cluster.default_buckets.items():
                r = await self._meta("POST", "/v1/buckets", BucketCreate(name=name, policy=policy).model_dump())
                if r is not None and r.status_code >= 400 and r.status_code != 409:
                    log.warning("create bucket %s -> %s %s", name, r.status_code, r.text[:200])
            await self._meta("POST", "/v1/mode", ModeRequest(mode=req.mode).model_dump())
            nodes = len(self.procs.node_ids())
            alive = await self._wait_nodes_alive(nodes, timeout=12)

            files = 0
            if req.seed:
                # ALIVE comes with the first heartbeat; give every node a couple more beats to hold its lease,
                # or early writes fall back to spare targets and land on machines that share a switch/disk batch.
                await asyncio.sleep(1.5)
                try:
                    files = (await self.seed(SeedRequest())).uploaded
                except VaultHTTPError as e:
                    log.warning("reset: seeding skipped: %s", e.body.message)
            elapsed = round(time.time() - t0, 1)
            human = f"Demo reset: {nodes} machines, {files} files."
            await self._meta("POST", "/v1/events", ExternalEvent(
                type="chaos.reset", human=human, technical=f"reset in {elapsed}s; alive={alive}; mode={req.mode}",
                data={"elapsed_s": elapsed, "files": files}).model_dump(mode="json"))
            log.info("%s (%.1f s)", human, elapsed)
            return ResetResult(ok=True, elapsed_s=elapsed)
