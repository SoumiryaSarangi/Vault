"""Plain-language events (ARCHITECTURE §8.1, DESIGN §6.3). Owner: Anushka (shared).

Events are created ONLY through emit(); it fills `human` and `technical` from TEMPLATES.

    await emit("node.down", {"node": "n3"}, {"phi": 9.3}, confirm=1.0)

Template fields = subject ∪ data ∪ keyword fields (keywords win). If the subject has "node" and
you pass no `node=` keyword, the registered name resolver turns "n3" into "Lab Laptop" for
{node}, and {id} becomes "n3". Human lines use display names; IDs belong in technical lines.

The metadata service registers the sink (store in SQLite + push to SSE) with set_sink().
Supervisor and oracle don't call emit(): they POST ExternalEvent to metadata /v1/events.
"""
import logging
import time
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("events")

# type → (severity, human, technical)
TEMPLATES: dict[str, tuple[str, str, str]] = {
    "node.joined": ("info", "{node} joined Vault.", "register {id} epoch={epoch} labels={labels}"),
    "node.suspect": ("warn", "{node} is slow to answer. Checking…",
                     "φ={phi:.1f} ≥ {threshold}; last heartbeat {s:.1f}s ago"),
    "node.partitioned": ("warn", "Vault can't reach {node} directly, but {peer} can. Routing through {peer}.",
                         "{src}→{id} failed; {peer}→{id} ok; relay active"),
    "link.relayed": ("warn", "The cable between {a} and {b} looks cut. Messages now go through {relay}.",
                     "link {a}↔{b} blocked ({dir}); relay {r}"),
    "link.restored": ("success", "{a} and {b} can talk directly again.", "link {a}↔{b} ok; relay released"),
    "node.down": ("warn", "{node} stopped responding. Your files are still readable.",
                  "φ={phi:.1f}; no peer reached {id} in {confirm}s"),
    "node.recovered_in_grace": ("success", "{node} is back after {s}s. No rebuild needed.",
                                "heartbeat resumed before dead_after={x}s; avoided repair of {chunks} chunks"),
    "node.dead": ("danger", "{node} has been off for {s}s. Rebuilding its {n} copies on other machines.",
                  "DEAD; epoch {e}→{e2}; {chunks} chunks below target; incident #{inc}"),
    "node.fenced": ("warn", "{node} lost contact with Vault and stopped accepting new files, to stay safe.",
                    "lease expired {s}s ago; writes refused"),
    "node.slow": ("info", "{node} is answering slowly. Vault will read from other machines first.",
                  "{why}"),   # detector explains which rule fired (RTT or ping loss)
    "node.rejoined": ("success", "{node} is back. Removed {n} extra copies it no longer needs.",
                      "re-registered epoch {e}; kept {kept}, trimmed {trim}"),
    "node.drained": ("success", "{node} is empty and can be unplugged.", "all fragments moved; RETIRED"),
    "node.startup_discarded": ("info",
                               "{node} started up and discarded {n} unfinished write(s) from before the power cut.",
                               "removed {n} tmp/*.part"),
    "meta.recovered": ("success", "Vault's index recovered {files} files from its log in {ms} ms.",
                       "SQLite WAL recovery; {versions} committed versions; {pending} pending expired"),
    "write.quorum_failed": ("warn",
                            "{file} couldn't be saved: only {got} of {w} machines confirmed it. Nothing was saved; try again.",
                            "quorum_not_met W={w} got={got}"),
    "read.failover": ("info", "Served {n} downloads from backup copies because {node} didn't answer.",
                      "failover reads={n} from {id}"),
    "fragment.corrupt": ("warn", "Found a damaged copy of {file} on {node}. Replacing it from a healthy copy.",
                         "sha256 mismatch fid={fid} ctx={ctx}; quarantined"),
    "fragment.missing": ("warn", "A copy of {file} is missing from {node}. Rebuilding it.",
                         "fid={fid} absent in inventory"),
    "repair.started": ("info", "Rebuilding {n} copies ({size}) that were on {node}. Files closest to loss go first.",
                       "incident #{inc}: {p0} P0, {p1} P1 jobs; {mbps} MB/s cap"),
    "repair.progress": ("info", "Rebuilding: {pct}% done, about {eta}s left.", "{done}/{total} jobs; {mbps:.0f} MB/s"),
    "repair.completed": ("success", "All files are fully protected again! Recovered in {mttr:.1f}s.",
                         "detect {d:.1f}s + wait {g:.1f}s + rebuild {r:.1f}s; {bytes} moved"),
    "repair.blocked": ("warn", "Can't rebuild {n} copies yet: {reason}.", "no eligible target: {why}"),
    "repair.failed": ("danger", "Couldn't rebuild a copy of {file} after 3 tries. Vault will keep trying.",
                      "job {job} failed: {error}"),
    "scrub.completed": ("info", "Checked {n} copies on {node}. {c} damaged found.", "scrub pass {ms}ms, {mb} MB"),
    "fate.at_risk": ("danger",
                     "{n} files have all their copies on {domain}. One problem there would lose them. Moving copies now.",
                     "IFL=1 for {n} files; shared {key}={value}"),
    "fate.fixed": ("success", "{n} files now survive losing {domain}. Effective copies: {ifl}.",
                   "moves={m}; IFL {before}→{after}"),
    "fate.limited": ("warn", "{advice}", "max IFL {x} < target {t}; {constraint}"),
    "fate.cluster_wide": ("info", "All machines run {domain}. One bug there could affect all of them.",
                          "{key}={value} covers every node; excluded from scoring"),
    "rebalance.started": ("info", "{node} joined. Moving about {pct}% of your data onto it.",
                          "{moves} moves planned; ideal {ideal:.1%}"),
    "rebalance.completed": ("success", "Balanced. Moved {actual:.0%} of your data (ideal {ideal:.0%}).",
                            "{bytes} moved in {s:.1f}s"),
    "gc.cleaned": ("info", "Cleaned up {size} of leftovers from unfinished uploads and old versions.",
                   "trimmed {n} fragments"),
    "config.mode_changed": ("warn", "{human}", "safety={flags}"),   # caller passes the naive/vault sentence
    "oracle.run_started": ("info", "Durability check started ({mode} mode): {clients} clients, {duration}s of chaos.",
                           "run {id} seed={seed}"),
    "oracle.violation": ("danger", "Durability check found a problem: {sample}", "{kind} key={key} {detail}"),
    "oracle.run_completed": ("success", "Durability check finished: {faults} faults, {v} violations.",
                             "ops={ops} unknown={u}"),
}

