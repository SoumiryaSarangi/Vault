"""Process control for the supervisor (ARCHITECTURE §6). Owner: Anushka.

Starts metadata, gateway, n1..nN and the oracle as child processes. Restarts nothing
automatically: crashes are demo events. Kill = proc.kill() (SIGKILL / TerminateProcess).

LAN mode (docs/soum_lan_demo.md, added by Soum with Anushka's OK): a machine on another laptop is a
remote child. Its process belongs to the node agent on that laptop (node/soum_agent.py), so start/kill
go to the agent over rpc (target "agent:<id>") and `running` is the agent's last answer, refreshed by
poll_agents(). An agent that can't be reached (laptop asleep, Wi-Fi off) reads as stopped.
"""
import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from vault.common.config import CONTROL_DISPLAY_NAMES, ENV_NODE_DISPLAY_NAME, ENV_NODE_LABELS, VaultConfig
from vault.common.models import AgentStatus, Proc
from vault.common.rpc import NetworkError, get_rpc
from vault.common.service import VaultHTTPError

log = logging.getLogger("sup.procs")
BACKEND_DIR = Path(__file__).resolve().parents[2]          # …/backend
AGENT_POLL_S = 1.0
AGENT_TIMEOUT_S = 1.5
ASLEEP = "asleep or off the network"


def agent_target(node_id: str) -> str:
    """rpc participant id of the agent that runs `node_id` on another laptop."""
    return f"agent:{node_id}"


@dataclass
class Child:
    pid: str
    module: str
    display_name: str
    extra_args: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)      # nodes only (initial labels)
    added: bool = False                                        # machine added at runtime (n7+)
    proc: Optional[asyncio.subprocess.Process] = None
    started_at: float = 0.0
    host: Optional[str] = None                                 # LAN mode: set → runs on that laptop
    agent: Optional[AgentStatus] = None                        # its agent's last answer (None: unreachable)

    @property
    def remote(self) -> bool:
        return self.host is not None

    @property
    def running(self) -> bool:
        if self.remote:
            return self.agent is not None and self.agent.running
        return self.proc is not None and self.proc.returncode is None


