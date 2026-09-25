# Jaiveer: Control plane ("the brain")

You own the metadata service, the source of truth (SQLite WAL), and the health brain that runs inside it: failure detection, repair scheduling and the **Shared-Fate Auditor** (a headline feature). You're on the critical path (MASTER_PLAN §4): **J4 → J5 → J6 → J7** decides whether M3 (the kill-a-machine demo) lands at H9.

**Read first (only these):** TEAM_PROTOCOL.md · MASTER_PLAN.md · OWNERSHIP.md · docs/contracts/README.md · ARCHITECTURE §0 rows "Metadata service" and "Health brain" · `common/models.py` · `common/brain_api.py` · DESIGN §6.3 (event copy, for `emit` fields).
**Branch per task:** `jaiveer/<task>` e.g. `jaiveer/j1-phi`. Commit `jaiveer: <what>`. PR to `main`, tell Anushka.
**After every task:** append to `updates/jaiveer_update.md` (TEAM_PROTOCOL §6).

---

## ▶ START NOW: J1–J3 need nothing but the scaffold

Paste into Claude Code:
```
Read CLAUDE.md, docs/TEAM_PROTOCOL.md, tasks/jaiveer_tasks.md, docs/ARCHITECTURE.md §4.5, §4.11, §4.12
and TECH_STACK §6.2. Implement task J1, then J2, then J3 exactly as specified in tasks/jaiveer_tasks.md.
Keep the function signatures already in the placeholder files. Pure functions only, no I/O.
Write the tests listed in each test file's docstring. Run `python -m pytest -q` until green.
```

### J1. Phi accrual (≈30 min) · target H2.5
- **Files:** `backend/vault/common/jaiveer_phi.py`, `backend/vault/tests/test_jaiveer_phi.py`
- **Spec:** ARCHITECTURE §4.5, verified code in TECH_STACK §6.2 (use it; the bootstrap is mean 500 ms / min std 200 ms).
- **Done when:** with 500 ms heartbeats, silence 1.0 s → φ ≈ 2.2, 1.5 s → ≈ 7.3, 2.0 s → ≈ 18 (±0.5); φ = 0 before the first heartbeat.

### J2. Placement ring (≈1 h) · target H3.5
- **Files:** `backend/vault/common/jaiveer_placement.py`, `tests/test_jaiveer_placement.py`
- **Spec:** ARCHITECTURE §4.11 (pseudo-code is there), D5. Hash with `vault.common.hashing.placement_hash`. 128 vnodes/node scaled by capacity.
- **Inputs:** `list[NodeView]` (build test nodes from `load_config("vault.yaml").nodes`). **Outputs:** node id lists.
- **Done when:** deterministic across processes; `rep3` on default labels returns a fully independent triple; constraints relax only when necessary; DEAD/fenced/full/excluded never chosen; adding `n7` changes ≈ 1/7 of chunks' first choices; `fate_keys=[]` → pure ring order.
- **Note:** Soum's rebalancer calls `place`/`choose_additional`. Don't change the signatures.

### J3. Effective copies / IFL (≈1 h) · target H4
- **Files:** `backend/vault/common/jaiveer_fate.py`, `tests/test_jaiveer_fate.py`
- **Spec:** ARCHITECTURE §4.12 (domains, cluster-wide, IFL by iterative deepening, min_cut, target), PRD §4.1.
- **Done when:** default labels, rep3 on {n1,n3,n5} → IFL 3; relabel n3 and n5 to power=A → IFL 1, min_cut `[("power","A")]`; `version` is cluster-wide and excluded; ec42 on all 6 → IFL 3 and survives any one power strip; results cached by `frozenset`.

---

## Then, in order

