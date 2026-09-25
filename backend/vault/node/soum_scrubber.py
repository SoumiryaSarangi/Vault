"""Walk all .blk at scrub.rate_mbps, recent first after restart; mismatch → quarantine + POST /v1/reports/fragment. §4.9. Task S4.
Owner: Soum.

Continuous background passes paced at scrub.rate_mbps (files written in the last
scrub.recent_first_minutes first, so a restart checks what was in flight before the power cut).
POST /v1/scrub wakes the loop for an unpaced pass right away. Paused while safety.scrub is off (also on request).
The pass itself (verify → quarantine → report) is Node.scrub_pass in soum_app.py.
"""
import asyncio
import logging
import time

log = logging.getLogger("node.scrub")

MIN_PASS_INTERVAL_S = 10.0     # don't spin on a small or empty disk


async def run(node) -> None:
    node.scrub_loop = True
    try:
        while True:
            now_requested = node.scrub_wakeup.is_set()
            node.scrub_wakeup.clear()
            started = time.monotonic()
            if node.safety.scrub:                  # Naive mode: no scrubbing at all, even on request
                try:
                    await node.scrub_pass(None if now_requested else node.cfg.scrub.rate_mbps)
                    if node.scrub.corrupt_found or now_requested:
                        log.info("scrub pass: %d checked, %d damaged", node.scrub.scanned, node.scrub.corrupt_found)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("scrub pass failed")
            wait = max(1.0, MIN_PASS_INTERVAL_S - (time.monotonic() - started))
            try:
                await asyncio.wait_for(node.scrub_wakeup.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
    finally:
        node.scrub_loop = False
