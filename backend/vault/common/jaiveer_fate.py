"""Shared-fate domains and effective copies / IFL (ARCHITECTURE §4.12, PRD §4.1). Owner: Jaiveer. Task J3.

Pure, no I/O. Used by brain/jaiveer_auditor.py, the object-health endpoint, and Soum's
reconciler/rebalancer (never lower IFL) — keep these signatures stable.

Domain = (key, value): ("node", "n3") for a single machine, ("power", "A") for a label value.
"""
from vault.common.models import NodeView, Policy

Domain = tuple[str, str]


def build_domains(nodes: list[NodeView], keys: list[str]) -> tuple[dict[Domain, set[str]], list[Domain]]:
    """→ (domains, cluster_wide). domains: one per machine + one per label value (non-retired nodes).
    cluster_wide: label domains that contain every non-retired node; they are EXCLUDED from `domains`."""
    raise NotImplementedError("J3")


def ifl(holders: dict[str, set[int]], needed: int, domains: dict[Domain, set[str]]) -> tuple[int, list[Domain]]:
    """Effective copies of one chunk.
    holders: node_id → set of durable frag_idx it holds. needed: 1 (replication) or k (EC).
    Returns (IFL, min_cut): the smallest set of domains whose failure leaves < needed distinct frag_idx.
    Iterative deepening over domains touching the holders; depth ≤ 3 in practice. Cache by frozenset."""
    raise NotImplementedError("J3")


def target_ifl(policy: Policy) -> int:
    """n for replication, m+1 for EC."""
    raise NotImplementedError("J3")
