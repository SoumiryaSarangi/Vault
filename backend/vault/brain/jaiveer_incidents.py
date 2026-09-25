"""Incidents: open on DEAD / corruption burst / power cut / fate violation; detect/grace/repair/MTTR. §4.8. Task J8.
Owner: Jaiveer.

J6 part: ground-truth fault store (from POST /v1/incidents/fault) and open_incident_tx, used by the detector when a
node becomes DEAD. J7 fills repair_started_at / remaining_chunks / recovered_at; J8 adds GET /v1/incidents + metrics.
"""
import time
from typing import Optional

import aiosqlite

GROUND_TRUTH_MAX_AGE_S = 300.0     # a supervisor fault report older than this isn't used for fault_at


class FaultLog:
    """Latest supervisor ground truth per (kind, subject): POST /v1/incidents/fault {kind, subject, at}."""

    def __init__(self):
        self.latest: dict[tuple[str, str], float] = {}
        self.node_faults: dict[str, set[str]] = {}     # node → active chaos faults shown on its card (slow, disk_full)

    def record(self, kind: str, subject: str, at: float) -> None:
        self.latest[(kind, subject)] = at
        if kind in ("slow", "disk_full"):
            self.node_faults.setdefault(subject, set()).add(kind)
        elif kind == "clear":
            if subject in ("cluster", "all", ""):
                self.node_faults.clear()
            else:
                self.node_faults.pop(subject, None)
        elif kind == "node_start":
            self.node_faults.get(subject, set()).discard("disk_full")

    def fault_at(self, kinds: tuple[str, ...], subject: str, not_before: float) -> Optional[float]:
        """Most recent ground-truth time for subject among kinds, if it's recent and not after `not_before`."""
        now = time.time()
        best = None
        for k in kinds:
            at = self.latest.get((k, subject))
            if at is not None and now - at <= GROUND_TRUTH_MAX_AGE_S and at <= not_before + 0.5:
                best = at if best is None else max(best, at)
        return best

    def faults_for(self, node_id: str) -> list[str]:
        return sorted(self.node_faults.get(node_id, ()))


async def open_incident_tx(c: aiosqlite.Connection, kind: str, subject: Optional[str], fault_at: Optional[float],
                           detected_at: Optional[float], affected_chunks: int) -> int:
    """Inside a write fn. An incident with nothing to repair is recovered at once."""
    now = time.time()
    recovered = now if affected_chunks == 0 else None
    cur = await c.execute(
        "INSERT INTO incidents (kind, subject, fault_at, detected_at, repair_started_at, recovered_at, "
        "affected_chunks, remaining_chunks, bytes_repaired) VALUES (?,?,?,?,NULL,?,?,?,0)",
        (kind, subject, fault_at, detected_at, recovered, affected_chunks, affected_chunks))
    return cur.lastrowid
