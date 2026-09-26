"""Node agent: runs one storage machine on a laptop that joins the hub (LAN mode, docs/soum_lan_demo.md).
Owner: Soum.

    python -m vault join --hub 192.168.1.101 --id n2 --name "Doctor's Desk" --strip B

  * Joins: POST sup /nodes/join {node_id, display_name, labels, host, agent_port}. The hub calls us back on
    GET /agent/status first, so a firewall problem shows up here, in plain words, instead of a grey card.
  * Starts the node once, after the first join, with VAULT_HUB (everything else is on the hub) and
    VAULT_NODE_ADDR=<this laptop's IP>:<port> (what the node advertises when it registers).
  * Re-joins every JOIN_EVERY_S: harmless when nothing changed, and it is how the hub finds us again after
    it restarts or resets, and how a rename in the dashboard reaches us (the name is used on the next start).
    If this laptop's IP changed (new DHCP lease after sleep), a running node restarts with the new address.
  * The hub's supervisor turns the machine off and on through POST /agent/node/stop|start, and a reset
    wipes its data through POST /agent/node/wipe. Closing the lid needs nothing from us: the laptop sleeps,
    heartbeats stop, and the hub's detector does the rest; on wake the node re-registers by itself.

Process control reuses the supervisor's Procs (one child, its own pid file) instead of a second spawner.
"""
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI

from vault.common.config import ENV_NODE_ADDR, VaultConfig
from vault.common.log import setup_logging
from vault.common.models import AgentStatus, JoinRequest, JoinResult
from vault.common.rpc import NetworkError, get_rpc
from vault.common.service import VaultHTTPError, make_app
from vault.common.soum_lan import detect_lan_ip
from vault.supervisor.anushka_cluster import _rmtree_retry
from vault.supervisor.anushka_procs import Child, Procs

log = logging.getLogger("agent")

JOIN_EVERY_S = 3.0


class AgentProcs(Procs):
    """The supervisor's process control, holding just this laptop's node."""

    def __init__(self, cfg: VaultConfig, child: Child):
        super().__init__(cfg)
        self.children = {child.pid: child}

    @property
    def _pidfile(self) -> Path:
        return Path(self.cfg.cluster.logs_dir) / f"agent-{next(iter(self.children))}.json"


class Agent:
    def __init__(self, cfg: VaultConfig, node_id: str, name: str, labels: dict[str, str], hub: str,
                 agent_port: int):
        self.cfg, self.node_id, self.hub, self.agent_port = cfg, node_id, hub, agent_port
        self.labels = labels
        self.child = Child(node_id, "vault.node", name, ["--id", node_id], labels=dict(labels), added=True)
        self.procs = AgentProcs(cfg, self.child)
        self.host = detect_lan_ip(hub)
        self.joined: Optional[JoinResult] = None
        self._said: Optional[str] = None           # last problem printed (print each one once)

    @property
    def port(self) -> int:
        return self.cfg.port(self.node_id)

    def status(self) -> AgentStatus:
        c = self.child
        return AgentStatus(node_id=self.node_id, running=c.running, os_pid=c.proc.pid if c.running else None,
                           uptime_s=round(time.time() - c.started_at, 1) if c.running else 0.0, host=self.host)

    # ── the node process ──
    async def start(self) -> AgentStatus:
        os.environ[ENV_NODE_ADDR] = f"{self.host}:{self.port}"       # inherited by the node (Procs._env)
        await self.procs.start(self.node_id)
        return self.status()

    async def stop(self) -> AgentStatus:
        await self.procs.kill(self.node_id)
        return self.status()

    async def wipe(self) -> AgentStatus:
        if self.child.running:
            raise VaultHTTPError(409, "running", "Turn the machine off before wiping its data.")
        await asyncio.to_thread(_rmtree_retry, self.cfg.data_path(self.node_id))
        log.info("wiped %s", self.cfg.data_path(self.node_id))
        return self.status()

    # ── talking to the hub ──
    def _say(self, msg: str) -> None:
        if msg != self._said:
            print(msg, flush=True)
            self._said = msg

    async def join_once(self) -> Optional[JoinResult]:
        req = JoinRequest(node_id=self.node_id, display_name=self.child.display_name, labels=self.labels,
                          host=self.host, agent_port=self.agent_port)
        try:
            r = await get_rpc().request("sup", "POST", "/nodes/join", json=req.model_dump(), allow_relay=False,
                                        timeout=8.0)
        except NetworkError:
            self._say(f"Can't reach the hub at {self.hub}:{self.cfg.cluster.ports.supervisor}. Is `python -m vault up "
                      f"--lan` running there, and are both laptops on the same Wi-Fi/hotspot? Retrying…")
            return None
        if r.status_code != 200:
            try:
                msg = r.json().get("message") or r.text
            except ValueError:
                msg = r.text
            self._say(f"The hub refused to add this laptop: {msg}")
            return None
        res = JoinResult.model_validate(r.json())
        if res.display_name != self.child.display_name:
            log.info("name from the hub: %s", res.display_name)
            self.child.display_name = res.display_name              # a rename: used on the next start
        self._say(f"Joined the hub {self.hub} as {res.node_id} \"{res.display_name}\" "
                  f"(this laptop: {self.host}:{res.port}). Leave this window open.")
        return res

    async def run(self) -> None:
        while True:
            try:
                host = detect_lan_ip(self.hub)
                if host != self.host:
                    log.warning("this laptop's IP changed %s → %s", self.host, host)
                    self.host = host
                    if self.child.running:
                        await self.stop()
                        await self.start()
                res = await self.join_once()
                if res is not None and self.joined is None:
                    self.joined = res
                    await self.start()                               # first join only: afterwards the hub decides
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("agent loop error")
            await asyncio.sleep(JOIN_EVERY_S)


def create_app(cfg: VaultConfig, agent: Agent) -> FastAPI:
    async def lifespan(app: FastAPI):
        agent.procs.kill_stale()                    # a node left over from a crashed agent holds the port
        task = asyncio.create_task(agent.run())
        yield
        task.cancel()
        await agent.procs.kill_all()                # Ctrl+C here takes the machine off the cluster

    app = make_app(cfg, f"agent-{agent.node_id}", lifespan, title=f"vault-agent-{agent.node_id}")

    @app.get("/agent/status")
    async def status() -> AgentStatus:
        return agent.status()

    @app.post("/agent/node/start")
    async def start() -> AgentStatus:
        return await agent.start()

    @app.post("/agent/node/stop")
    async def stop() -> AgentStatus:
        return await agent.stop()

    @app.post("/agent/node/restart")
    async def restart() -> AgentStatus:
        await agent.stop()
        return await agent.start()

    @app.post("/agent/node/wipe")
    async def wipe() -> AgentStatus:
        return await agent.wipe()

    return app


def run_agent(cfg: VaultConfig, node_id: str, name: str, labels: dict[str, str], hub: str, agent_port: int) -> None:
    """Entry point for `python -m vault join` (blocks until Ctrl+C)."""
    setup_logging(f"agent-{node_id}", cfg.cluster.logs_dir)
    if sys.platform == "win32":   # subprocesses need the Proactor loop on Windows (as in run_service)
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    agent = Agent(cfg, node_id, name, labels, hub, agent_port)
    print(f"Vault node agent for {node_id} on {agent.host} (agent port {agent_port}, node port {agent.port}); "
          f"hub {hub}.", flush=True)
    uvicorn.run(create_app(cfg, agent), host=cfg.listen_host(), port=agent_port, workers=1, log_level="warning",
                access_log=False, timeout_graceful_shutdown=1)
