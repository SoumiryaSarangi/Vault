"""Shared-Fate Auditor: audit every 5 s + on request_audit(); at_risk/limited/advice; greedy make-before-break moves.
§4.12, §8.3 FateReport, MASTER_PLAN §9 D1. Task J9. Owner: Jaiveer.
Entry points: async def run(ctx) -> None (loop), async def request_audit(ctx, reason) -> None, audit(ctx) -> FateReport.

Per live chunk: IFL (effective copies) from jaiveer_fate over durable holders. File IFL = min over chunks.
Levels: safe IFL ≥ target · limited 2 ≤ IFL < target · at_risk IFL = 1 with n ≥ 2.
Fix (greedy, one move per chunk per audit): for each holder a in the chunk's min_cut domains and each eligible
non-holder b (ALIVE, unfenced, not full), evaluate IFL(H − a + b); take the best (tie: least full b). Only if it
is strictly better (never lower IFL): enqueue move(chunk, frag_idx(a), a → b), P2 when IFL = 1, else P3 and only
if b stays under fate.improve_max_node_fill. The dispatcher does pull-to-b, verify, then delete-a.
No moves when safety.fate_aware_placement is off (Naive: report only) or the chunk has repair work in flight.
Advice (fate.limited) when no single move can reach the target, deduplicated until the layout changes:
  - D1: a chunk already spread over every eligible machine (ec42 on all 6) → one cluster-level sentence per policy.
  - otherwise the most crowded domain in the stuck chunks' min cuts: "{Power Strip A} feeds 4 of 6 machines. Give
    {Lab Laptop} or {Records Room} its own power supply to get 3 independent copies." (the suggested machines are
    the ones above that domain's fair share: the last members by id).
"""
import asyncio
import hashlib
import logging
import math
import time
from typing import Any, Optional

from vault.common.events import domain_name
from vault.common.jaiveer_fate import build_domains, ifl, target_ifl
from vault.common.models import (Advice, AtRiskFile, ClusterWideRisk, DomainRef, FateDomain, FateFiles, FateReport,
                                 JobKind, JobReason, NodeState, Policy)

log = logging.getLogger("auditor")

AUDIT_EVERY_S = 5.0
MAX_MOVES_PER_AUDIT = 200
NOT_DURABLE = (NodeState.DEAD, NodeState.RETIRED)
SUPPLY = {"power": "power supply", "switch": "network switch", "disk_batch": "disk from a different batch",
          "version": "software version"}
THING = {"power": "power strip", "switch": "network switch", "disk_batch": "disk batch", "version": "software version"}


class AuditState:
    def __init__(self):
        self.wake = asyncio.Event()
        self.report: Optional[FateReport] = None
        self.advice_sent: dict[str, str] = {}          # advice text → layout signature it was sent for
        self.cluster_wide_sent: set[tuple[str, str]] = set()
        self.at_risk_domains: dict[tuple[str, str], int] = {}   # domain → at-risk file count last audit
        self.moves_by_domain: dict[tuple[str, str], int] = {}


def _state(ctx) -> AuditState:
    st = getattr(ctx, "_audit", None)
    if st is None:
        st = ctx._audit = AuditState()
    return st


async def request_audit(ctx, reason: str) -> None:
    """Label or membership change: audit now instead of waiting for the 5 s tick."""
    _state(ctx).wake.set()


def _layout_sig(nodes) -> str:
    raw = "|".join(f"{n.id}:{n.state.value}:{sorted(n.labels.items())}" for n in sorted(nodes, key=lambda n: n.id))
    return hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()


def _fill(n) -> float:
    return n.disk_used / n.capacity if n.capacity else 0.0


