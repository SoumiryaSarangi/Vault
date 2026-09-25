"""Order holders: available → direct before relay → not slow → lowest RTT. §4.2. Task S7.
Owner: Soum.

S5 part: order by route and slowness (the manifest only lists ok fragments on known nodes).
S7 adds node state from the topology and RTT from the gateway pinger.
"""
from typing import Optional

from vault.common.models import FragLoc


def order_holders(frags: list[FragLoc], rtt_ms: Optional[dict[str, float]] = None) -> list[FragLoc]:
    rtt_ms = rtt_ms or {}
    return sorted(frags, key=lambda f: (f.route != "direct", f.slow, rtt_ms.get(f.node_id, 0.0)))
