"""Shared-fate domains and effective copies / IFL (ARCHITECTURE §4.12, PRD §4.1). Owner: Jaiveer. Task J3.

Pure, no I/O. Used by brain/jaiveer_auditor.py, the object-health endpoint, and Soum's
reconciler/rebalancer (never lower IFL) — keep these signatures stable.

Domain = (key, value): ("node", "n3") for a single machine, ("power", "A") for a label value.

IFL (independent failure level, "effective copies") = the smallest number of domains whose simultaneous
failure leaves fewer than `needed` distinct frag_idx. The set found is the chunk's `min_cut`.
Search: iterative deepening over the domains that touch the holders; domains with an identical footprint
on the holders are collapsed (first one kept: label domains before node domains, most important key first),
so a min_cut names "Power Strip A" rather than "Reception PC" when both would do.
Cache: keyed by frozenset of the holders plus each touching domain's footprint on them, so a relabel
can never return a stale answer.
"""
from functools import lru_cache
from itertools import combinations

from vault.common.models import NodeState, NodeView, Policy

Domain = tuple[str, str]


def build_domains(nodes: list[NodeView], keys: list[str]) -> tuple[dict[Domain, set[str]], list[Domain]]:
    """→ (domains, cluster_wide). domains: one per machine + one per label value (non-retired nodes).
    cluster_wide: label domains that contain every non-retired node; they are EXCLUDED from `domains`."""
    live = [n for n in nodes if n.state != NodeState.RETIRED]
    all_ids = {n.id for n in live}
    domains: dict[Domain, set[str]] = {}
    cluster_wide: list[Domain] = []
    for k in keys:
        by_value: dict[str, set[str]] = {}
        for n in live:
            v = n.labels.get(k)
            if v is not None:
                by_value.setdefault(v, set()).add(n.id)
        for v in sorted(by_value):
            members = by_value[v]
            if members == all_ids:
                cluster_wide.append((k, v))
            else:
                domains[(k, v)] = members
    for n in live:
        domains[("node", n.id)] = {n.id}
    return domains, cluster_wide


def _distinct(holders: tuple[tuple[str, frozenset[int]], ...], dead: frozenset[str]) -> int:
    frags: set[int] = set()
    for nid, idxs in holders:
        if nid not in dead:
            frags |= idxs
    return len(frags)


@lru_cache(maxsize=8192)
def _ifl_cached(holders: frozenset, needed: int, touching: tuple) -> tuple[int, tuple[Domain, ...]]:
    """holders: frozenset[(node_id, frozenset[frag_idx])]; touching: ((domain, frozenset[holder ids]), ...)
    in preference order, already deduplicated by footprint."""
    h = tuple(sorted(holders, key=lambda x: x[0]))
    if _distinct(h, frozenset()) < needed:
        return 0, ()
    for depth in range(1, len(touching) + 1):
        for combo in combinations(touching, depth):
            dead = frozenset().union(*(fp for _, fp in combo))
            if _distinct(h, dead) < needed:
                return depth, tuple(d for d, _ in combo)
    # Unreachable when node domains are present (removing every holder always loses the chunk).
    return len(touching), tuple(d for d, _ in touching)


def ifl(holders: dict[str, set[int]], needed: int, domains: dict[Domain, set[str]]) -> tuple[int, list[Domain]]:
    """Effective copies of one chunk.
    holders: node_id → set of durable frag_idx it holds. needed: 1 (replication) or k (EC).
    Returns (IFL, min_cut): the smallest set of domains whose failure leaves < needed distinct frag_idx.
    Iterative deepening over domains touching the holders; depth ≤ 3 in practice. Cache by frozenset."""
    hset = frozenset((nid, frozenset(idxs)) for nid, idxs in holders.items() if idxs)
    holder_ids = {nid for nid, _ in hset}
    # Preference order: label domains (in dict order: most important key first), then single machines.
    ordered = sorted(domains.items(), key=lambda kv: kv[0][0] == "node")
    seen: set[frozenset[str]] = set()
    touching = []
    for d, members in ordered:
        fp = frozenset(members & holder_ids)
        if not fp or fp in seen:
            continue
        seen.add(fp)
        touching.append((d, fp))
    # Holders on nodes no domain covers (e.g. retired): give each its own node domain so IFL stays finite.
    covered = frozenset().union(*(fp for _, fp in touching)) if touching else frozenset()
    for nid in sorted(holder_ids - covered):
        touching.append((("node", nid), frozenset({nid})))
    level, cut = _ifl_cached(hset, needed, tuple(touching))
    return level, list(cut)


def ifl_cache_info():
    """lru_cache stats (hits/misses) for the IFL search, for tests and /v1/metrics debugging."""
    return _ifl_cached.cache_info()


def target_ifl(policy: Policy) -> int:
    """n for replication, m+1 for EC."""
    if policy.type == "erasure":
        return (policy.m or 0) + 1
    return policy.n