async def audit(ctx) -> FateReport:
    st = _state(ctx)
    nodes = [n for n in ctx.nodes() if n.state != NodeState.RETIRED]
    by_id = {n.id: n for n in nodes}
    keys = ctx.cfg.fate.keys
    domains, cluster_wide = build_domains(nodes, keys)
    active_keys = [k for k in keys if not any(cw[0] == k for cw in cluster_wide)]
    sig = _layout_sig(nodes)

    rows = await ctx.db.fetchall(
        "SELECT o.bucket, o.key, v.version_id, v.policy, ch.chunk_id, f.frag_idx, f.node_id, f.state "
        "FROM objects o JOIN versions v ON v.version_id = o.current_version_id AND v.kind='data' AND v.state='committed' "
        "JOIN chunks ch ON ch.version_id = v.version_id LEFT JOIN fragments f ON f.chunk_id = ch.chunk_id")
    busy = {r["chunk_id"] for r in await ctx.db.fetchall(
        "SELECT DISTINCT chunk_id FROM jobs WHERE state IN ('queued','running') AND chunk_id IS NOT NULL")}

    files: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        f = files.setdefault((r["bucket"], r["key"]), {"policy": Policy.model_validate_json(r["policy"]), "chunks": {}})
        ch = f["chunks"].setdefault(r["chunk_id"], {})
        n = by_id.get(r["node_id"]) if r["node_id"] else None
        if r["state"] == "ok" and n is not None and n.state not in NOT_DURABLE:
            ch.setdefault(r["node_id"], set()).add(r["frag_idx"])

    eligible = [n for n in nodes if n.state == NodeState.ALIVE and not n.fenced and "disk_full" not in n.faults
                and not (n.capacity and n.disk_used >= n.capacity)]
    moves_allowed = ctx.safety().fate_aware_placement
    max_fill = ctx.cfg.fate.improve_max_node_fill

    hist: dict[str, int] = {}
    at_risk: list[AtRiskFile] = []
    at_risk_by_domain: dict[tuple[str, str], int] = {}
    moves: list[tuple[str, int, str, str, int, tuple[str, str]]] = []
    stuck: list[dict[str, Any]] = []            # chunks that can't reach target by a single move
    move_cache: dict[tuple, list] = {}
    planned_to: dict[str, int] = {}
    planned_from: dict[str, int] = {}

    for (bucket, key), f in files.items():
        policy: Policy = f["policy"]
        target = target_ifl(policy)
        file_ifl, file_cut, worst_holders = None, [], {}
        for cid, holders in f["chunks"].items():
            level, cut = ifl(holders, policy.needed, domains)
            if file_ifl is None or level < file_ifl:
                file_ifl, file_cut, worst_holders = level, cut, holders
            if level >= target or not holders:
                continue
            options = _best_moves(holders, policy, domains, cut, eligible, level, move_cache)
            best = (min(options, key=lambda o: (planned_to.get(o[2], 0) + planned_from.get(o[1], 0),
                                                 _fill(by_id[o[2]]), o[2], o[1])) if options else None)
            if best is None:
                stuck.append({"policy": policy, "bucket": bucket, "holders": holders, "cut": cut, "ifl": level,
                              "target": target})
                continue
            new_level, a, b, fi = best
            if not moves_allowed or cid in busy or len(moves) >= MAX_MOVES_PER_AUDIT:
                continue
            if level >= 2 and _fill(by_id[b]) >= max_fill:
                continue                                    # P3 improvement only with room to spare
            dom = next((d for d in cut if d[0] != "node"), cut[0] if cut else ("node", a))
            moves.append((cid, fi, a, b, 2 if level <= 1 else 3, dom))
            planned_to[b] = planned_to.get(b, 0) + 1          # spread equally good moves over machines
            planned_from[a] = planned_from.get(a, 0) + 1
        if file_ifl is None:                                # empty file: nothing to lose
            file_ifl = target
        hist[str(file_ifl)] = hist.get(str(file_ifl), 0) + 1
        if file_ifl <= 1 and policy.n >= 2:
            members = set(worst_holders)
            shared = [DomainRef(key=k, value=v) for (k, v), m in domains.items() if k != "node" and members <= m]
            at_risk.append(AtRiskFile(bucket=bucket, key=key, ifl=file_ifl, target=target, shared=shared))
            dom = next(((d.key, d.value) for d in shared), next((d for d in file_cut), ("node", "?")))
            at_risk_by_domain[dom] = at_risk_by_domain.get(dom, 0) + 1

    # enqueue moves (make-before-break is done by the dispatcher)
    if moves:
        from vault.metadata.jaiveer_app import enqueue_job_tx

        async def fn(c):
            for cid, fi, a, b, prio, _ in moves:
                await enqueue_job_tx(c, JobKind.move, JobReason.fate, chunk_id=cid, frag_idx=fi, priority=prio,
                                     source_node=a, target_node=b)
        await ctx.db.write(fn)
        for *_, dom in moves:
            st.moves_by_domain[dom] = st.moves_by_domain.get(dom, 0) + 1

    # events: at risk / fixed
    for dom, n in at_risk_by_domain.items():
        if st.at_risk_domains.get(dom, 0) == 0:
            await ctx.emit("fate.at_risk", {"domain": {"key": dom[0], "value": dom[1]}},
                           {"n": n, "key": dom[0], "value": dom[1]}, domain=_dname(ctx, dom))
    for dom, before in st.at_risk_domains.items():
        if before and dom not in at_risk_by_domain:
            fixed_ifl = min((int(k) for k in hist), default=0)
            await ctx.emit("fate.fixed", {"domain": {"key": dom[0], "value": dom[1]}},
                           {"n": before, "ifl": max(fixed_ifl, 2), "m": st.moves_by_domain.pop(dom, 0),
                            "before": 1, "after": max(fixed_ifl, 2)}, domain=_dname(ctx, dom))
    st.at_risk_domains = at_risk_by_domain

    # advice
    advice = _advice(ctx, stuck, domains, nodes)
    for a in advice:
        if st.advice_sent.get(a.human) != sig:
            st.advice_sent[a.human] = sig
            await ctx.emit("fate.limited", {}, {"x": a.technical.split(";")[0], "t": "", "constraint": a.technical},
                           advice=a.human)
    # cluster-wide risks
    cw_out = []
    for k, v in cluster_wide:
        human = (f"All machines run the same software version ({v}). One bug could affect all of them."
                 if k == "version" else f"All machines share {domain_name(k, v)}. One problem there affects all of them.")
        cw_out.append(ClusterWideRisk(key=k, value=v, human=human))
        if (k, v) not in st.cluster_wide_sent:
            st.cluster_wide_sent.add((k, v))
            await ctx.emit("fate.cluster_wide", {"domain": {"key": k, "value": v}}, {"key": k, "value": v},
                           domain=domain_name(k, v) if k != "version" else f"software version {v}")

    report = FateReport(
        fate_keys=active_keys,
        domains=[FateDomain(key=k, value=v, nodes=sorted(m), cluster_wide=False)
                 for (k, v), m in domains.items() if k != "node"]
                + [FateDomain(key=k, value=v, nodes=sorted(n.id for n in nodes), cluster_wide=True)
                   for k, v in cluster_wide],
        cluster_wide=cw_out,
        files=FateFiles(histogram=dict(sorted(hist.items())), at_risk=at_risk[:500]),
        advice=advice, last_audit_at=time.time())
    st.report = report
    return report