### J4. Metadata DB + app skeleton + membership basics · H4 → H5 · ⚠ gated: schema
- **Files:** `metadata/jaiveer_schema.sql` (copy ARCHITECTURE §3.5 **verbatim**: the schema is a contract), `metadata/jaiveer_db.py` (implements `brain_api.Db`: one writer connection behind an `asyncio.Lock`, one reader, `BEGIN IMMEDIATE … COMMIT`, `synchronous=FULL`, naive → `OFF`), `metadata/jaiveer_app.py` (`create_app` via `make_app`, lifespan opens the DB, builds a `BrainContext`, registers `events.set_sink` + `events.set_name_resolver`), `brain/jaiveer_membership.py`.
- **Endpoints:** `POST/GET /v1/buckets`; `POST /v1/nodes/register` (returns epoch, lease_ttl_ms); `POST /v1/nodes/{id}/heartbeat` (records arrival + `X-Vault-Via` route, returns `HeartbeatReply`); `GET /v1/config`, `PATCH /v1/config`, `POST /v1/mode` (bump `config_version`; `SafetyCfg.for_mode`).
- **Seed nodes table** from `cfg.nodes` on first start (display names + labels). Emit `meta.recovered` on startup with counts and ms (DESIGN §6.3).
- **Done when:** `python -m vault up` → `curl -X POST :7000/v1/buckets -d '{"name":"clinic","policy":"rep3"}'` works; a fake heartbeat gets a lease. Kill -9 metadata mid-write and restart: nothing committed is lost.
- **Stub until Soum's nodes heartbeat (S4, ~H6):** `tests/jaiveer/jaiveer_fake_nodes.py` registers n1–n6 and heartbeats every 500 ms; a flag stops one.

### J5. Uploads, commit, objects · H5 → H6.5 · **Soum's gateway waits on this (seam I1)**
- **Files:** `metadata/jaiveer_uploads.py`, `metadata/jaiveer_objects.py`, `tests/test_jaiveer_commit.py`
- **Spec:** ARCHITECTURE §4.1 (rules 1–5), §4.3, §7.2 tables. Placement per chunk = `place(...)` with n targets + 2 spares, each target with its node's current epoch.
- **Commit rules:** inside one `BEGIN IMMEDIATE`: version still pending and not expired; each chunk has ≥ W distinct `frag_idx` with `ok` on distinct non-dead nodes (never trust a count); assign `seq` and global `commit_seq` (in `kv`); publish pointer; old current → `superseded`; queue repair for chunks < n. Errors: `409 quorum_not_met`, `410 upload_expired`, `404 no_bucket`, `503 not_enough_machines` (via `VaultHTTPError`).
- **Also:** `GET /v1/objects/{bucket}/{key}` (Manifest with `FragLoc.route`/`slow` from membership), list, `DELETE` (tombstone), `GET /v1/inspect/objects` (Oracle needs it at H9).
- **Done when:** test_jaiveer_commit passes; you post Anushka + Soum the exact curl sequence in your update file.
- **Handoff out:** tell Soum the moment the plan/commit endpoints are on `main`.

### J6. Detector, snapshot, SSE · H6.5 → H8
- **Files:** `brain/jaiveer_detector.py`, `brain/jaiveer_membership.py`, `metadata/jaiveer_cluster.py`, `metadata/jaiveer_stream.py`
- **Spec:** ARCHITECTURE §4.5 state machine (tick 100 ms, φ ≥ 8 → SUSPECT, peer evidence from reach rows in the last 2 s → PARTITIONED, else DOWN after `confirm_ms`, DEAD after `dead_after_s` → epoch++, fragments → `lost`, incident, repair), §4.6 leases/epochs, startup grace, slow flag, `node.recovered_in_grace`. Snapshot = `models.Snapshot` (§8.2), summary level rules §8.2. SSE per TECH_STACK §6.4: `snapshot` every 500 ms, `event` per new event, last 100 events on connect. `GET /v1/events`, `POST /v1/events` (ExternalEvent from supervisor/oracle).
- **Done when:** with `vault up` + real nodes (or your fake), killing n3 shows `node.suspect` → `node.down` → `node.dead` on `curl -N :7000/v1/stream` with detect ≈ 2.6 s; a DEAD node that heartbeats gets `state: DEAD` and re-registers with a new epoch.

