# Vault: Deploying on AWS

**Status:** v1.0 · **Owner:** Anushka (changes are gated, TEAM_PROTOCOL §4) · **Date:** 2026-09-26
**Read with:** `TECH_STACK.md` §4 (setup), `ARCHITECTURE.md` §1 (processes and ports), §9 (`vault.yaml`), §13 (limitations).

**In a hurry?** `docs/AWS_QUICKSTART.md` does Option A with one paste-in launch script (about 10 minutes, no domain needed).

---

## 0. Read this first

Vault is built for places **without** a cloud (PRD §2). AWS is not the product's home. We use it for two things:

1. **A hosted demo** that judges and teammates can open in a browser without our laptop.
2. **A real multi-machine test bed**, where each storage node is its own server and "turn off a machine" can mean really stopping an instance.

| | Option A: one instance | Option B: one instance per machine |
|---|---|---|
| What runs where | Everything on one EC2 instance, exactly like the laptop | Control plane on one instance, each storage node on its own instance |
| Code changes needed | **None.** Works with `main` today. | **Yes**, three small changes (§3.2) before it works |
| Chaos buttons | All work | Cut cable, slow, freeze, corrupt, disk full work; turn off / power cut need EC2 stop/start (§3.4) |
| Use it for | The hosted demo | Showing it really runs across servers (P2, after the hackathon) |

**Recommendation: Option A now. Option B after the hackathon.**

**Security, before anything else:** Vault has **no authentication** (PRD §5.4 non-goal). Anyone who can reach ports 7000–7107 can read every file, delete data, switch off safety features, and power-cut the cluster. **Never open those ports to the internet.** The only public entry point is the reverse proxy (Caddy) with a password (§2.5).

---

## 1. What gets deployed

From ARCHITECTURE §1:

| Process | Port | Started by |
|---|---|---|
| Supervisor + chaos controller | 7070 | `python -m vault up` |
| Metadata + health brain | 7000 | supervisor |
| Gateway (public object API) | 7080 | supervisor |
| Storage nodes n1–n6 | 7101–7106 | supervisor |
| Durability Oracle | 7090 | supervisor |
| Dashboard (Next.js) | 3000 | `npm start` in `web/` |

The browser talks **directly** to metadata, gateway, supervisor and Oracle (`web/lib/api.ts`). That's why every one of them has to sit behind the proxy.

---

## 2. Option A: everything on one EC2 instance (works today)

```
                 Internet (HTTPS, password)
                          │
                    ┌─────▼──────┐   EC2 instance (Ubuntu 24.04)
                    │   Caddy    │   :443 → TLS + basic auth
                    └─────┬──────┘
   /            → 127.0.0.1:3000  dashboard (next start)
   /api/meta/*  → 127.0.0.1:7000  metadata  (SSE stream)
   /api/gw/*    → 127.0.0.1:7080  gateway   (uploads/downloads)
   /api/sup/*   → 127.0.0.1:7070  supervisor (chaos buttons, reset)
   /api/oracle/*→ 127.0.0.1:7090  Oracle
                          │
          python -m vault up  (all Vault processes bound to 127.0.0.1)
                          │
                   EBS gp3 volume: /opt/vault/data (fragments, SQLite WAL)
```

Everything Vault runs stays bound to `127.0.0.1` (the default `cluster.host` in `vault.yaml`), so nothing but Caddy is reachable from outside. The browser sees one origin, so no CORS settings are needed.

### 2.1 Launch the instance