def _dname(ctx, dom: tuple[str, str]) -> str:
    k, v = dom
    return ctx.membership.display_name(v) if k == "node" else domain_name(k, v)


def _best_moves(holders: dict[str, set[int]], policy: Policy, domains, cut, eligible, level: int,
                cache: dict) -> list[tuple[int, str, str, int]]:
    """All single moves a → b that reach the highest IFL above `level`. → [(new_ifl, a, b, frag_idx)] ([] if none)."""
    key = (frozenset((n, frozenset(f)) for n, f in holders.items()), policy.needed,
           tuple(sorted(e.id for e in eligible)), tuple(cut))
    if key in cache:
        return cache[key]
    cut_nodes = set()
    for d in cut:
        cut_nodes |= domains.get(d, set()) if d[0] != "node" else {d[1]}
    candidates_a = [a for a in holders if a in cut_nodes] or list(holders)
    found: list[tuple[int, str, str, int]] = []
    for a in sorted(candidates_a):
        if len(holders[a]) != 1:
            continue                                   # a node holding several pieces: leave it to the rebalancer
        fi = next(iter(holders[a]))
        for b in eligible:
            if b.id in holders:
                continue
            h2 = {n: f for n, f in holders.items() if n != a}
            h2[b.id] = {fi}
            new = ifl(h2, policy.needed, domains)[0]
            if new > level:
                found.append((new, a, b.id, fi))
    top = max((f[0] for f in found), default=None)
    out = [f for f in found if f[0] == top]
    cache[key] = out
    return out