### J7. Scheduler + repair + reports · H8 → H9.5 · **M3**
- **Files:** `brain/jaiveer_scheduler.py` (scan 500 ms, priorities P0–P4, implements `enqueue_job`), `brain/jaiveer_repair.py` (dispatch: max 8, 2 per node, 50 MB/s token bucket, 3 attempts with 1/3/9 s backoff; execute repair/move/trim via Soum's `POST /v1/fragments/{fid}/pull` and `DELETE /v1/fragments/{fid}`), `metadata/jaiveer_reports.py`
- **Spec:** ARCHITECTURE §4.8, §4.9 step 4, §7.2 "Health brain". `GET /v1/repair`. Reports: `POST /v1/reports/fragment` → mark corrupt/missing + P0/P1 repair; `POST /v1/participants/{pid}/report` (gateway reach + stats; emit aggregated `read.failover`); `POST /v1/incidents/fault` (supervisor ground truth); `POST /v1/scrub` fan-out to nodes.
- **Safety switch:** `safety.repair` off → nothing dispatched.
- **Done when:** kill a machine → after grace every chunk is back to n durable copies; corrupt a fragment → repaired from a verified copy; `repair.started/progress/completed` events carry the DESIGN §6.3 fields.

### J8. Incidents, metrics, summary · H9.5 → H10.5
- **Files:** `brain/jaiveer_incidents.py`, `brain/jaiveer_metrics.py`, `brain/jaiveer_summary.py`
- **Spec:** PRD §8 metric definitions (exact), ARCHITECTURE §8.3 `Metrics`, DESIGN §6.2 sentence templates. `GET /v1/metrics`, `GET /v1/incidents`.
- **Done when:** after a kill, `/v1/metrics.last_incident` shows detect/grace/repair/mttr that add up; summary sentence changes level correctly (ok → degraded → ok).
- **Fallback (R1):** if you're behind at H9, Anushka takes J8 via handoff. Say so early.

### J9. Shared-Fate Auditor · H10.5 → H13 · **headline feature**
- **Files:** `brain/jaiveer_auditor.py` (+ the `/health` endpoint in `jaiveer_objects.py`)
- **Spec:** ARCHITECTURE §4.12 (audit on label change / membership change / every 5 s; levels safe/limited/at_risk; greedy make-before-break moves via `enqueue_job(kind=move, reason=fate)`; never lower IFL; advice when the target is unreachable; cluster-wide risks), §8.3 `FateReport`, `GET /v1/fate`, `GET /v1/objects/{b}/{k}/health` (IFL + min_cut), `PATCH /v1/nodes/{id}/labels` → `request_audit`.
- **Events:** `fate.at_risk`, `fate.fixed`, `fate.limited` (human = the advice sentence), `fate.cluster_wide`.
- **Done when:** the demo relabel in §4.12 works: set power=A on n3 and n5 → files on {n1,n3,n5} drop to IFL 1 → moves raise them to 2 → advice explains why 3 needs another power supply → cutting Power Strip A leaves every file readable.

---

## Seams you own or depend on

| Seam | Direction | With |
|---|---|---|
| I1 plan/commit/manifest | you → Soum | by H6.5 |
| I3 register/heartbeat/inventory/reports | Soum → you | by H6 |
| I4 BrainContext | you host Soum's `reconcile_inventory`, `gc.run`, `rebalancer.on_node_added/on_drain/run` | wire at H10 |
| I5 ground truth | Anushka's supervisor → `/v1/incidents/fault`, `/v1/events` | by H9 |
| I6 snapshot/SSE/metrics/fate | you → Anushka's dashboard | by H8 |
| I7 `/v1/mode`, `/v1/inspect/objects` | you → Urooz's Oracle | by H9 |

## Gated items in your lane (ask Anushka first)
Schema changes beyond §3.5 · any new field in models.py · any new dependency · config keys in vault.yaml.
