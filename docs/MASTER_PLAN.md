# Vault: Master Plan

**Status:** v1.0 · **Owner:** Anushka (changes are gated, TEAM_PROTOCOL §4) · **Date:** 2026-09-26
**Built from:** `PRD.md` (P0 scope, demo script), `ARCHITECTURE.md` (components, contract), `TECH_STACK.md`, `DESIGN.md`, `TEAM_PROTOCOL.md`.
**Read with:** `OWNERSHIP.md` (who owns which file) and your own `tasks/<name>_tasks.md`.

Budget: 20 hours on the clock, about 15 usable. Hours below are **hackathon hours from the start (H0)**. Scope is the PRD P0 list (§5.1) and nothing else. P1 only after the H14 freeze check passes, and only with Anushka's OK.

---

## 1. What must work

The one loop that wins or loses the demo (PRD §9, 0:30 scene). Everything else layers on top of it:

```
upload a file ─▶ 3 verified copies on 3 machines ─▶ kill a machine ─▶ suspect → down → dead
      ─▶ rebuild the missing copies elsewhere ─▶ checksum verified ─▶ 3 copies again, MTTR on screen
```

The full 3-minute demo adds, in order: silent corruption → cut one cable (relay, 0 rebuilds) → shared fate (relabel, auditor moves copies, cut a power strip) → power cut everything (recovery overlay, "0 lost") → Oracle, Vault vs Naive.

---

## 2. Team lanes

| Person | Lane | Owns (summary; exact files in OWNERSHIP.md) |
|---|---|---|
| **Anushka** | Lead, integrator, full-stack | Shared contracts and `common/` infra, CLI, supervisor + chaos controller, the whole dashboard (except landing), merges to `main` |
| **Jaiveer** | Control plane ("the brain") | `phi`, `placement`, `fate` (IFL) pure modules; metadata service (SQLite, uploads/commit, objects, snapshot, SSE); detector, scheduler, repair, incidents, metrics, summary, **Shared-Fate Auditor** |
| **Soum** | Data plane ("the bytes") | `ec` (zfec); storage node (disk format, verify, scrub, heartbeat, pull, relay, node chaos); gateway (streaming PUT/GET, quorum, EC, failover); reconciler, GC, rebalancer |
| **Urooz** | Proof, demo, pitch | **Durability Oracle** (ledger, checker, workload, runs); chaos scripts; demo seed data; landing page; e2e smoke tests; demo runbook, pitch, backup video |

**Start right now (no dependencies):** Jaiveer J1–J3 (phi, placement, fate: pure functions with tests). Soum S1–S2 (erasure coding, crash-safe on-disk storage). Both only need `common/models.py` and `common/config.py`, which are already in the repo.

---

## 3. Milestones

Each milestone is a merge point: everyone pushes, Anushka merges, and everyone pulls `main`.

