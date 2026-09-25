"""Chaos controller (ARCHITECTURE §6, §7.4). Owner: Anushka. Tasks A2 and A3 (power, scripts).

Every chaos action:
  1. is applied (process control, or /_chaos/* on the affected participants, never via a relay),
  2. is reported to metadata as ground truth: POST /v1/incidents/fault {kind, subject, at}
     and an ExternalEvent chaos.<action> with the DESIGN §5.8 copy. Best effort: if metadata is
     down or not built yet, the action still happens and the report is only logged,
  3. is listed in GET /chaos until cleared (one-shot actions such as corrupt and kill are not listed).

FaultReport.kind values (Jaiveer matches incidents on kind + subject):
  node_dead (kill, freeze) · partition (link cut, subject "a|b") · corruption · slow · disk_full · power_cut
"""
import asyncio
import logging
import secrets
import time
from typing import Any, Optional

from vault.common.config import CONTROL_DISPLAY_NAMES, VaultConfig
from vault.common.events import domain_name
from vault.common.models import (ChaosStep, ExternalEvent, Fault, FaultReport, LinkRequest, NodeChaosRequest,
                                 PowerCutRequest, PowerCutResult, PowerRestoreRequest, PowerRestoreResult, Proc,
                                 ScriptRequest)
from vault.common.rpc import NetworkError, get_rpc
from vault.common.service import VaultHTTPError

log = logging.getLogger("sup.chaos")


