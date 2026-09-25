# Vault

**Storage that heals itself.** A self-healing object store for places with a few ordinary computers and no cloud
or IT staff: checksummed chunks, copies spread across machines that don't share a power strip, failures detected
in seconds, automatic repair, and plain-language explanations. Plus two headline features: the **Shared-Fate
Auditor** and the **Durability Oracle**. See `docs/PRD.md`.

## Team start here

1. Read `docs/TEAM_PROTOCOL.md` (the law), `docs/MASTER_PLAN.md`, `docs/OWNERSHIP.md`, then your `tasks/<you>_tasks.md`.
2. Copy `tasks/claude/<you>.CLAUDE.local.md` to the repo root as `CLAUDE.local.md` (git-ignored).
3. Work on branch `<you>/<task>`; log every finished task in `updates/<you>_update.md`.

## Setup (Windows shown; macOS/Linux: `source .venv/bin/activate`)

```bash
python -m venv .venv                     # Python 3.11–3.13 (NOT 3.14: no zfec wheel)
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e . --no-deps               # makes `python -m vault` work from anywhere
python -m pytest -q                      # should be green

cd web && npm ci && npm run build && cd ..
```

## Run

```bash
python -m vault up        # supervisor :7070 → metadata :7000, gateway :7080, n1..n6 :7101-7106, oracle :7090
python -m vault status    # (second terminal) process table
cd web && npm run dev     # http://localhost:3000
```
Stop with Ctrl+C. If the supervisor is killed hard, the next `vault up` cleans up leftover processes itself.

## Layout

```
backend/vault/common/     shared contracts + infra (models.py is the frozen API contract)
backend/vault/node/       storage node        (Soum)
backend/vault/gateway/    public object API   (Soum)
backend/vault/metadata/   metadata service    (Jaiveer)
backend/vault/brain/      health brain        (Jaiveer; reconciler/gc/rebalancer: Soum)
backend/vault/oracle/     durability oracle   (Urooz)
backend/vault/supervisor/ process + chaos     (Anushka; scripts/seed: Urooz)
web/                      Next.js dashboard   (Anushka; landing: Urooz)
docs/  tasks/  updates/  handoffs/  demo/  tests/<owner>/
```