| Hour | Milestone | Done when (checked by Anushka at the merge) | Owners |
|---|---|---|---|
| **H0–2** | **M0 Kickoff** | Repo pushed; every laptop ran setup (README); `pytest` green; `python -m vault up` boots 10 processes; `web` builds. Jaiveer and Soum already coding J1–J3 / S1–S2. | Anushka |
| **H4** | **M1 Pieces** | Pure modules pass tests (S1, J1–J3). Node stores/serves verified fragments (S2–S3). Metadata DB + buckets + node register/heartbeat (J4). Chaos link/node endpoints (A2). Oracle checker passes hand-written ledgers (U1). Dashboard shell renders from fixtures (A5). | all |
| **H7** | **M2 First light** | `curl -T` a 50 MB file through the gateway, download it, SHA-256 matches, fragments on 3 nodes' disks (S5, J5). Nodes heartbeat; dashboard shows 6 live machines over SSE (S4, J6, A6). Seed uploads 200 files (U2). | Soum, Jaiveer, Anushka, Urooz |
| **H9** | **M3 Self-healing** ★ | Kill a machine from the dashboard → suspect → down → dead → repair → all files back to target copies; timeline + MTTR tile correct (J6–J7, S4, A6). First Oracle run in Vault mode on the real gateway: 0 violations (U3, U5). | Jaiveer, Soum, Anushka, Urooz |
| **H11** | **M4 Faults** | Corrupt copies → found by scrub/read and repaired. Cut meta↔node → relay badge, **0 repairs**. Power cut all → recovery with 0 lost. Naive mode switches respected in every component. Chaos dock drives every action (S6, J7–J8, A3, A6–A7). | all |
| **H13** | **M5 Headlines** | Shared-fate demo from ARCHITECTURE §4.12 works end to end from the Fate page (J9, A8). Oracle Vault vs Naive on `standard` shows 0 vs >0 (U5–U6, A8). `ec42` bucket round-trips and rebuilds. Rejoin trims extra copies; GC cleans up (S8). Landing done (U4). | all |
| **H14** | **FEATURE FREEZE** | Go/no-go on §8. After this: bug fixes and polish only. | Anushka |
| H14–17 | Hardening | Full demo script (PRD §9) runs 3× in a row without a restart. Oracle violations are fixed before anything else. DESIGN §11 polish checklist. | all |
| H17–19 | Record + rehearse | Record `heavy` Oracle runs (Vault and Naive) for the pitch. Record the backup video. Rehearse the pitch 3×. | Urooz (lead), all |
| H19–20 | Buffer | Nothing new. | all |

---

## 4. Critical path

```
H0 ── common/models.py + config + rpc + netsim + events   (DONE in the scaffold)
 │
 ├─ Soum:    S1 ec ─ S2 storage ─ S3 node API ─ S4 heartbeat/pull/scrub ─┐
 │                                                                       ├─ S5 gateway PUT/GET ─▶ M2 (H7)
 ├─ Jaiveer: J1–J3 pure ─ J4 DB + register ─ J5 uploads/commit/manifest ─┘
 │                                    └─ J6 detector + snapshot + SSE ─ J7 scheduler + repair ─▶ M3 (H9) ★
 │                                                                                  └─ J9 auditor ─▶ M5
 ├─ Anushka: A2 chaos ctl ─ A3 power/reset ─ A5 shell on fixtures ─ A6 live wiring ─▶ M3
 └─ Urooz:   U1 checker ─ U2 seed ─ U3 workload ─ U5 runs API (needs S5) ─▶ M3 ─ U6 naive vs vault ─▶ M5
```

The longest chain is **Jaiveer J4 → J5 → J6 → J7**. If Jaiveer slips, Anushka moves J8 (incidents/metrics/summary) to herself before touching anything else (§7, R1).

---

## 5. Demo scenes → what each needs

| Scene (PRD §9) | Needs | Owners | Ready by |
|---|---|---|---|
| Healthy console | snapshot + SSE, node cards, KPI strip, 200 seeded files | J6, A5–A6, U2 | M2 |
| Kill a machine | detector (φ, peer confirm, grace), epochs, repair queue + pull, incident MTTR, chaos kill | J6–J8, S4, A2, A6 | **M3** |
| Silent corruption | node `/_chaos/corrupt`, scrubber, read verify + report, repair from verified source | S4, S6, J7 | M4 |
| Cut one cable | netsim (done), pinger reach rows, relay endpoint, PARTITIONED state, relay route | S4, S6, J6, A2 | M4 |
| Shared fate | IFL, labels PATCH, auditor moves (make-before-break), advice, power cut by label, Fate page | J3, J9, A3, A8 | M5 |
| Pull the plug | crash-safe writes (S2), WAL (J4), startup recovery + grace, `meta.recovered`, boot overlay | S2, J4, J6, A3, A10 | M4 |
| The Oracle | ledger, checker, workload, seeded chaos scripts, runs API, Naive mode, Oracle page | U1–U6, A3, A8 | M5 |

Not in the 3-minute demo, but P0 and shown in Q&A if asked: add machine / rebalance (S9), rejoin trim (S8), `ec42` overhead (S5, J5).

