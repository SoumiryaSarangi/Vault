# Urooz: Proof, demo and pitch

You own the thing that makes judges believe us: the **Durability Oracle**, an independent ledger that checks, live, that nothing acknowledged was ever lost, damaged, brought back after deletion, or served stale (PRD §4.2). You also own the demo data, the landing page, the demo runbook and the pitch. Your code is mostly pure logic with crisp rules, which is ideal for Claude plus tests.

**Read first (only these):** TEAM_PROTOCOL.md · MASTER_PLAN.md · OWNERSHIP.md · docs/contracts/README.md · PRD §4.2, §9, §10 · ARCHITECTURE §0 row "Durability Oracle" (§1, §4.1–4.4, §5, §7.1, §7.5, §8) · `common/models.py` (`LedgerEntry`, `Run*`).
**Branch per task:** `urooz/<task>`. Commit `urooz: <what>`. PR to `main`, tell Anushka.
**After every task:** append to `updates/urooz_update.md` (TEAM_PROTOCOL §6).

---

### U1. Ledger + checker (pure) · H2 → H4 · no dependencies, start first
- **Files:** `oracle/urooz_ledger.py`, `oracle/urooz_checker.py`, `tests/test_urooz_checker.py`
- **Spec:** ARCHITECTURE §5: ledger line format (= `models.LedgerEntry`), `unknown` vs `fail`, the five violation rules, **acceptable final states** (the acked write with the highest `commit_seq` ∪ every `unknown` write invoked after that write's invocation; DELETE = "absent"), `under_protected` from `/v1/inspect/objects` counted separately.
- **Checker shape:** `check(entries: list[LedgerEntry], final_reads: dict[key, (sha256|None)], inspect: list[InspectObject]) -> (RunViolations, RunHealth, list[ViolationSample])`. Sample `human` lines like "k042 was saved at 12:03:11 and is now missing." (ARCHITECTURE §7.5).
- **Ledger:** append-only JSONL in `oracle_runs/<run_id>/ledger.jsonl`, flush + fsync every 100 ms.
- **Done when:** one hand-written ledger per rule triggers exactly that violation, and a ledger full of `unknown` writes triggers **none** (no false alarms).
- Paste into Claude Code:
  ```
  Read CLAUDE.md, docs/TEAM_PROTOCOL.md, tasks/urooz_tasks.md and docs/ARCHITECTURE.md §5 and §7.5.
  Implement task U1 exactly as specified. Pure functions, no network. Write the tests from the
  docstring in backend/vault/tests/test_urooz_checker.py. Run `python -m pytest -q` until green.
  ```

### U2. Demo seed data · H4 → H5 · Anushka's reset calls this (seam I9)
- **Files:** `supervisor/urooz_seed.py`
- **Spec:** ARCHITECTURE §6 "Seed": ~200 synthetic clinic files (`xray-0042.png`, `lab-report-0113.pdf`, …), 20 KB–3 MB of seeded random bytes, uploaded through the gateway with `get_rpc().request("gw", "PUT", f"/{bucket}/{key}", content=..., timeout=...)`, a few in parallel. **No real patient data.**
- **Signature (fixed):** `async def seed(rpc, bucket: str = "clinic", count: int = 200, seed: int = 42) -> int` (returns files uploaded).
- **Done when:** after Soum's gateway lands (~H7): 200 files uploaded in < 60 s.

### U3. Workload + chaos scripts · H5 → H7
- **Files:** `oracle/urooz_workload.py`, `supervisor/urooz_scripts.py`, `tests/urooz/urooz_fake_gateway.py`
- **Workload** (§5): `clients` (8) async loops, seeded RNG, 200 keys in bucket `oracle`; 50% PUT (1 KB–2 MB random), 40% GET, 10% DELETE; record every op with invoke/complete times and outcome (`ok`/`fail`/`unknown` rules in §5); read `X-Vault-Commit-Seq`/`X-Vault-Seq` headers.
- **Scripts:** `steps(name, seed, nodes) -> list[ChaosStep]` for `standard` (the 9 timed steps in §5) and `heavy` (3 cycles, 80 corruptions per burst, restart crashed machines at the end of each cycle); `fault_units(steps) -> int` (standard ≈ 47, heavy ≈ 500). Agree the `ChaosStep` shape with Anushka (she executes it; suggest a pydantic model with `t`, `action`, `params`). **Gated**: a new shared shape, so get her OK first.
- **Fake gateway until Soum's lands:** an in-memory FastAPI with PUT/GET/DELETE and commit_seq headers, so you can develop the workload now.

### U4. Landing page · H7 → H9 (or any time you're blocked)
- **Files:** `web/app/(landing)/page.tsx`, `web/components/landing/urooz_Hero.tsx`, `urooz_HeroBackground.tsx`, `urooz_StoryScroller.tsx`, `urooz_ProofBand.tsx`, `urooz_CtaButton.tsx`
- **Spec:** DESIGN §5.2 (wireframe, hero entrance, magnetic CTA, 4-beat scroll story, proof band), §4.2–4.3 (performance and smoothness rules), TECH_STACK §6.5–6.6 (verified `LiquidMetal` and `ShaderGradient` snippets), §6.7 (load WebGL with `next/dynamic`, `ssr: false`). The logo is ready: `import VaultMarkLazy from "@/components/brand/VaultMarkLazy"` (liquid metal, loads client-side, pauses offscreen) and `Wordmark` for the top bar. Don't import `VaultMark` directly.
- **Budget:** ~3 hours total (DESIGN §5.2). Story beats can use a simple SVG until Anushka's `ClusterScene2D` exists; keep it simple.
- **Done when:** `npm run build` passes; hero is complete within 1.5 s; no console errors; the CTA opens `/console`.
- **Gated:** anything in `globals.css`, `layout.tsx` or shared components. Ask Anushka.

### U5. Oracle service: runs API · H9 → H11 · **needs Soum's gateway (S5)**
- **Files:** `oracle/urooz_app.py`, `oracle/urooz_runs.py`
- **Spec:** ARCHITECTURE §5 run lifecycle (`preparing`: supervisor `POST /cluster/reset` + metadata `POST /v1/mode`; `running`: workload + supervisor `POST /chaos/script`; `settling`: until repair queue empty or 60 s; `verifying`: final GET of every key + `/v1/inspect/objects` → checker; `done`), §7.5 API (`POST /runs`, `GET /runs`, `GET /runs/{id}`, SSE `/runs/{id}/stream`, `POST /runs/{id}/stop`, `409 run_active`). Emit `oracle.run_started`, `oracle.violation`, `oracle.run_completed` via metadata `POST /v1/events` (`ExternalEvent`).
- **Done when:** a Vault-mode `standard` run completes end to end and reports 0 violations (M3 target: H9–H10).

### U6. Oracle page + Vault vs Naive · page H7 → H9 on fixtures, runs H11 → H13 · **M5**
- **The Oracle page is yours** (moved from Anushka in plan v1.1). **Files:** `web/app/console/oracle/page.tsx`, `web/components/oracle/urooz_OracleColumn.tsx`, `urooz_ViolationBreakdown.tsx`, `urooz_RunCounters.tsx`, `urooz_ChaosTimelineStrip.tsx`, `urooz_ViolationSamples.tsx`.
- **Spec:** DESIGN §5.6 (two columns Vault | Naive, the big 72 px number, counters, chaos timeline strip with violation ticks, "what went wrong" samples, "Run again" warning), §6 voice, §9 empty state, §8.1 data flow (poll `GET /runs` every 2 s, SSE `/runs/{id}/stream` while a run is active). Types from `web/lib/contracts.ts` (`RunStatus`, `RunSummary`, `RunList`); URLs from `web/lib/api.ts` (`ORACLE_URL`, `getJson`, `postJson`).
- **Build it first on fixtures:** `web/fixtures/oracle_runs.json` has a finished Vault run and a Naive run (checked against models.py). Switch to the live API once U5 works.
- **Runs:** run `standard` in both modes; confirm Naive shows > 0 violations with readable samples; fix false alarms in the checker (never "fix" by hiding real violations).
- **Done when:** the page reads correctly from 4 m in presenter mode, and matches DESIGN §5.6 with real runs.
- The page sits inside Anushka's console layout (top bar, KPI strip, chaos dock). Don't edit the layout; ask via handoff.

### U7. End-to-end smoke tests · H11 → H14
- **Files:** `tests/urooz/urooz_e2e_*.py`: one script per demo scene (PRD §9) that drives the supervisor + gateway and asserts the expected outcome (e.g. kill n3 → within 30 s all files back to target). These are the H14/H17 go/no-go checks (MASTER_PLAN §8).

### U8. Demo runbook, pitch, recording · H12 → H19
- **Files:** `demo/urooz_demo_runbook.md` (exact clicks, lines and expected screen per scene, reset procedure, fallbacks), `demo/urooz_pitch.md` (PRD §10 + judge Q&A), backup video.
- H17–19: record `heavy` Oracle runs (Vault + Naive), record the full demo video, lead 3 rehearsals.

---

## Seams

| Seam | Direction | With | When |
|---|---|---|---|
| I7 Oracle ↔ system | you → gateway (Soum), supervisor (Anushka), metadata (Jaiveer) | H9 |
| I8 chaos scripts | you → Anushka's script runner | H11 |
| I9 seed | you → Anushka's reset/seed endpoints | H7 |

## Gated items in your lane
`ChaosStep` shape (agree with Anushka) · any change to models.py · any new dependency · any web file that isn't `urooz_*`, the landing `page.tsx` or the Oracle `page.tsx`.
