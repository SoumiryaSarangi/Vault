"""Cluster operations: reset, add machine, seed (ARCHITECTURE §6, §7.4). Owner: Anushka. Task A3.

reset   kill metadata + gateway + nodes (never the oracle: its runs call reset), forget added machines
        and chaos, wipe data/, start everything, wait for health, create default buckets, set the mode,
        wait for nodes to be ALIVE, seed via Urooz's urooz_seed.seed(). Target ≤ 20 s (PRD F16).
add     spawn n7, n8, … with a display name and labels (passed via env, see config.node_identity).
seed    urooz_seed.seed(rpc, bucket, count).
join    LAN mode: a laptop's node agent registers its machine (repeated every few seconds; idempotent).
rename  change a machine's display name in metadata and here; re-applied after every reset.
(join, rename and info added by Soum for LAN mode, with Anushka's OK; docs/soum_lan_demo.md)

Everything that depends on a teammate's code degrades gracefully: a missing endpoint or module is
logged and skipped, so reset still gives a clean, running cluster.
"""
import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path

from vault.common.config import CONTROL_DISPLAY_NAMES, ENV_HUB, VaultConfig
from vault.common.models import (AddNodeRequest, BucketCreate, ClusterInfo, ExternalEvent, JoinRequest, JoinResult,
                                 LabelsPatch, ModeRequest, Proc, ResetRequest, ResetResult, SeedRequest, SeedResult)
from vault.common.rpc import NetworkError, get_rpc
from vault.common.service import VaultHTTPError
from vault.supervisor.anushka_chaos_ctl import ChaosController
from vault.supervisor.anushka_procs import agent_target