---

## 6. Integration points (the seams)

Every seam is defined by `backend/vault/common/models.py` (Python) and `web/lib/contracts.ts` (TypeScript). Both are frozen; see `docs/contracts/README.md`.

| # | Seam | Producer → consumer | Contract | First joined | How to test the seam alone |
|---|---|---|---|---|---|
| I1 | Upload plan / commit / manifest | Jaiveer → Soum (gateway) | `UploadPlan`, `CommitRequest`, `CommitResult`, `Manifest` (§7.2) | H6–7 | Soum: `tests/soum/soum_fake_meta.py` until J5 lands |
| I2 | Fragment PUT/GET/DELETE/pull | Soum (node) → Soum (gateway), Jaiveer (repair) | `FragmentHeader`, `PullRequest`, headers in §7.3 | H5 | curl a fragment in and out |
| I3 | Register / heartbeat / inventory / reports | Soum (node) → Jaiveer (metadata) | `RegisterRequest`, `Heartbeat`, `HeartbeatReply`, `Inventory`, `FragmentReport` | H6 | Jaiveer: `tests/jaiveer/jaiveer_fake_nodes.py` posts heartbeats |
| I4 | Brain modules inside metadata | Jaiveer (host) ↔ Soum (reconciler, gc, rebalancer) | `common/brain_api.py` `BrainContext` | H10 | Soum: unit tests with a fake `BrainContext` |
| I5 | Chaos + ground truth | Anushka (supervisor) → nodes `/_chaos/*`, metadata `/v1/incidents/fault` + `/v1/events` | §7.3 chaos, §7.4, `FaultReport`, `ExternalEvent` | H4 | curl `/_chaos/*` on a stub node (already works) |
| I6 | Snapshot / SSE / metrics / fate | Jaiveer → Anushka (web) | `Snapshot`, `Event`, `Metrics`, `FateReport` (§8) | H7 | Anushka builds on `web/fixtures/*.json` (validated against models.py) |
| I7 | Oracle ↔ system | Urooz → gateway (client traffic), supervisor (reset, script), metadata (`/v1/mode`, `/v1/inspect/objects`) | §5, §7.5, `LedgerEntry`, `RunStatus` | H9 | Urooz: `tests/urooz/urooz_fake_gateway.py` in-memory |
| I8 | Chaos scripts | Urooz (`urooz_scripts.steps`) → Anushka (`/chaos/script` runner) | `ChaosStep` defined by Urooz in U3, shape agreed with Anushka | H11 | pure unit test |
| I9 | Seed | Urooz (`urooz_seed.seed`) → Anushka (`/demo/seed`, `/cluster/reset`) | `seed(rpc, bucket, count, seed) -> int` | H7 | run against the gateway |
| I10 | Events copy | everyone → `common/events.py` templates (DESIGN §6.3) | `emit(type, subject, data, **fields)` | now | `test_common.py` |
| I11 | Service entry | every owner's `create_app(cfg, pid)` → `python -m vault.<svc>` shims | `common/service.py` | now (stubs boot) | `python -m vault up` |

**Merge ritual (H4, H7, H9, H11, H13):** everyone pushes by :00 → Anushka merges in order Jaiveer, Soum, Urooz, Anushka → `pytest` + `vault up` + the milestone check → everyone pulls `main` by :15. Update `updates/<name>_update.md` **before** pushing; if it's not in your update file, it doesn't exist for integration.

---

## 7. Risks and fallbacks

