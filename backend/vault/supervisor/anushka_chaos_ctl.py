"""Chaos controller (ARCHITECTURE §6, §7.4). Owner: Anushka. Tasks A2 (done here) and A3.

Every chaos action:
  1. is applied (process control, or /_chaos/* on the affected participants, never via a relay),
  2. is reported to metadata as ground truth: POST /v1/incidents/fault {kind, subject, at}
     and an ExternalEvent chaos.<action> with the DESIGN §5.8 copy. Best effort: if metadata is
     down or not built yet, the action still happens and the report is only logged,
  3. is listed in GET /chaos until cleared (one-shot actions such as corrupt and kill are not listed).

FaultReport.kind values (Jaiveer matches incidents on kind + subject):
  node_dead (kill, freeze) · partition (link cut, subject "a|b") · corruption · slow · disk_full · power_cut
"""
import logging
import time
from typing import Any, Optional

from vault.common.config import CONTROL_DISPLAY_NAMES, VaultConfig
from vault.common.models import ExternalEvent, Fault, FaultReport, LinkRequest, NodeChaosRequest
from vault.common.rpc import NetworkError, get_rpc
from vault.common.service import VaultHTTPError

log = logging.getLogger("sup.chaos")


class ChaosController:
    def __init__(self, cfg: VaultConfig, procs):
        self.cfg = cfg
        self.procs = procs                      # anushka_procs.Procs
        self.faults: dict[str, Fault] = {}
        self._expires: dict[str, float] = {}    # fault id → time it ends by itself (freeze)

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
                    log.warning("metadata %s → %s (not built yet?)", path, r.status_code)
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

    # ── process actions (called by the /procs routes) ──
    async def on_kill(self, pid: str) -> None:
        await self.report("node_dead", pid, "chaos.kill", f"You turned off {self.name(pid)}.", f"kill {pid}",
                          {"node": pid})

    async def on_start(self, pid: str) -> None:
        self._remove(f"freeze:{pid}")
        await self.report("node_start", pid, "chaos.start", f"You turned {self.name(pid)} back on.", f"start {pid}",
                          {"node": pid})

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
            body = {"mode": p.get("mode", "bitflip")}
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
        nodes = [p for p, c in self.procs.children.items() if p.startswith("n") and c.running]
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

    # ── A2: clear everything ──
    async def clear_all(self) -> list[Fault]:
        for pid, c in self.procs.children.items():
            if c.running and pid != "oracle":
                try:
                    await self._chaos_call(pid, "clear")
                except VaultHTTPError as e:
                    log.warning("clear %s failed: %s", pid, e.body.message)
        self.faults.clear()
        self._expires.clear()
        await self.report("clear", "cluster", "chaos.clear", "You fixed every cable and speed problem.", "clear_all", {})
        return self.active()
