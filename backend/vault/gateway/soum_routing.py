"""Order holders: available → direct before relay → not slow → lowest RTT. §4.2. Task S7.
Owner: Soum.

  available  node state ALIVE / SUSPECT / PARTITIONED (§3.4), from the metadata snapshot the gateway pinger
             fetches. A holder with no known state yet counts as available. DOWN/DEAD/… holders go last, so a
             read doesn't wait ~2 s for a crashed machine's refused connection (Windows) before failing over.
  route      the manifest's route ("direct" before "relay:<n>")
  slow       the manifest's slow flag
  RTT        the gateway pinger's latest measurement (unknown = 0, i.e. no penalty)
Hedged reads are P1 and not built.
"""
from typing import Optional

from vault.common.models import FragLoc

AVAILABLE = {"ALIVE", "SUSPECT", "PARTITIONED"}


def order_holders(frags: list[FragLoc], rtt_ms: Optional[dict[str, float]] = None,
                  states: Optional[dict[str, str]] = None) -> list[FragLoc]:
    rtt_ms = rtt_ms or {}
    states = states or {}

    def key(f: FragLoc):
        state = states.get(f.node_id)
        return (state is not None and state not in AVAILABLE, f.route != "direct", f.slow,
                rtt_ms.get(f.node_id, 0.0))
    return sorted(frags, key=key)
