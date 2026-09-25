"""Process control for the supervisor (ARCHITECTURE §6). Owner: Anushka.

Starts metadata, gateway, n1..nN and the oracle as child processes. Restarts nothing
automatically: crashes are demo events. Kill = proc.kill() (SIGKILL / TerminateProcess).
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
from vault.common.models import Proc

log = logging.getLogger("sup.procs")
BACKEND_DIR = Path(__file__).resolve().parents[2]          # …/backend


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

    @property
    def running(self) -> bool:
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

    async def start(self, pid: str) -> Proc:
        c = self.children[pid]
        if c.running:
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
        if c.running:
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
        pids = {p: c.proc.pid for p, c in self.children.items() if c.running}
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
        await asyncio.gather(*(self.start(p) for p in self.children if p != "meta"))

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
        """Forget machines added at runtime (reset). They must already be stopped."""
        gone = [p for p, c in self.children.items() if c.added]
        for p in gone:
            del self.children[p]
        return gone

    async def kill_many(self, pids: list[str]) -> list[str]:
        """Kill at once (a power cut). Returns the pids that were running."""
        running = [p for p in pids if self.children[p].running]
        await asyncio.gather(*(self.kill(p) for p in running))
        return running

    async def start_many(self, pids: list[str]) -> list[str]:
        """Start, metadata first so nodes can register. Returns the pids that were stopped."""
        stopped = [p for p in pids if not self.children[p].running]
        if "meta" in stopped:
            await self.start("meta")
            await asyncio.sleep(0.5)
        await asyncio.gather(*(self.start(p) for p in stopped if p != "meta"))
        return stopped

    async def kill_all(self) -> None:
        await asyncio.gather(*(self.kill(p) for p in self.children))
        self._pidfile.unlink(missing_ok=True)

    def view(self, pid: str) -> Proc:
        c = self.children[pid]
        return Proc(pid=pid, port=self.cfg.port(pid), state="running" if c.running else "stopped",
                    os_pid=c.proc.pid if c.running else None,
                    uptime_s=round(time.time() - c.started_at, 1) if c.running else 0.0,
                    display_name=c.display_name)

    def list(self) -> list[Proc]:
        return [self.view(p) for p in self.children]
