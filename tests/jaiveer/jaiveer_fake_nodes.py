"""Fake storage nodes for testing metadata before Soum's real heartbeats land (S4). Owner: Jaiveer. Task J4.

Registers n1–n6 (from vault.yaml) with metadata and heartbeats each one every detector.heartbeat_ms.
A heartbeat reply of state DEAD makes that fake node re-register, like a real node (§4.6).
One Rpc per fake node, so each request carries the right X-Vault-From.

    python tests/jaiveer/jaiveer_fake_nodes.py                 # all six, forever
    python tests/jaiveer/jaiveer_fake_nodes.py --stop n3 --after 5   # n3 goes silent after 5 s (kill test, J6)
    python tests/jaiveer/jaiveer_fake_nodes.py --nodes n1,n2 --seconds 10

Run it from the repo root with the venv active; metadata must be up (python -m vault up, or
python -m vault.metadata). Don't run it next to real nodes: they'd fight over the same ids.
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from vault.common.config import load_config, node_identity  # noqa: E402
from vault.common.models import Heartbeat, HeartbeatReply, RegisterReply, RegisterRequest  # noqa: E402
from vault.common.netsim import NetSim  # noqa: E402
from vault.common.rpc import NetworkError, Rpc  # noqa: E402


async def fake_node(cfg, node_id: str, stop_at: float | None, until: float | None, verbose: bool) -> None:
    rpc = Rpc(node_id, cfg, NetSim(node_id))
    name, labels = node_identity(cfg, node_id)
    epoch = None
    period = cfg.detector.heartbeat_ms / 1000
    try:
        while until is None or time.monotonic() < until:
            if stop_at is not None and time.monotonic() >= stop_at:
                print(f"[{node_id}] going silent (simulated crash)")
                return
            try:
                if epoch is None:
                    req = RegisterRequest(node_id=node_id, addr=cfg.addr(node_id), display_name=name, labels=labels,
                                          capacity_bytes=cfg.cluster.node_capacity_bytes)
                    r = await rpc.request("meta", "POST", "/v1/nodes/register", json=req.model_dump(mode="json"))
                    if r.status_code == 200:
                        reply = RegisterReply.model_validate(r.json())
                        epoch = reply.epoch
                        print(f"[{node_id}] registered: epoch={epoch} state={reply.state.value}")
                else:
                    hb = Heartbeat(node_id=node_id, epoch=epoch, disk_used=0,
                                   capacity=cfg.cluster.node_capacity_bytes, fragments=0)
                    r = await rpc.request("meta", "POST", f"/v1/nodes/{node_id}/heartbeat",
                                          json=hb.model_dump(mode="json"))
                    if r.status_code == 200:
                        reply = HeartbeatReply.model_validate(r.json())
                        if verbose:
                            print(f"[{node_id}] hb → {reply.state.value}")
                        if reply.state.value == "DEAD":
                            print(f"[{node_id}] metadata says DEAD → re-registering")
                            epoch = None
                            continue
            except NetworkError as e:
                if verbose:
                    print(f"[{node_id}] metadata unreachable: {e.reason}")
            await asyncio.sleep(period)
    finally:
        await rpc.close()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", default="", help="comma-separated ids (default: every node in vault.yaml)")
    ap.add_argument("--stop", default="", help="node id that goes silent after --after seconds")
    ap.add_argument("--after", type=float, default=5.0)
    ap.add_argument("--seconds", type=float, default=None, help="stop everything after this many seconds")
    ap.add_argument("--config", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    ids = [n for n in a.nodes.split(",") if n] or cfg.node_ids()
    t0 = time.monotonic()
    until = t0 + a.seconds if a.seconds else None
    await asyncio.gather(*(fake_node(cfg, n, t0 + a.after if n == a.stop else None, until, a.verbose) for n in ids))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