def _advice(ctx, stuck: list[dict[str, Any]], domains, nodes) -> list[Advice]:
    if not stuck:
        return []
    out: list[Advice] = []
    total = len([n for n in nodes if n.state not in NOT_DURABLE])
    eligible_ids = {n.id for n in nodes if n.state == NodeState.ALIVE}

    # D1: pieces already on every eligible machine (ec42 on all 6) → one sentence per policy
    seen_policies = set()
    rest = []
    for s in stuck:
        if s["policy"].type == "erasure" and eligible_ids and eligible_ids <= set(s["holders"]):
            if s["policy"].name in seen_policies:
                continue
            seen_policies.add(s["policy"].name)
            k = next((d[0] for d in s["cut"] if d[0] != "node"), "power")
            per = max((len(m & set(s["holders"])) for (dk, _), m in domains.items() if dk == k), default=1)
            bucket = s["bucket"].capitalize()
            survive = s["ifl"] - 1
            out.append(Advice(
                human=(f"{bucket} files are split into {s['policy'].n} pieces across all {total} machines, and each "
                       f"{THING.get(k, k)} feeds {per} of them. They survive losing {_count(survive)} "
                       f"{THING.get(k, k)}, but not {_count(survive + 1)}. To survive {_count(survive + 1)}, give each "
                       f"machine its own {SUPPLY.get(k, k)}."),
                technical=f"max IFL {s['ifl']} < target {s['target']}; {s['policy'].name} already on every machine; "
                          f"{k} shared by {per}"))
        else:
            rest.append(s)
    if not rest:
        return out

    # the most crowded label domain across the stuck chunks' min cuts
    counts: dict[tuple[str, str], int] = {}
    for s in rest:
        for d in s["cut"]:
            if d[0] != "node":
                counts[d] = counts.get(d, 0) + 1
    if not counts:
        return out
    dom = max(counts, key=lambda d: (len(domains.get(d, ())), counts[d], d))
    members = sorted(domains.get(dom, set()))
    values = {v for (k, v) in domains if k == dom[0]}
    fair = math.ceil(total / max(len(values), 1))
    extra = members[fair:] if len(members) > fair else members[-1:]
    names = [ctx.membership.display_name(n) for n in extra]
    target = max(s["target"] for s in rest)
    best = max(s["ifl"] for s in rest)
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
    out.append(Advice(
        human=(f"{domain_name(*dom)} feeds {len(members)} of {total} machines. Give {who} its own "
               f"{SUPPLY.get(dom[0], dom[0])} to get {target} independent copies."),
        technical=f"max IFL {best} < target {target}; {dom[0]}={dom[1]} covers {len(members)}/{total}; "
                  f"{len(rest)} chunks can't improve by one move"))
    return out


def _count(n: int) -> str:
    return {0: "no", 1: "one", 2: "two", 3: "three"}.get(n, str(n))


async def run(ctx) -> None:
    st = _state(ctx)
    while True:
        try:
            await audit(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("audit failed")
        try:
            await asyncio.wait_for(st.wake.wait(), timeout=AUDIT_EVERY_S)
        except asyncio.TimeoutError:
            pass
        st.wake.clear()