MODE_SENTENCE = {
    "naive": "Comparison mode: safety features are off. Vault now behaves like a basic storage system.",
    "vault": "Safety features are back on.",
}

DOMAIN_NAMES = {"power": "Power Strip", "switch": "Network Switch", "disk_batch": "Disk Batch", "version": "Software"}


def domain_name(key: str, value: str) -> str:
    """power=A → "Power Strip A" (DESIGN §6.3)."""
    return f"{DOMAIN_NAMES.get(key, key)} {value}"


def fmt_bytes(n: float) -> str:
    """Base 1000 for display (DESIGN §6.4): 2.1 MB."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


class _Missing(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(type_: str, fields: dict[str, Any]) -> tuple[str, str, str]:
    """→ (severity, human, technical). Unknown types and missing fields degrade gracefully."""
    sev, human_t, tech_t = TEMPLATES.get(type_, ("info", "{human}", "{technical}"))

    def fill(t: str) -> str:
        try:
            return t.format_map(_Missing(fields))
        except (ValueError, TypeError, KeyError):
            return t
    return sev, fill(human_t), fill(tech_t)


EventSink = Callable[[dict[str, Any]], Awaitable[None]]
_sink: Optional[EventSink] = None
_name_resolver: Optional[Callable[[str], str]] = None


def set_sink(sink: EventSink) -> None:
    global _sink
    _sink = sink


def set_name_resolver(fn: Callable[[str], str]) -> None:
    """Metadata registers node_id → display name (and "meta" → "Vault index")."""
    global _name_resolver
    _name_resolver = fn


def build_event(type_: str, subject: dict[str, Any], data: Optional[dict[str, Any]] = None,
                severity: Optional[str] = None, **fields: Any) -> dict[str, Any]:
    """Event dict without `id` (the sink assigns it). Shape = models.Event."""
    data = data or {}
    f: dict[str, Any] = {**subject, **data, **fields}
    node_id = subject.get("node")
    if node_id and "node" not in fields:
        f["id"] = node_id
        if _name_resolver:
            f["node"] = _name_resolver(node_id)
    sev, human, technical = render(type_, f)
    return {"ts": time.time(), "type": type_, "severity": severity or sev, "subject": subject,
            "human": human, "technical": technical, "data": data}


async def emit(type_: str, subject: dict[str, Any], data: Optional[dict[str, Any]] = None,
               severity: Optional[str] = None, **fields: Any) -> dict[str, Any]:
    ev = build_event(type_, subject, data, severity, **fields)
    if _sink is None:
        log.info("event (no sink) %s: %s", type_, ev["human"])
    else:
        await _sink(ev)
    return ev
