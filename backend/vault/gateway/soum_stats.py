"""Per-second GatewayStats + reach row → POST /v1/participants/gw/report; failover counts. §7.2. Task S7.
Owner: Soum.

S5 part: the counters the PUT/GET paths record into. S7 adds the per-second report loop and reach row.
Availability (PRD §8): 404 and 412 count as successes, the system answered correctly.
"""
from collections import defaultdict

from vault.common.models import GatewayStats, OpStats


class Stats:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.by_op: dict[str, OpStats] = defaultdict(OpStats)
        self.failover: dict[str, int] = defaultdict(int)

    def record(self, op: str, ok: bool) -> None:
        s = self.by_op[op]
        if ok:
            s.ok += 1
        else:
            s.failed += 1

    def failover_read(self, node_id: str) -> None:
        """A read that node_id should have served was served elsewhere."""
        self.failover[node_id] += 1

    def snapshot(self, window_s: float = 1.0) -> GatewayStats:
        ok = sum(s.ok for s in self.by_op.values())
        failed = sum(s.failed for s in self.by_op.values())
        return GatewayStats(window_s=window_s, ok=ok, failed=failed, by_op=dict(self.by_op),
                            failover_reads=dict(self.failover))
