"""Summary level + sentence for the snapshot (ARCHITECTURE §8.2, DESIGN §6.2). Task J8 (first version in J6).
Owner: Jaiveer.

Level: critical if any live file is unreadable now; else at_risk if any chunk is one failure from loss (durable ≤
needed) or has IFL 1 (n ≥ 2); else degraded if any chunk is below target (durable < n); else ok.
"durable" = ok rows on nodes not DEAD/RETIRED; "available" = ok rows on ALIVE/SUSPECT/PARTITIONED/DRAINING nodes.
"""
from dataclasses import dataclass, field
from typing import Any, Optional

from vault.common.events import MODE_SENTENCE
from vault.common.jaiveer_fate import build_domains, ifl
from vault.common.models import NodeState, Policy, Summary

NOT_DURABLE = {NodeState.DEAD, NodeState.RETIRED}
AVAILABLE = {NodeState.ALIVE, NodeState.SUSPECT, NodeState.PARTITIONED, NodeState.DRAINING}


@dataclass
class FileHealth:
    bucket: str
    key: str
    policy: Policy
    size: int
    min_durable: int
    min_ifl: int
    unreadable: bool
    at_risk: bool
    below_target: bool
    offline_holders: set[str] = field(default_factory=set)


async def file_health(ctx) -> list[FileHealth]:
    """One pass over every live file (current committed data version)."""
    db = ctx.db
    vers = await db.fetchall(
        "SELECT o.bucket, o.key, v.version_id, v.size, v.policy FROM objects o "
        "JOIN versions v ON v.version_id = o.current_version_id WHERE v.kind='data' AND v.state='committed'")
    if not vers:
        return []
    chunks = await db.fetchall(
        "SELECT ch.version_id, ch.chunk_id FROM chunks ch JOIN objects o ON o.current_version_id = ch.version_id")
    frags = await db.fetchall(
        "SELECT f.chunk_id, f.frag_idx, f.node_id, ch.frag_size FROM fragments f "
        "JOIN chunks ch ON ch.chunk_id = f.chunk_id JOIN objects o ON o.current_version_id = ch.version_id "
        "WHERE f.state = 'ok'")
    states = {n.id: n.state for n in ctx.nodes()}
    domains = build_domains(ctx.nodes(), ctx.cfg.fate.keys)[0]
    by_v: dict[str, list[str]] = {}
    for ch in chunks:
        by_v.setdefault(ch["version_id"], []).append(ch["chunk_id"])
    by_c: dict[str, list[dict[str, Any]]] = {}
    for f in frags:
        by_c.setdefault(f["chunk_id"], []).append(f)

    out = []
    for v in vers:
        policy = Policy.model_validate_json(v["policy"])
        fh = FileHealth(bucket=v["bucket"], key=v["key"], policy=policy, size=v["size"], min_durable=policy.n,
                        min_ifl=10 ** 6, unreadable=False, at_risk=False, below_target=False)
        for cid in by_v.get(v["version_id"], []):
            holders: dict[str, set[int]] = {}
            available: set[int] = set()
            for f in by_c.get(cid, []):
                st = states.get(f["node_id"])
                if st is None or st in NOT_DURABLE:
                    continue
                holders.setdefault(f["node_id"], set()).add(f["frag_idx"])
                if st in AVAILABLE:
                    available.add(f["frag_idx"])
                else:
                    fh.offline_holders.add(f["node_id"])
            durable = len(set().union(*holders.values())) if holders else 0
            level = ifl(holders, policy.needed, domains)[0]
            fh.min_durable = min(fh.min_durable, durable)
            fh.min_ifl = min(fh.min_ifl, level)
            if len(available) < policy.needed:
                fh.unreadable = True
            if durable <= policy.needed or (level <= 1 and policy.n >= 2):
                fh.at_risk = True
            if durable < policy.n:
                fh.below_target = True
        if fh.min_ifl == 10 ** 6:           # empty file
            fh.min_ifl = policy.n if policy.type == "replication" else (policy.m or 0) + 1
        out.append(fh)
    return out