class ChaosController:
    def __init__(self, cfg: VaultConfig, procs):
        self.cfg = cfg
        self.procs = procs                           # anushka_procs.Procs
        self.faults: dict[str, Fault] = {}
        self._expires: dict[str, float] = {}         # fault id → time it ends by itself (freeze)
        self._power: dict[str, list[str]] = {}       # power fault id → pids it turned off
        self._timers: dict[str, asyncio.Task] = {}   # power fault id → auto-restore task
        self._pending: list[dict[str, Any]] = []     # reports held while metadata is off (power cut all)
        self._scripts: dict[str, asyncio.Task] = {}

    # ── names ──
    def name(self, pid: str) -> str:
        if pid in CONTROL_DISPLAY_NAMES:
            return CONTROL_DISPLAY_NAMES[pid]
        child = self.procs.children.get(pid)
        return child.display_name if child else pid

    def _check_target(self, pid: str) -> None:
        if pid not in self.procs.children or pid == "oracle":
            raise VaultHTTPError(404, "unknown_target", f"There's no machine called {pid}.")

    # ── transport to participants ──
    async def _chaos_call(self, pid: str, path: str, body: Optional[dict] = None) -> dict:
        """POST /_chaos/<path> on a participant, directly (the supervisor never relays)."""
        if not self.procs.children[pid].running:
            raise VaultHTTPError(409, "not_running", f"{self.name(pid)} is turned off.")
        try:
            r = await get_rpc().request(pid, "POST", f"/_chaos/{path}", json=body or {}, allow_relay=False)
        except NetworkError as e:
            raise VaultHTTPError(503, "unreachable", f"Couldn't reach {self.name(pid)}.", {"reason": e.reason})
        if r.status_code >= 400:
            raise VaultHTTPError(r.status_code, "chaos_failed", f"{self.name(pid)} refused: {r.text[:200]}")
        return r.json()

    async def wait_healthy(self, pid: str, timeout: float = 15.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                r = await get_rpc().request(pid, "GET", "/_vault/health", allow_relay=False)
                if r.status_code == 200:
                    return True
            except NetworkError:
                pass
            await asyncio.sleep(0.2)
        return False

    # ── ground truth + timeline (best effort) ──
    async def report(self, kind: str, subject: str, event_type: str, human: str, technical: str,
                     subject_obj: dict[str, Any], data: Optional[dict[str, Any]] = None, at: Optional[float] = None) -> None:
        at = at or time.time()
        rpc = get_rpc()
        calls = [("/v1/incidents/fault", FaultReport(kind=kind, subject=subject, at=at)),
                 ("/v1/events", ExternalEvent(type=event_type, subject=subject_obj, human=human,
                                              technical=technical, data={**(data or {}), "at": at}))]
        for path, body in calls:
            try:
                r = await rpc.request("meta", "POST", path, json=body.model_dump(mode="json"), allow_relay=False)
                if r.status_code >= 400:
                    log.warning("metadata %s -> %s (not built yet?)", path, r.status_code)
            except NetworkError as e:
                log.warning("metadata unreachable for %s: %s", path, e.reason)
        log.info("chaos %s: %s", event_type, human)

    # ── fault registry ──
    def _add(self, fid: str, kind: str, subject: str, params: dict[str, Any], lasts_s: Optional[float] = None) -> None:
        self.faults[fid] = Fault(id=fid, kind=kind, subject=subject, since=time.time(), params=params)
        if lasts_s:
            self._expires[fid] = time.time() + lasts_s
        else:
            self._expires.pop(fid, None)

    def _remove(self, fid: str) -> None:
        self.faults.pop(fid, None)
        self._expires.pop(fid, None)

    def active(self) -> list[Fault]:
        now = time.time()
        for fid in [f for f, end in self._expires.items() if end <= now]:
            self._remove(fid)
        return sorted(self.faults.values(), key=lambda f: f.since)

    # ── process actions (the /procs routes and chaos scripts) ──
    async def kill_proc(self, pid: str) -> Proc:
        was_running = self.procs.children[pid].running
        view = await self.procs.kill(pid)
        if was_running:
            await self.report("node_dead", pid, "chaos.kill", f"You turned off {self.name(pid)}.", f"kill {pid}",
                              {"node": pid})
        return view

    async def start_proc(self, pid: str) -> Proc:
        was_running = self.procs.children[pid].running
        view = await self.procs.start(pid)
        if not was_running:
            self._remove(f"freeze:{pid}")
            await self.report("node_start", pid, "chaos.start", f"You turned {self.name(pid)} back on.",
                              f"start {pid}", {"node": pid})
        return view

    # ── A2: links ──
    async def link(self, req: LinkRequest) -> list[Fault]:
        for p in (req.a, req.b):
            self._check_target(p)
        if req.a == req.b:
            raise VaultHTTPError(400, "same_machine", "Pick two different machines.")
        a, b = sorted((req.a, req.b)) if req.direction == "both" else (req.a, req.b)
        fid = f"link:{a}|{b}"
        if req.cut:
            if req.direction == "both":
                await self._chaos_call(a, "block", {"peers": [b], "direction": "both"})
                await self._chaos_call(b, "block", {"peers": [a], "direction": "both"})
            else:   # a_to_b: a can't send to b, and b drops anything from a
                await self._chaos_call(a, "block", {"peers": [b], "direction": "out"})
                await self._chaos_call(b, "block", {"peers": [a], "direction": "in"})
            self._add(fid, "link", f"{a}|{b}", {"a": a, "b": b, "direction": req.direction})
            way = "" if req.direction == "both" else " (one way)"
            await self.report("partition", f"{a}|{b}", "chaos.link_cut",
                              f"You cut the cable between {self.name(a)} and {self.name(b)}{way}.",
                              f"block {a}↔{b} ({req.direction})", {"a": a, "b": b}, {"direction": req.direction})
        else:
            for x, y in ((a, b), (b, a)):
                if self.procs.children[x].running:
                    await self._chaos_call(x, "unblock", {"peers": [y]})
            self._remove(f"link:{a}|{b}")
            self._remove(f"link:{b}|{a}")
            await self.report("link_restored", f"{a}|{b}", "chaos.link_restored",
                              f"You reconnected {self.name(a)} and {self.name(b)}.", f"unblock {a}↔{b}",
                              {"a": a, "b": b})
        return self.active()

    # ── A2: node faults ──
    async def node(self, pid: str, req: NodeChaosRequest) -> list[Fault]:
        self._check_target(pid)
        p, name, act = req.params, self.name(pid), req.action
        if act == "slow":
            ms = int(p.get("ms", 800))
            await self._chaos_call(pid, "slow", {"ms": ms})
            if ms > 0:
                self._add(f"slow:{pid}", "slow", pid, {"ms": ms})
                await self.report("slow", pid, "chaos.slow", f"You slowed {name} by {ms} ms.", f"slow {pid} {ms}ms",
                                  {"node": pid}, {"ms": ms})
            else:
                self._remove(f"slow:{pid}")
        elif act == "freeze":
            seconds = float(p.get("seconds", 10))
            await self._chaos_call(pid, "freeze", {"seconds": seconds})
            self._add(f"freeze:{pid}", "freeze", pid, {"seconds": seconds}, lasts_s=seconds)
            await self.report("node_dead", pid, "chaos.freeze", f"You froze {name} for {seconds:g} s.",
                              f"freeze {pid} {seconds:g}s", {"node": pid}, {"seconds": seconds})
        elif act == "corrupt":
            if not pid.startswith("n"):
                raise VaultHTTPError(400, "not_a_node", "Only storage machines hold copies to damage.")
            body: dict[str, Any] = {"mode": p.get("mode", "bitflip")}
            if p.get("fids"):
                body["fids"] = p["fids"]
            else:
                body["count"] = int(p.get("count", 10))
            res = await self._chaos_call(pid, "corrupt", body)
            n = len(res.get("corrupted", []))
            await self.report("corruption", pid, "chaos.corrupt",
                              f"You damaged {n} {'copy' if n == 1 else 'copies'} on {name}.",
                              f"corrupt {pid} n={n} mode={body['mode']}", {"node": pid},
                              {"fids": res.get("corrupted", [])})
        elif act == "disk_full":
            on = bool(p.get("on", True))
            await self._chaos_call(pid, "disk_full", {"on": on})
            if on:
                self._add(f"disk_full:{pid}", "disk_full", pid, {})
                await self.report("disk_full", pid, "chaos.disk_full", f"You filled up {name}'s disk.",
                                  f"disk_full {pid} on", {"node": pid})
            else:
                self._remove(f"disk_full:{pid}")
        elif act == "clear":
            await self._chaos_call(pid, "clear")
            for fid in [f for f, x in self.faults.items() if x.subject == pid]:
                self._remove(fid)
        return self.active()

    async def corrupt_random(self, count: int, mode: str = "bitflip") -> list[str]:
        """Damage `count` copies spread over the running nodes (DESIGN §5.8 "Damage copies"). Returns fids."""
        nodes = [p for p in self.procs.node_ids() if self.procs.children[p].running]
        if not nodes:
            raise VaultHTTPError(409, "no_machines", "No storage machines are on.")
        per, extra = divmod(count, len(nodes))
        fids: list[str] = []
        for i, pid in enumerate(nodes):
            k = per + (1 if i < extra else 0)
            if k:
                res = await self._chaos_call(pid, "corrupt", {"count": k, "mode": mode})
                fids += res.get("corrupted", [])
        await self.report("corruption", "cluster", "chaos.corrupt", f"You damaged {len(fids)} random copies.",
                          f"corrupt random n={len(fids)} mode={mode}", {}, {"fids": fids})
        return fids

    # ── A3: power ──
    async def _labels(self) -> dict[str, dict[str, str]]:
        """node_id → labels. Live labels come from metadata (relabels happen there), else our own copy."""
        try:
            r = await get_rpc().request("meta", "GET", "/v1/cluster", allow_relay=False)
            if r.status_code == 200:
                return {n["id"]: n.get("labels", {}) for n in r.json().get("nodes", [])}
        except (NetworkError, ValueError):
            pass
        return {p: self.procs.children[p].labels for p in self.procs.node_ids()}

    async def _power_targets(self, scope: str, label: Optional[str], running: bool) -> tuple[str, str, list[str]]:
        """→ (fault id, human name, pids in scope that are running (for a cut) or stopped (for a restore))."""
        if scope == "all":
            pids = ["meta", "gw", *self.procs.node_ids()]
            fid, what = "power:all", "everything"
        else:
            if not label or "=" not in label:
                raise VaultHTTPError(400, "bad_label", "Pick a power strip, like power=A.")
            key, value = label.split("=", 1)
            labels = await self._labels()
            pids = [n for n in self.procs.node_ids() if labels.get(n, {}).get(key) == value]
            fid, what = f"power:{label}", domain_name(key, value)
        return fid, what, [p for p in pids if self.procs.children[p].running == running]

    async def power_cut(self, req: PowerCutRequest) -> PowerCutResult:
        at = time.time()
        fid, what, targets = await self._power_targets(req.scope, req.label, running=True)
        if not targets:
            raise VaultHTTPError(409, "nothing_to_cut", f"Every machine on {what} is already off.")
        killed = await self.procs.kill_many(targets)
        self._power[fid] = killed
        subject = "all" if req.scope == "all" else str(req.label)
        self._add(fid, "power_cut", subject, {"killed": killed, "restore_after_s": req.restore_after_s})
        human = "You cut the power to everything." if req.scope == "all" else f"You cut {what}."
        args = dict(kind="power_cut", subject=subject, event_type="chaos.power_cut", human=human,
                    technical=f"power cut {subject}: killed {','.join(killed)}", subject_obj={"power": subject},
                    data={"killed": killed}, at=at)
        if "meta" in killed:
            # Restarted processes come back with fresh netsim state, so link/slow faults are gone too.
            for f in [f for f in self.faults if not f.startswith("power:")]:
                self._remove(f)
            self._pending.append(args)          # metadata is off: report once it's back
            log.info("chaos chaos.power_cut: %s", human)
        else:
            await self.report(**args)
        if req.restore_after_s:
            self._timers[fid] = asyncio.create_task(self._auto_restore(req, req.restore_after_s))
        return PowerCutResult(killed=killed)

    async def _auto_restore(self, req: PowerCutRequest, delay: float) -> None:
        await asyncio.sleep(delay)
        try:
            await self.power_restore(PowerRestoreRequest(scope=req.scope, label=req.label))
        except VaultHTTPError as e:
            log.warning("auto-restore failed: %s", e.body.message)

    async def power_restore(self, req: PowerRestoreRequest) -> PowerRestoreResult:
        fid = "power:all" if req.scope == "all" else f"power:{req.label}"
        timer = self._timers.pop(fid, None)
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        pids = self._power.pop(fid, None)
        if pids is None:   # not cut by us: turn on whatever in scope is off
            _, _, pids = await self._power_targets(req.scope, req.label, running=False)
        started = await self.procs.start_many(pids)
        self._remove(fid)
        if "meta" in started:
            await self.wait_healthy("meta")
        pending, self._pending = self._pending, []
        for args in pending:
            await self.report(**args)
        what = "everything" if req.scope == "all" else domain_name(*str(req.label).split("=", 1))
        await self.report("power_restore", "all" if req.scope == "all" else str(req.label), "chaos.power_restore",
                          f"You turned the power back on for {what}.", f"power restore: started {','.join(started)}",
                          {"power": req.label or "all"}, {"started": started})
        return PowerRestoreResult(started=started)

    # ── A3: seeded chaos scripts (the steps come from Urooz's urooz_scripts.steps) ──
    async def run_script(self, req: ScriptRequest) -> str:
        try:
            from vault.supervisor.urooz_scripts import steps
            plan = [ChaosStep.model_validate(x) if isinstance(x, dict) else x
                    for x in steps(req.name, req.seed, self.procs.node_ids())]
        except (ImportError, NotImplementedError) as e:
            raise VaultHTTPError(501, "scripts_not_ready", "Chaos scripts aren't built yet (Urooz, task U3).",
                                 {"reason": str(e)})
        sid = f"s_{secrets.token_hex(4)}"
        self._scripts[sid] = asyncio.create_task(self._run_steps(sid, req.name, sorted(plan, key=lambda x: x.t)))
        log.info("script %s (%s, seed %s): %d steps", sid, req.name, req.seed, len(plan))
        return sid

    async def _run_steps(self, sid: str, name: str, plan: list[ChaosStep]) -> None:
        t0 = time.time()
        try:
            for step in plan:
                delay = t0 + step.t - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                try:
                    await self.execute(step)
                except VaultHTTPError as e:     # one failing step (e.g. target already off) never stops the script
                    log.warning("script %s t=%.1f %s failed: %s", sid, step.t, step.action, e.body.message)
        finally:
            self._scripts.pop(sid, None)
            log.info("script %s (%s) finished", sid, name)

    async def execute(self, step: ChaosStep) -> None:
        p, a = step.params, step.action
        if a == "kill":
            await self.kill_proc(p["pid"])
        elif a == "start":
            await self.start_proc(p["pid"])
        elif a == "restart_down":
            for pid in [n for n in self.procs.node_ids() if not self.procs.children[n].running]:
                await self.start_proc(pid)
        elif a == "corrupt":
            await self.corrupt_random(int(p.get("count", 10)), p.get("mode", "bitflip"))
        elif a == "link":
            await self.link(LinkRequest(**p))
        elif a in ("slow", "freeze"):
            await self.node(p["pid"], NodeChaosRequest(action=a, params={k: v for k, v in p.items() if k != "pid"}))
        elif a == "power_cut":
            await self.power_cut(PowerCutRequest(**p))
        elif a == "power_restore":
            await self.power_restore(PowerRestoreRequest(**p))
        elif a == "clear":
            await self.clear_all(stop_scripts=False)

    def stop_script(self, sid: str) -> bool:
        task = self._scripts.get(sid)
        if task is None:
            return False
        task.cancel()
        return True

    def stop_scripts(self) -> None:
        for task in list(self._scripts.values()):
            if task is not asyncio.current_task():
                task.cancel()

    def forget_all(self) -> None:
        """Reset: stop scripts and timers and drop every fault record (all processes restart)."""
        self.stop_scripts()
        for t in self._timers.values():
            t.cancel()
        self._timers.clear()
        self._power.clear()
        self._pending.clear()
        self.faults.clear()
        self._expires.clear()

    # ── A2: clear everything ──
    async def clear_all(self, stop_scripts: bool = True) -> list[Fault]:
        """Fix every cable and speed problem. Machines that are off stay off (DESIGN §5.8)."""
        if stop_scripts:
            self.stop_scripts()
        for pid, c in self.procs.children.items():
            if c.running and pid != "oracle":
                try:
                    await self._chaos_call(pid, "clear")
                except VaultHTTPError as e:
                    log.warning("clear %s failed: %s", pid, e.body.message)
        for fid in [f for f in self.faults if not f.startswith("power:")]:
            self._remove(fid)
        await self.report("clear", "cluster", "chaos.clear", "You fixed every cable and speed problem.", "clear_all", {})
        return self.active()