log = logging.getLogger("sup.cluster")
NODE_ID = re.compile(r"n[1-9]\d{0,2}")
MAX_NAME = 40


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
        self.renames: dict[str, str] = {}      # node id → name given in the dashboard (survives reset)

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
        """Wait until `want` nodes are ALIVE **and hold a lease** (not fenced), so the first writes don't bounce."""
        end, ready = time.time() + timeout, 0
        while time.time() < end:
            r = await self._meta("GET", "/v1/cluster")
            if r is None:
                return -1          # no snapshot endpoint: nothing to wait for
            if r.status_code == 200:
                nodes = r.json().get("nodes", [])
                ready = sum(1 for n in nodes if n.get("state") == "ALIVE" and not n.get("fenced"))
                if ready >= want:
                    return ready
            await asyncio.sleep(0.3)
        return ready

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

    # ── LAN mode: a machine on another laptop joins ──
    async def join(self, req: JoinRequest) -> JoinResult:
        node_id = req.node_id or self.procs.next_node_id()
        if not NODE_ID.fullmatch(node_id):
            raise VaultHTTPError(400, "bad_id", f"{node_id!r} isn't a machine id. Use n2, n3, …")
        existing = self.procs.children.get(node_id)
        if existing is not None and not existing.remote:
            raise VaultHTTPError(409, "id_taken", f"{node_id} already runs on the hub ({existing.display_name}). "
                                                  f"Pick another id, e.g. {self.procs.next_node_id()}.")
        port = self.cfg.port(node_id)
        rpc = get_rpc()
        if existing is not None and existing.host != req.host and await self._agent_answers(node_id):
            # Two laptops asked for the same id: the first one keeps it (a real IP change leaves nobody
            # answering at the old address).
            raise VaultHTTPError(409, "id_taken", f"{node_id} is already the laptop at {existing.host}. "
                                                  f"Use --id {self.procs.next_node_id()} on this one.")
        # Can we reach the agent back? (Windows Firewall on the joining laptop is the usual problem.)
        rpc.set_addr(agent_target(node_id), f"{req.host}:{req.agent_port}")
        try:
            r = await rpc.request(agent_target(node_id), "GET", "/agent/status", allow_relay=False, timeout=3.0)
            r.raise_for_status()
        except Exception:
            raise VaultHTTPError(502, "cant_reach_laptop",
                                 f"The hub can't reach {req.host}. On that laptop, allow TCP ports "
                                 f"{req.agent_port} and {port} in Windows Firewall (docs/soum_lan_demo.md).")
        new = existing is None
        moved = not new and existing.host != req.host
        name = self.renames.get(node_id) or (existing.display_name if existing else req.display_name)
        c = self.procs.add_remote(node_id, name, req.labels, req.host)
        rpc.set_addr(node_id, f"{req.host}:{port}")
        await self.procs.poll_one(c)
        if new:
            await self.chaos.report("node_added", node_id, "chaos.add_node", f"{name} joined from its own laptop.",
                                    f"join {node_id} from {req.host}:{port} labels={req.labels}", {"node": node_id},
                                    {"labels": req.labels, "host": req.host})
        elif moved:
            log.info("%s now at %s", node_id, req.host)
        return JoinResult(node_id=node_id, port=port, display_name=name, labels=c.labels, new=new)

    async def _agent_answers(self, node_id: str) -> bool:
        """Does the agent at the address we know for `node_id` still answer, as that machine?"""
        try:
            r = await get_rpc().request(agent_target(node_id), "GET", "/agent/status", allow_relay=False, timeout=2.0)
            return r.status_code == 200 and r.json().get("node_id") == node_id
        except (NetworkError, ValueError):
            return False

    # ── rename ──
    async def _node_labels(self, node_id: str) -> dict[str, str]:
        r = await self._meta("GET", "/v1/cluster")
        if r is not None and r.status_code == 200:
            for n in r.json().get("nodes", []):
                if n.get("id") == node_id:
                    return dict(n.get("labels", {}))
        return dict(self.procs.children[node_id].labels)

    async def _apply_name(self, node_id: str, name: str) -> bool:
        body = LabelsPatch(labels=await self._node_labels(node_id), display_name=name).model_dump()
        r = await self._meta("PATCH", f"/v1/nodes/{node_id}/labels", body)
        return r is not None and r.status_code < 400

    async def rename(self, node_id: str, name: str) -> Proc:
        name = " ".join(name.split())
        c = self.procs.children.get(node_id)
        if c is None or node_id in CONTROL_DISPLAY_NAMES or node_id == "oracle":
            raise VaultHTTPError(404, "unknown_node", f"There's no machine called {node_id}.")
        if not name or len(name) > MAX_NAME:
            raise VaultHTTPError(400, "bad_name", f"Use a name of 1 to {MAX_NAME} characters.")
        old = c.display_name
        if not await self._apply_name(node_id, name):
            raise VaultHTTPError(503, "rename_failed", "The Vault index didn't take the new name. Is it running?")
        c.display_name = name
        self.renames[node_id] = name
        await self._meta("POST", "/v1/events", ExternalEvent(
            type="chaos.rename", subject={"node": node_id}, human=f"You renamed {old} to {name}.",
            technical=f"rename {node_id}: {old!r} → {name!r}").model_dump(mode="json"))
        return self.procs.view(node_id)

    def info(self) -> ClusterInfo:
        return ClusterInfo(hub=self.cfg.cluster.host, lan=bool(os.environ.get(ENV_HUB)),
                           agent_port=self.cfg.cluster.ports.agent, next_node_id=self.procs.next_node_id())

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
            for p in [p for p in self.procs.node_ids() if self.procs.children[p].remote]:
                await self.procs.wipe_remote(p)      # data on the other laptops goes too
            self.renames = {p: n for p, n in self.renames.items() if p in self.procs.children}
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
            alive = await self._wait_nodes_alive(nodes, timeout=15)
            for node_id, name in self.renames.items():   # metadata starts from vault.yaml's names again
                await self._apply_name(node_id, name)

            files = 0
            if req.seed:
                # One more heartbeat round so every node's lease is on disk before the burst of writes.
                await asyncio.sleep(0.6)
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