async def raw_bytes(ctx) -> int:
    rows = await ctx.db.fetchall(
        "SELECT f.node_id, ch.frag_size FROM fragments f JOIN chunks ch ON ch.chunk_id = f.chunk_id "
        "JOIN objects o ON o.current_version_id = ch.version_id WHERE f.state='ok'")
    states = {n.id: n.state for n in ctx.nodes()}
    return sum(r["frag_size"] for r in rows if states.get(r["node_id"]) not in NOT_DURABLE and r["node_id"] in states)


def _names(ctx, ids: set[str]) -> str:
    names = sorted(ctx.membership.display_name(i) for i in ids)
    if not names:
        return "the missing machines"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


async def build_summary(ctx, files: Optional[list[FileHealth]] = None, repair_pct: Optional[int] = None) -> Summary:
    files = files if files is not None else await file_health(ctx)
    nodes = [n for n in ctx.nodes() if n.state != NodeState.RETIRED]
    alive = sum(1 for n in nodes if n.state in (NodeState.ALIVE, NodeState.PARTITIONED, NodeState.DRAINING))
    logical = sum(f.size for f in files)
    raw = await raw_bytes(ctx)
    unreadable = [f for f in files if f.unreadable]
    at_risk = [f for f in files if f.at_risk]
    below = [f for f in files if f.below_target]
    under_chunks = await _under_chunks(ctx)
    min_ifl = min((f.min_ifl for f in files), default=0)

    if unreadable:
        level = "critical"
        off = set().union(*(f.offline_holders for f in unreadable))
        human = (f"{len(unreadable)} files can't be read right now because the machines holding them are off. "
                 f"Turn {_names(ctx, off)} back on.")
    elif at_risk:
        level = "at_risk"
        human = (f"{len(at_risk)} files are one failure away from loss. Vault is rebuilding them first. "
                 f"Please don't turn off other machines.")
    elif below:
        level = "degraded"
        human = (f"Your data is safe, but {len(below)} files have fewer copies than usual. "
                 f"Vault is rebuilding them ({repair_pct or 0}% done).")
    else:
        level = "ok"
        human = f"Your data is safe. {alive} of {len(nodes)} machines are healthy."
    if ctx.mode == "naive":
        human = MODE_SENTENCE["naive"] + " " + human
    return Summary(level=level, human=human, technical=f"min IFL {min_ifl}; {under_chunks} chunks below target",
                   files=len(files), logical_bytes=logical, raw_bytes=raw,
                   overhead=round(raw / logical, 2) if logical else 0.0, under_replicated_chunks=under_chunks,
                   at_risk_files=len(at_risk), unreadable_files=len(unreadable), min_ifl=min_ifl)


async def _under_chunks(ctx) -> int:
    rows = await ctx.db.fetchall(
        "SELECT ch.chunk_id, v.policy, f.frag_idx, f.node_id FROM chunks ch "
        "JOIN objects o ON o.current_version_id = ch.version_id "
        "JOIN versions v ON v.version_id = ch.version_id AND v.kind='data' AND v.state='committed' "
        "LEFT JOIN fragments f ON f.chunk_id = ch.chunk_id AND f.state='ok'")
    states = {n.id: n.state for n in ctx.nodes()}
    need: dict[str, int] = {}
    have: dict[str, set[int]] = {}
    for r in rows:
        if r["chunk_id"] not in need:
            need[r["chunk_id"]] = Policy.model_validate_json(r["policy"]).n
            have[r["chunk_id"]] = set()
        if r["node_id"] is not None and r["node_id"] in states and states[r["node_id"]] not in NOT_DURABLE:
            have[r["chunk_id"]].add(r["frag_idx"])
    return sum(1 for cid, n in need.items() if len(have[cid]) < n)
