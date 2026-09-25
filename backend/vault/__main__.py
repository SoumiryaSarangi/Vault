"""CLI: python -m vault up | reset | seed | status   (owner: Anushka)

up      start the supervisor (:7070), which starts metadata, gateway, n1..n6 and the oracle
reset   POST supervisor /cluster/reset  (wipe data, restart, create buckets, seed)
seed    POST supervisor /demo/seed
status  table of processes (supervisor /procs)
"""
import json
import sys
import urllib.error
import urllib.request

from vault.common.config import load_config


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


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "up":
        from vault.common.service import run_service
        from vault.supervisor.anushka_app import create_app
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        run_service("sup", create_app)
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
