"""CLI: python -m vault up | reset | seed | status | join   (owner: Anushka; `--lan` and `join` added by Soum)

up      start the supervisor (:7070), which starts metadata, gateway, n1..n6 and the oracle
up --lan  hub of a multi-laptop cluster: vault.lan.yaml, listens on the network (docs/soum_lan_demo.md)
reset   POST supervisor /cluster/reset  (wipe data, restart, create buckets, seed)
seed    POST supervisor /demo/seed
status  table of processes (supervisor /procs)
join    run this laptop's storage machine in the hub's cluster:
        python -m vault join --hub 192.168.1.101 [--id n2] [--name "Doctor's Desk"] [--strip B] [--agent-port 7071]
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from vault.common.config import ENV_HUB, load_config
from vault.common.soum_lan import LAN_CONFIG, detect_lan_ip


def _sup(method: str, path: str, body: dict | None = None, timeout: float = 60) -> dict:
    cfg = load_config()
    req = urllib.request.Request(cfg.url("sup") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.URLError as e:
        sys.exit(f"Vault isn't running (supervisor unreachable: {e}). Start it with `python -m vault up`.")


def _lan_hub() -> None:
    """`up --lan`: this laptop is the hub. Children inherit the env (VAULT_HUB → listen on the network)."""
    os.environ.setdefault("VAULT_CONFIG", LAN_CONFIG)
    hub = os.environ.setdefault(ENV_HUB, detect_lan_ip())
    print(f"Vault hub on {hub}. Dashboard: http://{hub}:3000/console  (run `cd web && npm run dev`)\n"
          f"On each other laptop:  python -m vault join --hub {hub} --id n2 --name \"Doctor's Desk\" --strip B",
          flush=True)


def _join(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="python -m vault join", description="Run this laptop's storage machine "
                                 "in the hub's cluster (docs/soum_lan_demo.md).")
    ap.add_argument("--hub", required=True, help="the hub laptop's IP, e.g. 192.168.1.101")
    ap.add_argument("--id", default=None, help="machine id, e.g. n2 (default: the next free one)")
    ap.add_argument("--name", default=None, help="name on the dashboard (default: \"Laptop <id>\")")
    ap.add_argument("--strip", default=None, help="power strip letter; one per laptop (default: from the id)")
    ap.add_argument("--agent-port", type=int, default=None, help="default 7071; change it to run 2 on one laptop")
    ap.add_argument("--config", default=None, help=f"default {LAN_CONFIG}")
    args = ap.parse_args(argv)
    os.environ[ENV_HUB] = args.hub
    os.environ["VAULT_CONFIG"] = args.config or os.environ.get("VAULT_CONFIG") or LAN_CONFIG
    cfg = load_config()
    node_id = args.id or _sup("GET", "/cluster/info", timeout=10)["next_node_id"]
    num = int(node_id[1:]) if node_id[1:].isdigit() else 0
    strip = (args.strip or chr(ord("A") + (num - 1) % 26)).upper()
    # Each laptop has its own power and disk; every laptop shares the hotspot (a cluster-wide risk, not scored).
    labels = {"power": strip, "switch": "hotspot", "disk_batch": f"D-{node_id}", "version": "1.0"}
    from vault.node.soum_agent import run_agent
    run_agent(cfg, node_id, args.name or f"Laptop {node_id[1:]}", labels, args.hub,
              args.agent_port or cfg.cluster.ports.agent)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "up":
        from vault.common.service import run_service
        from vault.supervisor.anushka_app import create_app
        rest = sys.argv[2:]
        if "--lan" in rest:
            rest.remove("--lan")
            _lan_hub()
        sys.argv = [sys.argv[0]] + rest
        run_service("sup", create_app)
    elif cmd == "join":
        _join(sys.argv[2:])
    elif cmd == "reset":
        print(_sup("POST", "/cluster/reset", {"seed": "--no-seed" not in sys.argv, "mode": "vault"}, timeout=120))
    elif cmd == "seed":
        print(_sup("POST", "/demo/seed", {"bucket": "clinic", "count": 200}, timeout=300))
    elif cmd == "status":
        procs = _sup("GET", "/procs")["procs"]
        print(f"{'pid':8} {'port':6} {'state':8} {'uptime':>8}  name")
        for p in procs:
            print(f"{p['pid']:8} {p['port']:<6} {p['state']:8} {p['uptime_s']:>7.0f}s  {p['display_name']}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