| Setting | Value | Why |
|---|---|---|
| AMI | **Ubuntu Server 24.04 LTS** (x86_64) | Ships Python 3.12, the version TECH_STACK pins. zfec has wheels for it. |
| Instance type | **c7i.xlarge** (4 vCPU, 8 GiB) or bigger | 10 Python processes plus Oracle runs are CPU-heavy. Avoid burstable `t3`/`t4g`: when CPU credits run out mid-demo, heartbeats slow down and the detector starts flagging machines. |
| Storage | 40 GiB **gp3** root volume | Fragments, SQLite, logs, `node_modules`, Next build |
| Network | Default VPC, public subnet, **Elastic IP** attached | Stable address for DNS |
| Security group | Inbound **80 and 443 from anywhere** (Caddy needs both for Let's Encrypt). **No port 22.** | Log in through **SSM Session Manager** instead of SSH |
| IAM role | `AmazonSSMManagedInstanceCore` | Needed for Session Manager |

Point a DNS name at the Elastic IP (Route 53 A record), for example `vault.<your-domain>`. Let's Encrypt needs a real domain; it often refuses the default `ec2-…amazonaws.com` names.

Check current prices on the AWS pricing page for your region. **Stop the instance when you aren't demoing.**

### 2.2 Install

Connect with Session Manager, then:

```bash
sudo apt update && sudo apt install -y git python3.12-venv caddy
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs   # Node 22 LTS

sudo mkdir -p /opt/vault && sudo chown ubuntu:ubuntu /opt/vault
git clone https://github.com/anushkaa2205/vault.git /opt/vault && cd /opt/vault

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e . --no-deps
.venv/bin/python -m pytest -q                      # must be green before going further
```

(The repo is private, so clone with a GitHub deploy key or a personal access token.)

### 2.3 Build the dashboard for the proxy paths

The dashboard reads its service URLs at build time (`NEXT_PUBLIC_*`, TECH_STACK §4). Use **relative** paths, so the same build works whatever the domain is:

```bash
cd /opt/vault/web
cat > .env.production.local <<'EOF'
NEXT_PUBLIC_META_URL=/api/meta
NEXT_PUBLIC_GATEWAY_URL=/api/gw
NEXT_PUBLIC_SUPERVISOR_URL=/api/sup
NEXT_PUBLIC_ORACLE_URL=/api/oracle
EOF
npm ci && npm run build
```

### 2.4 Run it as services (systemd)

`/etc/systemd/system/vault.service`:

```ini
[Unit]
Description=Vault cluster (supervisor starts metadata, gateway, n1-n6, oracle)
After=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/opt/vault
Environment=VAULT_DEMO=1
ExecStart=/opt/vault/.venv/bin/python -m vault up
Restart=on-failure
KillMode=control-group

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/vault-web.service`:

```ini
[Unit]
Description=Vault dashboard
After=vault.service

[Service]
User=ubuntu
WorkingDirectory=/opt/vault/web
ExecStart=/usr/bin/npm start -- -H 127.0.0.1 -p 3000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now vault vault-web
```

- `VAULT_DEMO=1` gives the 8 s "wait before rebuilding" used on stage (PRD §7). Remove it for the normal 15 s.
- `KillMode=control-group` means stopping the service also stops every child process. The supervisor also cleans up leftovers itself on the next start (`logs/children.json`).
- `-H 127.0.0.1` keeps the dashboard off the public network; only Caddy reaches it.

### 2.5 Caddy: HTTPS, password, one origin

Make a password hash with `caddy hash-password`, then write `/etc/caddy/Caddyfile`:

```caddy
vault.<your-domain> {
	basicauth {                          # Caddy 2.8+ spells this basic_auth
		team <paste-the-hash-here>
	}
	handle_path /api/meta/* {
		reverse_proxy 127.0.0.1:7000 {
			flush_interval -1            # live stream (SSE): pass events through immediately
		}
	}
	handle_path /api/gw/*     { reverse_proxy 127.0.0.1:7080 }
	handle_path /api/sup/*    { reverse_proxy 127.0.0.1:7070 }
	handle_path /api/oracle/* { reverse_proxy 127.0.0.1:7090 {
			flush_interval -1
		}
	}
	handle { reverse_proxy 127.0.0.1:3000 }
}
```

```bash
sudo systemctl reload caddy
```

`handle_path` strips the `/api/...` prefix, so each service sees its normal paths (`/v1/stream`, `/{bucket}/{key}`, …). Caddy has no upload size limit by default, so large gateway uploads work.

### 2.6 First start and checks

```bash
cd /opt/vault && .venv/bin/python -m vault reset     # 6 machines, 200 demo files, ≤ 20 s
.venv/bin/python -m vault status                     # 9 processes running
```

Open `https://vault.<your-domain>/console`, log in, and check (the same checks as MASTER_PLAN §8):

- [ ] Top bar says "Your data is safe. 6 of 6 machines are healthy." with **no** "Sample data" badge
- [ ] Effective copies **3**, 200 files
- [ ] Turn off a machine → suspect → down → rebuilt; the recovery-time tile fills in
- [ ] Cut a cable (Vault index ↔ Records Room) → relay badge, **0** repairs
- [ ] Files page: upload a file, open it, download it
- [ ] Power cut everything → overlay → back with "0 lost"

### 2.7 Operating it

| Task | How |
|---|---|
| Logs | `/opt/vault/logs/<pid>.log` (one per process) and `journalctl -u vault -u vault-web -f` |
| Reset the demo | `python -m vault reset`, or Settings → Reset demo in the dashboard |
| Deploy a new version | `git pull && .venv/bin/pip install -r requirements.txt && (cd web && npm ci && npm run build) && sudo systemctl restart vault vault-web` |
| Back up data | EBS snapshot of the volume (stop `vault` first for a clean SQLite state) |
| Save money | Stop the instance between demos (the Elastic IP stays attached) |
| Tear down | Terminate the instance, delete its volumes and snapshots, release the Elastic IP, delete the DNS record |

---

## 3. Option B: one EC2 instance per machine (after the hackathon)

This is the answer to the judge question "Does this run on real machines?" (PRD §10). The design already allows it: everything is HTTP between addresses in `vault.yaml`, and nodes already advertise `nodes.<id>.addr`. But three pieces of today's code assume one host (§3.2).

### 3.1 Layout

```
            ┌────────────── VPC, one region ───────────────┐
 Internet ─▶│ control (c7i.large): Caddy, dashboard,        │
            │   metadata :7000, gateway :7080, oracle :7090,│
            │   supervisor :7070                            │
            │                                               │
            │ AZ a:  n1 (t3.small)   n2 (t3.small)   power A│
            │ AZ b:  n3              n4              power B│
            │ AZ c:  n5              n6              power C│
            └───────────────────────────────────────────────┘
   each node: its own gp3 EBS volume for data/, reached at its private IP:710x
```

- **Fate labels.** Put the two machines of each power strip in the same Availability Zone. `power=A` then really means "AZ a", and the Shared-Fate Auditor audits a real failure domain. Keep `switch` and `disk_batch` from `vault.yaml`. Be honest in the pitch: an AZ is far more independent than a power strip, so this illustrates the idea rather than reproducing a clinic.
- **Network.** One security group `vault-internal` that allows TCP 7000–7110 **from itself only**. Only the control instance also gets 80/443 for Caddy.
- **Costs.** Traffic between AZs is billed. Oracle runs and rebuilds copy a lot of data, so keep test runs short.

### 3.2 Code changes needed first (owners)

| # | Change | Why | Owner | Size |
|---|---|---|---|---|
| 1 | Split the **bind address** from the **advertised address**: add `cluster.bind_host` (e.g. `0.0.0.0`) used by `service.run_service`, and optional `addr` for `meta`, `gw`, `oracle` and `sup` in `vault.yaml`, used by `config.addr()` | Today one `cluster.host` is both the bind address and every control-plane address, so `0.0.0.0` would break service-to-service calls | Anushka (`common/config.py`, `common/service.py`) | ~30 lines + tests |
| 2 | Supervisor **roles**: `python -m vault up --role control` starts only metadata, gateway, Oracle and the supervisor; each node host runs `python -m vault.node --id nX` under its own systemd unit | The supervisor spawns every process locally today (`anushka_procs.py`) | Anushka (`supervisor/anushka_procs.py`, `__main__.py`) | ~40 lines |
| 3 | **Remote on/off** for chaos: turn-off and power-cut call SSM (`systemctl stop vault-node`) or EC2 `StopInstances` / `StartInstances` for nodes on other hosts | The supervisor can only kill its own child processes | Anushka (`supervisor/anushka_chaos_ctl.py`) | ~60 lines; needs an IAM role |

These are gated changes (config, folder structure, deployment). They need Anushka's approval and a merge point, and none of them belongs in the hackathon build.

### 3.3 `vault.yaml` for Option B (after change 1)

```yaml
cluster:
  bind_host: 0.0.0.0             # new (change 1)
  data_dir: /opt/vault/data
control:                          # new (change 1): advertised control-plane addresses
  meta:   { addr: "10.0.1.10:7000" }
  gw:     { addr: "10.0.1.10:7080" }
  oracle: { addr: "10.0.1.10:7090" }
  sup:    { addr: "10.0.1.10:7070" }
nodes:
  n1: { display_name: "Reception PC", addr: "10.0.1.21:7101", labels: { power: A, switch: S1, disk_batch: D1, version: "1.0" } }
  n2: { display_name: "Doctor's Desk", addr: "10.0.1.22:7102", labels: { power: A, switch: S2, disk_batch: D2, version: "1.0" } }
  # … n3–n6 in AZ b and c, same labels as today
web:
  origin: "https://vault.<your-domain>"
```

### 3.4 What works across machines, and what doesn't

| Works as-is | Why |
|---|---|
| Upload, download, repair, scrub, relay, fencing, the auditor | Everything goes through `rpc.py` to the addresses in `vault.yaml` |
| Cut cable, slow, freeze, corrupt, disk full | These are `/_chaos/*` calls on the target process, which work over the network |
| Real power cut | **Stop the EC2 instance** in the console. That's a genuinely harder test than killing a process: the OS page cache is really lost (PRD §10 caveat) |

| Needs change 3 | Why |
|---|---|
| "Turn off" and "Power cut" buttons for nodes on other hosts | The supervisor can only kill local child processes |
| Oracle chaos scripts that crash nodes | Same reason |

---

## 4. Why not other AWS services

| Option | Why not |
|---|---|
| **S3** as storage | Vault *is* the object store. Putting bytes in S3 hides the exact failures we demonstrate (REFORGE plan §4, §23). |
| **ECS / EKS / Docker** | PRD §5.4 non-goal ("Kubernetes or a Docker requirement"). The supervisor manages processes itself, and the demo depends on killing individual processes. |
| **RDS** for metadata | Metadata is SQLite in WAL mode by design (ARCHITECTURE D1). Crash-safe and one file, with no server to run. |
| **Lambda / serverless** | Nodes keep state on disk and run background loops (heartbeats, scrubber). They need long-lived processes. |

---

## 5. Known limitations

1. **No authentication** in Vault itself. The Caddy password is the only lock (§0).
2. **One metadata service and one gateway** (ARCHITECTURE §13). If the control instance goes down, writes stop until it's back.
3. **Option B isn't implemented** until the three changes in §3.2 are made.
4. Timings measured on a Windows laptop (kill → rebuilt in about 13 s) will differ on EC2. Re-run the kill test after deploying, and tune `repair.per_node` or `bandwidth_mbps` if needed.
