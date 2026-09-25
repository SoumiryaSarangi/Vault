"""Per-second GatewayStats + reach row → POST /v1/participants/gw/report; failover counts. §7.2. Task S7.
Owner: Soum.

Counts are per window (Jaiveer's snapshot computes rate = count / window_s), so every report sends the
counters since the previous report and starts a new window. The report also carries the gateway's reach
row (its pinger), which the connectivity matrix and the gw↔node links need.
Availability (PRD §8): 404 and 412 count as successes, since the system answered correctly.
"""
import asyncio
import logging
import time
from collections import defaultdict

from vault.common.models import GatewayStats, OpStats, ParticipantReport
from vault.common.rpc import NetworkError, get_rpc

log = logging.getLogger("gateway.stats")


class Stats:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.by_op: dict[str, OpStats] = defaultdict(OpStats)
        self.failover: dict[str, int] = defaultdict(int)
        self.window_start = time.monotonic()

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

    def take_window(self) -> GatewayStats:
        """Counters since the last call, then start a new window."""
        window = max(1e-3, time.monotonic() - self.window_start)
        snap = self.snapshot(round(window, 3))
        self.reset()
        return snap


async def report_once(gw) -> bool:
    body = ParticipantReport(reach=dict(gw.pinger.row), stats=gw.stats.take_window())
    try:
        r = await get_rpc().request("meta", "POST", f"/v1/participants/{gw.pid}/report", json=body.model_dump())
    except NetworkError:
        return False
    return r.status_code < 300


async def run(gw) -> None:
    """Report every second (§7.2). A lost report drops that window's counts; nothing is retried."""
    while True:
        started = time.monotonic()
        try:
            await report_once(gw)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("stats report failed")
        await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - started)))
