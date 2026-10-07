# Vault

**Storage that heals itself.**

Vault is a self-healing object store for places that have a few ordinary computers and no cloud or IT staff: a clinic, a school, a factory floor. It splits files into checksummed chunks, spreads copies across machines that don't share a power strip, detects failures in seconds, repairs itself, and explains every action in plain language.

> Built in 20 hours at a hackathon by a team of four. Everyone counts copies. Vault checks whether they can **die together**, and keeps its own passbook to show nothing was lost.

---

## What makes it different

| | |
|---|---|
| **Shared-Fate Auditor** | "3 copies on 3 machines" isn't "3 independent copies" if all 3 share one power strip. Vault labels every machine (`power`, `switch`, `disk_batch`, `version`), scores each file by how many independent failures it takes to lose it (*effective copies*), places new writes to avoid shared dependencies, and moves existing copies when the score is low. |
| **Durability Oracle** | An independent test client runs uploads, downloads and deletes during a scripted chaos run and keeps its own ledger. It checks for lost, damaged, resurrected and stale data. Vault never grades its own homework. A "Naive" mode (safety switches off) runs the same script side by side. |
| **"Can't reach" is not "dead"** | Detects partial network partitions and routes around them through a relay instead of rebuilding data on a machine that's alive. |
| **Plain-language operations** | Every event reads like a sentence a clinic manager understands, and **Explain** mode shows the technical reason underneath. |
| **Measured, not claimed** | Effective copies, recovery time, availability and storage overhead are live numbers on the dashboard. |

Durability policies per bucket: `rep2`, `rep3` (default) and `ec42` (Reed-Solomon 4+2 erasure coding, 1.5× overhead).

---

## How it fits together

```
  Browser ── Dashboard (Next.js :3000)
                │
   Supervisor :7070 ── starts and chaos-controls everything below
                │
   Gateway :7080 ── object API (PUT/GET/HEAD/DELETE /{bucket}/{key})
        │                │
   Metadata + health brain :7000     Oracle :7090
   (SQLite: source of truth,         (independent ledger,
    failure detector, repair,         chaos scripts)
    Shared-Fate Auditor)
        │
   Storage nodes n1…nN :7101…
```

Everything talks HTTP, so the same code runs on one laptop (simulated machines) or across real laptops.

---

## Quick start (one laptop, 6 simulated machines)

**Needs:** Python 3.11–3.13 (not 3.14: no `zfec` wheel), Node.js 20+.

```bash
python -m venv .venv
.venv\Scripts\activate                   # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -e . --no-deps               # makes `python -m vault` work from anywhere
python -m pytest -q                      # should be green

cd web && npm ci && cd ..
```

Run it (two terminals):

```bash
python -m vault up                       # supervisor, metadata, gateway, 6 nodes, oracle
cd web && npm run dev                    # dashboard at http://localhost:3000
```

Useful commands:

```bash
python -m vault status                   # table of processes
python -m vault reset                    # wipe data, restart, create buckets, seed 200 demo files
```

Stop with Ctrl+C. If the supervisor is killed hard, the next `vault up` cleans up leftover processes.

### Using the dashboard

Open **http://localhost:3000/console**.

- **Overview:** the machines (left), a map of the cluster (centre), and a live event timeline (right). Press **E** for Explain mode and **P** for presenter mode.
- **Files:** upload files, see where each file's pieces live and its effective copies.
- **Fate:** what the machines share (power strips, switches, disk batches), relabel a machine, see at-risk files and advice.
- **Check:** run the Durability Oracle in Vault vs Naive mode and compare violations.
- **Break something** (bottom bar): **Turn off**, **Damage** copies, **Cut cable**, **Slow**, **Freeze**, **Power cut**, **Move plug**, **Add** a machine, **Clear**. Hover a machine card and click the pencil to **rename** it.

### Using the object API