| # | Risk | Early signal | Fallback (decided now, so no debate later) |
|---|---|---|---|
| R1 | Jaiveer's chain (J4→J7) is the longest and slips | J5 not merged at H7 | Anushka takes J8 (incidents/metrics/summary). J9 auditor moves become "report + advice only, no moves" (IFL and advice still shown). |
| R2 | Gateway ↔ metadata integration late | No end-to-end PUT at H7 | Soum keeps testing against `soum_fake_meta.py`; Jaiveer and Soum pair for 30 minutes at H7 on the seam only. |
| R3 | Relay is harder than expected | S6 not working at H11 | Keep peer confirmation: a node that peers can reach becomes PARTITIONED (not DOWN), so **no repair** happens. That's the key claim. Relay badge becomes "Checked through {peer}". |
| R4 | EC (`ec42`) bugs | S5 EC round trip fails at H11 | Demo uses `rep3` only. `archive` bucket shown with overhead 1.5× only if round-trip passes; otherwise hidden. |
| R5 | Oracle finds violations | any non-zero Vault count | Violations are bugs and get fixed **before** features (PRD §11). Samples tell you the key and the rule. |
| R6 | Windows process/port quirks (whole team is on Windows) | `vault up` fails, ports stuck | Already handled: supervisor kills stale children from `logs/children.json`. Directory fsync is skipped on Windows (TECH_STACK §6.3). Test kill/restart at every merge. |
| R7 | 3D view slow or late | A9 not done by H13 | Ship `ClusterScene2D` (SVG) only; 3D is the first UI item cut. |
| R8 | Dashboard scope too big for one person | A7/A8 behind at H11 | Urooz can take the Oracle page (her data, her story) via a handoff. Files page drops the inspect drawer's fragment grid first. |
| R9 | Demo timing flaky | detect/repair times vary | `VAULT_DEMO=1` (grace 8 s), seeded chaos, `vault reset` ≤ 20 s, recorded backup video, every chaos action is one button. |
| R10 | Token budget runs out | anyone near their limit | Read only your ARCHITECTURE §0 sections. One task per Claude session. Don't re-plan. Use fixtures and fakes instead of pasting other people's code. |
| R11 | Simulated page cache (P1) not built | — | Not P0. The Naive comparison still shows violations from no-verify, W=1, no-repair and no-fencing. Say "process kill ≠ power loss" honestly (PRD §10). |

**Cut list, in order, if behind at H11:** (1) all P1 items, (2) 3D → 2D only, (3) rebalancer UI (keep the event line), (4) landing scroll story (keep the hero), (5) `ec42` in the demo, (6) auditor moves (keep IFL + advice).

---

## 8. Go/no-go at H14 (feature freeze) and H17

- [ ] `python -m vault reset` → clean seeded state in ≤ 20 s, twice in a row
- [ ] Kill a machine: detect ≈ 2–3 s, rebuild starts after grace, all files back to target, MTTR tile correct
- [ ] Corrupt 10 copies: all found and replaced; no damaged byte ever served (Oracle agrees)
- [ ] Cut meta↔Records Room: relay badge, **0** repairs, files readable
- [ ] Relabel onto Power Strip A: at-risk files appear, moves raise IFL to 2, advice shown; cut Power Strip A: all files readable
- [ ] Power cut everything: overlay shows "N of N files present. 0 lost." computed from data
- [ ] Oracle `standard`: Vault 0 violations; Naive > 0 with readable samples
- [ ] Overview fits 1280×720 with no scrolling; presenter mode readable from 4 m
- [ ] Backup video recorded (H17–19)

---

## 9. Open decision for Anushka

**`docs/REFORGE_4_LAPTOP_DEMO_PLAN.md` vs the PRD.** The REFORGE doc describes 4 physical laptops, 4 nodes and the name "REFORGE". The frozen PRD describes 6 simulated machines on one laptop, names the product "Vault", and lists "real hardware for the demo" as a non-goal (§5.4).

**Recommendation:** build and demo the PRD version. The architecture already supports real addresses (`nodes.<id>.addr` in `vault.yaml`, everything is HTTP), so a **4-laptop bonus** is possible at H16+ **only if every §8 box is ticked**: set `cluster.host: 0.0.0.0`, give each node a real `addr`, open the ports in Windows Firewall, and run one node per laptop. Treat it as P2; nobody builds for it before then. REFORGE's demo order (§17) matches our PRD scenes 1–4, so no demo-script changes are needed.
