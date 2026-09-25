"""Consistent hash ring with virtual nodes, fate-aware placement (ARCHITECTURE §4.11). Owner: Jaiveer. Task J2.

Pure, no I/O. Hash with vault.common.hashing.placement_hash (never Python's hash()).
Also used by Soum's brain/soum_rebalancer.py — keep these signatures stable.

Notes
- Capacity weight: a node gets round(vnodes_per_node × capacity / REF_CAPACITY_BYTES) points (min 1,
  max 8×). REF is fixed (the default 2 GiB node) so adding a bigger/smaller machine never reshuffles the
  others' points. capacity ≤ 0 (unknown) → weight 1.
- "full" is not a NodeView field; a node is full when its faults contain "disk_full" or
  capacity > 0 and disk_used ≥ capacity.
- fate_keys=None → active_fate_keys(nodes) (FATE_KEYS minus cluster-wide keys). fate_keys=[] → pure ring order.
- Two nodes "share" a key only if both carry that label with the same value (a missing label never matches).
"""
import bisect
from typing import Iterable, Iterator, Optional

from vault.common.hashing import placement_hash
from vault.common.models import NodeState, NodeView

FATE_KEYS = ["power", "switch", "disk_batch", "version"]   # most → least important

REF_CAPACITY_BYTES = 2 * 1024 ** 3     # cluster.node_capacity_bytes default; weight 1.0
_MAX_WEIGHT = 8


def _is_retired(n: NodeView) -> bool:
    return n.state == NodeState.RETIRED


def _is_full(n: NodeView) -> bool:
    if "disk_full" in n.faults:
        return True
    return n.capacity > 0 and n.disk_used >= n.capacity


def _vnode_count(n: NodeView, vnodes_per_node: int) -> int:
    if n.capacity <= 0:
        return vnodes_per_node
    w = n.capacity / REF_CAPACITY_BYTES
    return max(1, min(round(vnodes_per_node * w), vnodes_per_node * _MAX_WEIGHT))


class Ring:
    def __init__(self, vnodes_per_node: int = 128):
        self.vnodes_per_node = vnodes_per_node
        self._hashes: list[int] = []
        self._owners: list[str] = []
        self._node_ids: list[str] = []

    def rebuild(self, nodes: list[NodeView]) -> None:
        """vnodes_per_node points per non-retired node at placement_hash(f"{id}#{i}"), scaled by capacity weight."""
        points: list[tuple[int, str]] = []
        ids: list[str] = []
        for n in nodes:
            if _is_retired(n):
                continue
            ids.append(n.id)
            for i in range(_vnode_count(n, self.vnodes_per_node)):
                points.append((placement_hash(f"{n.id}#{i}"), n.id))
        points.sort()                         # (hash, id): deterministic even on hash ties
        self._hashes = [p[0] for p in points]
        self._owners = [p[1] for p in points]
        self._node_ids = sorted(set(ids))

    @property
    def node_ids(self) -> list[str]:
        return list(self._node_ids)

    def walk(self, h: int) -> Iterator[str]:
        """Distinct node ids clockwise from hash h."""
        total = len(self._hashes)
        if total == 0:
            return
        want = len(self._node_ids)
        seen: set[str] = set()
        start = bisect.bisect_left(self._hashes, h)
        for step in range(total):
            nid = self._owners[(start + step) % total]
            if nid in seen:
                continue
            seen.add(nid)
            yield nid
            if len(seen) == want:
                return


def active_fate_keys(nodes: list[NodeView], keys: list[str] = FATE_KEYS) -> list[str]:
    """keys minus cluster-wide ones (a label value held by every non-retired node)."""
    live = [n for n in nodes if not _is_retired(n)]
    if not live:
        return list(keys)
    out = []
    for k in keys:
        values = {n.labels.get(k) for n in live}
        cluster_wide = len(values) == 1 and None not in values
        if not cluster_wide:
            out.append(k)
    return out


def _shares(a: NodeView, b: NodeView, keys: list[str]) -> bool:
    if a.id == b.id:
        return True
    for k in keys:
        va, vb = a.labels.get(k), b.labels.get(k)
        if va is not None and va == vb:
            return True
    return False


def _eligible(n: NodeView, exclude: set[str]) -> bool:
    return (n.state == NodeState.ALIVE and not n.fenced and not _is_full(n) and n.id not in exclude)


def place(ring: Ring, nodes: dict[str, NodeView], chunk_id: str, count: int, holders: Iterable[str] = (),
          exclude: Optional[set[str]] = None, fate_keys: Optional[list[str]] = None) -> list[str]:
    """Up to `count` node ids in total (existing holders first). Eligible = ALIVE, not fenced, not full,
    not excluded. Relax fate constraints from least to most important (§4.11 pseudo-code).
    fate_keys=[] means pure ring order (Naive mode)."""
    exclude = exclude or set()
    if fate_keys is None:
        fate_keys = active_fate_keys(list(nodes.values()))
    chosen: list[str] = []
    for h in holders:
        if h not in chosen:
            chosen.append(h)
    if len(chosen) >= count:
        return chosen[:count]

    eligible = [nodes[nid] for nid in ring.walk(placement_hash(chunk_id))
                if nid in nodes and _eligible(nodes[nid], exclude)]
    for i in range(len(fate_keys), -1, -1):
        keys = fate_keys[:i]
        for n in eligible:
            if len(chosen) == count:
                break
            if n.id in chosen:
                continue
            if not any(_shares(n, nodes[c], keys) if c in nodes else n.id == c for c in chosen):
                chosen.append(n.id)
        if len(chosen) == count:
            break
    return chosen


def choose_additional(ring: Ring, nodes: dict[str, NodeView], chunk_id: str, holders: Iterable[str],
                      exclude: Optional[set[str]] = None, fate_keys: Optional[list[str]] = None) -> Optional[str]:
    """place(chunk_id, len(holders)+1, holders, exclude)[-1], or None if no new node is eligible."""
    held = list(dict.fromkeys(holders))
    out = place(ring, nodes, chunk_id, len(held) + 1, held, exclude, fate_keys)
    if len(out) <= len(held):
        return None
    return out[-1]
