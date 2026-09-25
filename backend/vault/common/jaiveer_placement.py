"""Consistent hash ring with virtual nodes, fate-aware placement (ARCHITECTURE §4.11). Owner: Jaiveer. Task J2.

Pure, no I/O. Hash with vault.common.hashing.placement_hash (never Python's hash()).
Also used by Soum's brain/soum_rebalancer.py — keep these signatures stable.
"""
from typing import Iterable, Iterator, Optional

from vault.common.models import NodeView

FATE_KEYS = ["power", "switch", "disk_batch", "version"]   # most → least important


class Ring:
    def __init__(self, vnodes_per_node: int = 128):
        raise NotImplementedError("J2")

    def rebuild(self, nodes: list[NodeView]) -> None:
        """vnodes_per_node points per non-retired node at placement_hash(f"{id}#{i}"), scaled by capacity weight."""
        raise NotImplementedError("J2")

    def walk(self, h: int) -> Iterator[str]:
        """Distinct node ids clockwise from hash h."""
        raise NotImplementedError("J2")


def active_fate_keys(nodes: list[NodeView], keys: list[str] = FATE_KEYS) -> list[str]:
    """keys minus cluster-wide ones (a label value held by every non-retired node)."""
    raise NotImplementedError("J2")


def place(ring: Ring, nodes: dict[str, NodeView], chunk_id: str, count: int, holders: Iterable[str] = (),
          exclude: Optional[set[str]] = None, fate_keys: Optional[list[str]] = None) -> list[str]:
    """Up to `count` node ids in total (existing holders first). Eligible = ALIVE, not fenced, not full,
    not excluded. Relax fate constraints from least to most important (§4.11 pseudo-code).
    fate_keys=[] means pure ring order (Naive mode)."""
    raise NotImplementedError("J2")


def choose_additional(ring: Ring, nodes: dict[str, NodeView], chunk_id: str, holders: Iterable[str],
                      exclude: Optional[set[str]] = None, fate_keys: Optional[list[str]] = None) -> Optional[str]:
    """place(chunk_id, len(holders)+1, holders, exclude)[-1], or None if no new node is eligible."""
    raise NotImplementedError("J2")