class Procs:
    def __init__(self, cfg: VaultConfig):
        self.cfg = cfg
        self.children: dict[str, Child] = {}
        self.children["meta"] = Child("meta", "vault.metadata", CONTROL_DISPLAY_NAMES["meta"])
        self.children["gw"] = Child("gw", "vault.gateway", CONTROL_DISPLAY_NAMES["gw"])
        for node_id, n in cfg.nodes.items():
            self.children[node_id] = Child(node_id, "vault.node", n.display_name, ["--id", node_id],
                                           labels=dict(n.labels))
        self.children["oracle"] = Child("oracle", "vault.oracle", "Durability check")

    def _env(self, c: Child) -> dict[str, str]:
        env = dict(os.environ)
        if c.added:   # not in vault.yaml: the node reads these via config.node_identity()
            env[ENV_NODE_DISPLAY_NAME] = c.display_name
            env[ENV_NODE_LABELS] = json.dumps(c.labels)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(BACKEND_DIR), env.get("PYTHONPATH", "")]))
        env["VAULT_CONFIG"] = str(Path(os.environ.get("VAULT_CONFIG", "vault.yaml")).resolve())
        env["PYTHONUNBUFFERED"] = "1"
        return env

    # ── remote machines (LAN mode) ──
    async def _agent(self, c: Child, method: str, path: str) -> AgentStatus:
        """Call the agent for a remote child; remembers its answer. Raises 503 if it can't be reached."""
        try:
            r = await get_rpc().request(agent_target(c.pid), method, path, allow_relay=False,
                                        timeout=AGENT_TIMEOUT_S if method == "GET" else 10.0)
            if r.status_code != 200:
                raise VaultHTTPError(502, "agent_failed", f"{c.display_name}'s laptop refused: {r.text[:200]}")
            c.agent = AgentStatus.model_validate(r.json())
            return c.agent
        except NetworkError as e:
            c.agent = None
            raise VaultHTTPError(503, "asleep", f"{c.display_name} is {ASLEEP}. Wake that laptop first.",
                                 {"reason": e.reason})

    def add_remote(self, node_id: str, display_name: str, labels: dict[str, str], host: str) -> Child:
        """Register (or update, after an IP change) a machine that runs on another laptop."""
        c = self.children.get(node_id)
        if c is None:
            c = self.children[node_id] = Child(node_id, "vault.node", display_name, ["--id", node_id],
                                               labels=dict(labels), added=True)
        c.host = host
        return c

    async def poll_agents(self) -> None:
        """Keep each remote child's running state fresh (the dashboard polls /procs every second)."""
        while True:
            remote = [c for c in self.children.values() if c.remote]
            await asyncio.gather(*(self.poll_one(c) for c in remote))
            await asyncio.sleep(AGENT_POLL_S)

    async def poll_one(self, c: Child) -> None:
        try:
            await self._agent(c, "GET", "/agent/status")
        except VaultHTTPError:
            pass                                                   # c.agent is None → "asleep"

    async def wipe_remote(self, pid: str) -> None:
        """Reset: the agent deletes its node's data (it must be stopped). Best effort."""
        c = self.children[pid]
        try:
            await self._agent(c, "POST", "/agent/node/wipe")
        except VaultHTTPError as e:
            log.warning("wipe %s on %s failed: %s", pid, c.host, e.body.message)

    # ── local and remote ──
    async def start(self, pid: str) -> Proc:
        c = self.children[pid]
        if c.running:
            return self.view(pid)
        if c.remote:
            await self._agent(c, "POST", "/agent/node/start")
            return self.view(pid)
        logs = Path(self.cfg.cluster.logs_dir)
        logs.mkdir(parents=True, exist_ok=True)
        out = open(logs / f"{pid}.stdout.log", "ab")
        c.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", c.module, *c.extra_args, env=self._env(c), stdout=out, stderr=out)
        out.close()   # the child holds its own handle
        c.started_at = time.time()
        self._save_pids()
        log.info("started %s (os pid %s)", pid, c.proc.pid)
        return self.view(pid)

    async def kill(self, pid: str) -> Proc:
        c = self.children[pid]
        if c.running and c.remote:
            await self._agent(c, "POST", "/agent/node/stop")
            log.info("stopped %s on %s", pid, c.host)
        elif c.running:
            c.proc.kill()
            await c.proc.wait()
            self._save_pids()
            log.info("killed %s", pid)
        return self.view(pid)

    async def restart(self, pid: str) -> Proc:
        await self.kill(pid)
        return await self.start(pid)

    # A supervisor that is force-killed leaves orphans holding the ports (Windows has no process groups
    # here). We record child OS pids and kill leftovers from the previous run before starting.
    @property
    def _pidfile(self) -> Path:
        return Path(self.cfg.cluster.logs_dir) / "children.json"

    def _save_pids(self) -> None:
        pids = {p: c.proc.pid for p, c in self.children.items() if not c.remote and c.running}
        self._pidfile.write_text(json.dumps(pids))

    def kill_stale(self) -> None:
        try:
            stale = json.loads(self._pidfile.read_text())
        except (OSError, ValueError):
            return
        for pid, os_pid in stale.items():
            try:
                os.kill(int(os_pid), signal.SIGTERM)    # TerminateProcess on Windows
                log.warning("killed stale %s (os pid %s) from a previous run", pid, os_pid)
            except (OSError, ValueError):
                pass
        self._pidfile.unlink(missing_ok=True)

    async def start_all(self) -> None:
        self.kill_stale()
        # metadata first so nodes can register; the rest in parallel
        await self.start("meta")
        await asyncio.sleep(0.5)
        await asyncio.gather(*(self.start(p) for p in self.children if p != "meta" and not self.children[p].remote))

    def node_ids(self) -> list[str]:
        return [p for p, c in self.children.items() if c.module == "vault.node"]

    def next_node_id(self) -> str:
        return f"n{max(int(p[1:]) for p in self.node_ids()) + 1}"

    def add_node(self, display_name: str, labels: dict[str, str]) -> str:
        node_id = self.next_node_id()
        self.children[node_id] = Child(node_id, "vault.node", display_name, ["--id", node_id],
                                       labels=dict(labels), added=True)
        return node_id

    def drop_added_nodes(self) -> list[str]:
        """Forget machines added at runtime (reset). They must already be stopped.
        Machines on other laptops stay: their agents are still there and start them again."""
        gone = [p for p, c in self.children.items() if c.added and not c.remote]
        for p in gone:
            del self.children[p]
        return gone

    async def kill_many(self, pids: list[str]) -> list[str]:
        """Kill at once (a power cut). Returns the pids that were running."""
        running = [p for p in pids if self.children[p].running]
        results = await asyncio.gather(*(self.kill(p) for p in running), return_exceptions=True)
        return self._done(running, results, "kill")

    @staticmethod
    def _done(pids: list[str], results: list, what: str) -> list[str]:
        """pids whose call worked. Only a remote machine can fail (its laptop fell asleep mid-call)."""
        ok = []
        for p, res in zip(pids, results):
            if isinstance(res, VaultHTTPError):
                log.warning("%s %s: %s", what, p, res.body.message)
            elif isinstance(res, BaseException):
                raise res
            else:
                ok.append(p)
        return ok

    async def start_many(self, pids: list[str]) -> list[str]:
        """Start, metadata first so nodes can register. Returns the pids that were stopped."""
        stopped = [p for p in pids if not self.children[p].running]
        if "meta" in stopped:
            await self.start("meta")
            await asyncio.sleep(0.5)
        rest = [p for p in stopped if p != "meta"]
        results = await asyncio.gather(*(self.start(p) for p in rest), return_exceptions=True)
        return (["meta"] if "meta" in stopped else []) + self._done(rest, results, "start")

    async def kill_all(self) -> None:
        """Supervisor shutdown: our own children only. Machines on other laptops keep running and
        rejoin when the hub comes back."""
        await asyncio.gather(*(self.kill(p) for p, c in self.children.items() if not c.remote))
        self._pidfile.unlink(missing_ok=True)

    def view(self, pid: str) -> Proc:
        c = self.children[pid]
        if c.remote:
            a = c.agent
            return Proc(pid=pid, port=self.cfg.port(pid), state="running" if c.running else "stopped",
                        os_pid=a.os_pid if a and a.running else None,
                        uptime_s=a.uptime_s if a and a.running else 0.0, display_name=c.display_name,
                        remote=True, host=c.host, note=None if a is not None else ASLEEP)
        return Proc(pid=pid, port=self.cfg.port(pid), state="running" if c.running else "stopped",
                    os_pid=c.proc.pid if c.running else None,
                    uptime_s=round(time.time() - c.started_at, 1) if c.running else 0.0,
                    display_name=c.display_name)

    def list(self) -> list[Proc]:
        return [self.view(p) for p in self.children]