```bash
curl -T photo.jpg http://127.0.0.1:7080/clinic/photo.jpg    # upload
curl http://127.0.0.1:7080/clinic/photo.jpg -o back.jpg     # download
curl http://127.0.0.1:7080/clinic                           # list
```

---

## The 4-laptop demo (real machines)

The headline demo: **four physical laptops, one storage node each.** Close one laptop's lid and Vault detects it, rebuilds that laptop's copies on the others, and the dashboard shows every step. Open the lid and it rejoins by itself.

```powershell
# Laptop 1 (the hub)
python -m vault up --lan                  # prints the hub's IP
cd web; npm run dev

# Laptops 2, 3, 4: one line each, with the hub's IP
python -m vault join --hub <HUB-IP> --id n2 --name "Doctor's Desk" --strip B
python -m vault join --hub <HUB-IP> --id n3 --name "Lab Laptop"    --strip C
python -m vault join --hub <HUB-IP> --id n4 --name "Pharmacy PC"   --strip D
```

Then open `http://<HUB-IP>:3000/console` and run `python -m vault reset` on the hub. In the dashboard, **Add → Real laptop** gives the join command for a fifth laptop, and **Add → Simulated** adds a machine on the hub if you have no spare device.

Full setup (firewall, lid settings, hotspot), demo scenes and troubleshooting: **[docs/soum_lan_demo.md](docs/soum_lan_demo.md)**.

---

## Configuration

- `vault.yaml`: one-laptop cluster (6 machines, policies, failure-detector timings, safety switches).
- `vault.lan.yaml`: multi-laptop cluster (the hub's own machine only; the others join at runtime).
- `VAULT_DEMO=1`: failure is declared after 8 s instead of 15 s, for stage demos.

---

## Repository layout

```
backend/vault/common/     shared contracts and infrastructure (models.py is the API contract)
backend/vault/node/       storage node and the LAN node agent
backend/vault/gateway/    public object API
backend/vault/metadata/   metadata service (SQLite, source of truth)
backend/vault/brain/      failure detector, repair, auditor, reconciler, GC, rebalancer
backend/vault/oracle/     durability oracle
backend/vault/supervisor/ process control and chaos controller
web/                      Next.js dashboard and landing page
docs/                     PRD, architecture, design, tech stack, runbooks
tests/, backend/vault/tests/
```

## Documentation

| Doc | What's in it |
|---|---|
| [docs/PRD.md](docs/PRD.md) | The problem, the users, the features, the demo script |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it works: commit protocol, failure detection, repair, fate auditing, API |
| [docs/TECH_STACK.md](docs/TECH_STACK.md) | Libraries, setup and conventions |
| [docs/DESIGN.md](docs/DESIGN.md) | Visual design and the plain-language copy rules |
| [docs/soum_lan_demo.md](docs/soum_lan_demo.md) | The 4-laptop setup and demo runbook |
| [docs/AWS_QUICKSTART.md](docs/AWS_QUICKSTART.md) | Hosted demo on one EC2 instance |
| [docs/MASTER_PLAN.md](docs/MASTER_PLAN.md) | Hackathon plan, milestones and decisions |

## Tech

Python 3.11+, FastAPI, httpx, SQLite (aiosqlite), `zfec` (Reed-Solomon), pytest · Next.js, React, Tailwind, React Three Fiber, GSAP.

## Limitations (it's a hackathon project)

- **No authentication or users.** Anyone who can reach the gateway can read and delete files. Don't expose it to the internet without a password in front (see the AWS quickstart).
- **One metadata service** is the scaling ceiling (tested at thousands of objects) and a single point of failure for control. Storage machines fail and heal; the index doesn't replicate.
- Latest version of each object only, no multipart upload, not S3-compatible.

## Team

Anushka (lead, supervisor, dashboard) · Jaiveer (metadata and health brain) · Soum (storage nodes, gateway, data-plane repair, LAN mode) · Urooz (Durability Oracle, demo, landing page).
