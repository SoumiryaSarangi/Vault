# Anushka: updates

Append one entry per completed task (TEAM_PROTOCOL §6). Newest at the bottom. If it isn't here, it doesn't exist for integration.

<!--
## [Hour X] <task id + name>
- What was done:
- Files created/changed:
- Endpoints / functions / components exposed (with signatures):
- How to run / test it:
- Known issues / TODO:
- Anything other teammates must know or do:
-->

## [Hour 1] A0 Scaffold, master plan, task split
- What was done: docs/MASTER_PLAN.md, docs/OWNERSHIP.md, docs/contracts/README.md, tasks/*_tasks.md; full repo scaffold; shared contracts and infra; supervisor process control; web scaffold.
- Files created/changed: backend/vault/common/{models,config,ids,hashing,log,netsim,rpc,events,service,brain_api}.py; backend/vault/__main__.py; */__main__.py shims; supervisor/anushka_{app,procs}.py; all owner placeholders; web/ (Next 16, Tailwind 4 tokens, lib/*, fixtures/*); vault.yaml; requirements.txt; pyproject.toml.
- Endpoints / functions exposed: `make_app(cfg, pid, lifespan)`, `run_service(...)`; `get_rpc().request(target, method, path, ...)`, `rpc.forward(...)`, `NetworkError`; `emit(type, subject, data, **fields)`; `load_config()`, `cfg.policy(name)`, `cfg.addr(pid)`; supervisor `GET /procs`, `POST /procs/{pid}/kill|start|restart`; every service `GET /_vault/health`, `/_chaos/*`.
- How to run / test it: `python -m pytest -q` (9 passing); `python -m vault up` then `python -m vault status`; `cd web && npm run build`.
- Known issues / TODO: supervisor chaos/power/reset/seed endpoints are A2–A3. Stub apps only answer health (and /v1/ping on nodes).
- Anything other teammates must know or do: Jaiveer start J1–J3, Soum start S1–S2 now. Keep the pinned signatures in your placeholder files.

## [Hour 2] A4 Brand components + A2 Chaos controller I + plan v1.1
- What was done: logo components (liquid metal + static + wordmark); supervisor chaos endpoints; Oracle page moved to Urooz; 3D view made a stretch goal.
- Files created/changed: web/components/brand/{VaultMark,VaultMarkLazy,VaultMarkStatic,Wordmark}.tsx; backend/vault/supervisor/anushka_{chaos_ctl,app}.py; web/fixtures/oracle_runs.json; docs/{MASTER_PLAN,OWNERSHIP,contracts/README}.md; tasks/{anushka,urooz}_tasks.md; tasks/claude/urooz.CLAUDE.local.md.
- Endpoints / functions / components exposed:
  - `<VaultMarkLazy size={240} />` (use this one), `<VaultMarkStatic size={22} />`, `<Wordmark withMark />`
  - Supervisor: `POST /chaos/link {a,b,cut,direction}`, `POST /chaos/node/{pid} {action: slow|freeze|corrupt|disk_full|clear, params}`, `POST /chaos/corrupt {count,mode}` (random, across nodes), `GET /chaos`, `POST /chaos/clear_all`. Kill/start/restart now report ground truth.
  - Every action POSTs `FaultReport` to meta `/v1/incidents/fault` and an `ExternalEvent` `chaos.*` to meta `/v1/events` (kinds and types listed in docs/contracts/README.md items 9–10).
- How to run / test it: `python -m vault up`, then e.g. `curl -X POST :7070/chaos/link -H "Content-Type: application/json" -d '{"a":"meta","b":"n5","cut":true,"direction":"both"}'` and `curl :7105/_chaos`.
- Known issues / TODO: node corrupt/disk_full return 404 until Soum's S6 adds those node routes. Metadata reports log a warning until Jaiveer's J6/J7 add `/v1/events` and `/v1/incidents/fault`. A3 next.
- Anything other teammates must know or do: **Urooz**: the Oracle page is now yours (U6), fixture at web/fixtures/oracle_runs.json, logo via VaultMarkLazy. **Jaiveer**: match incidents on FaultReport kinds `node_dead`/`partition`/`corruption`/`power_cut`. **Soum**: `/_chaos/corrupt` must return `CorruptResult` with the damaged fids.

## [Hour 3] A3 Chaos controller II (power, add machine, reset, seed, scripts)
- What was done: power cut (all / by label) with optional auto-restore, power restore, add machine (n7+), cluster reset, seed endpoint, chaos-script runner. `ChaosStep` added to the contract. `node_identity()` helper for nodes.
- Files created/changed: backend/vault/supervisor/anushka_{chaos_ctl,cluster,procs,app}.py; backend/vault/common/{models,config}.py (ChaosStep, node_identity); web/lib/contracts.ts (ChaosStep); tests/test_common.py (+2 tests, 11 passing); docs/contracts/README.md (items 10–12); tasks for Soum, Jaiveer, Urooz.
- Endpoints / functions exposed:
  - `POST /power/cut {scope:"all"|"label", label:"power=A", restore_after_s?}` → `{killed}` · `POST /power/restore {scope, label?}` → `{started}`
  - `POST /nodes/add {display_name, labels}` → Proc (n7, port 7107, …)
  - `POST /cluster/reset {seed, mode}` → `{ok, elapsed_s}` (never kills the oracle) · `POST /demo/seed {bucket, count}` → `{uploaded}`
  - `POST /chaos/script {name, seed}` → `{script_id}` · `POST /chaos/script/{id}/stop`
  - `vault.common.config.node_identity(cfg, node_id) -> (display_name, labels)`; `models.ChaosStep {t, action, params}`
- How to run / test it: `python -m vault up`, then `python -m vault reset`, or `curl -X POST :7070/power/cut -H "Content-Type: application/json" -d '{"scope":"label","label":"power=A","restore_after_s":3}'`.
- Known issues / TODO: bucket creation, mode and the ALIVE wait in reset are skipped (logged) until Jaiveer's J4/J6; seed and scripts return 501 until Urooz's U2/U3. Power cut by label uses metadata's live labels when `/v1/cluster` exists, else the vault.yaml labels.
- Anything other teammates must know or do: **Soum**: register with `node_identity(cfg, pid)`. **Jaiveer**: register must accept unknown nodes (n7+); `POST /v1/buckets` on an existing bucket → 409. **Urooz**: `ChaosStep` is ready in models.py; reset calls your `seed()` and must stay ≤ 20 s in total.

## [Hour 3] Merge point: Jaiveer J1–J3 + decision D1 (ec42 IFL)
- What was done: reviewed and merged `jaiveer-j1-j3` (only his files touched; full suite 72 passing on the merged main). Merged chaos controller II. Decided Jaiveer's ec42 question: MASTER_PLAN §9 D1.
- Files created/changed: docs/MASTER_PLAN.md (§9 decisions log, REFORGE now §10); tasks/{jaiveer,urooz,anushka}_tasks.md.
- Anything other teammates must know or do: **Everyone**: `git pull origin main`. **Jaiveer**: ec42 IFL 2 is correct, keep it; J9 rule: no moves when none can help, one aggregated `fate.limited` advice for ec42. **Urooz**: seed `clinic` only; ec42 Q&A line added to U8. **Soum**: `place`/`choose_additional` are on main now.

## [Hour 4] A5 Console shell + Overview (and most of A6)
- What was done: the whole console frame and the Overview page, driven by one store; chaos dock wired to the live supervisor; sample-data fallback; CORS fix.
- Files created/changed: web/components/console/{ConsoleShell,TopBar,NodeCard,NodeList,EventTimeline,KpiStrip,ChaosDock}.tsx; web/components/scene/ClusterScene2D.tsx; web/components/ui/{Popover,Toasts}.tsx; web/lib/{store,feed,states,hooks,chaos,api,contracts}.ts; web/app/console/{layout,page}.tsx; web/app/globals.css (animations, presenter mode); backend/vault/common/service.py (CORS); docs/contracts/README.md (item 13).
- Endpoints / functions / components exposed: `startFeed()` (SSE + pollers, sample fallback); `useVault` store adds `source` ("connecting" | "live" | "sample"), `toast()`, `setEvents()`; `chaos.*` helpers in lib/chaos.ts; `<ClusterScene2D snapshot faults selectedId onSelect />`; `nodeLook()`, `iflColor()`, `islandColor()` in lib/states.ts; `Popover`, `MenuItem`, `MenuLabel` in components/ui.
- How to run / test it: `python -m vault up`, `cd web && npm run dev`, open http://localhost:3000/console. Without metadata's stream it shows "Sample data" after 2 s; the chaos dock still drives the real cluster.
- Known issues / TODO: KPI "effective copies" colour uses the rep3 scale (see D1); an exact per-policy colour needs a below-target count from metrics (ask Jaiveer if needed). Move plug returns an error toast until Jaiveer's `PATCH /v1/nodes/{id}/labels` (J9).
- Anything other teammates must know or do: **Everyone**: services now accept the dashboard from localhost/127.0.0.1 on any port (contracts README 13). **Urooz**: the Oracle page renders inside this shell; reuse `Popover`/`MenuItem` and `lib/states.ts` colours (read-only). **Jaiveer**: the dashboard reads `Snapshot`, `Event`, `Metrics`, `FateReport` exactly as in models.py; web/fixtures/*.json show what it expects.

## [Hour 5] Merge point H5 + TECH_STACK φ fix
- What was done: merged Soum S1–S3 and my A5 into main (151 tests green, cluster boots with Soum's real node, reset 3.9 s). Fixed the φ snippet in TECH_STACK §6.2 (found by Jaiveer: log10(0) after ~6 s of silence); verified that the new form gives identical values and stays finite.
- Files created/changed: docs/TECH_STACK.md §6.2.
- Anything other teammates must know or do: **Jaiveer**: J6 isn't on GitHub yet (no branch, no PR); push it and I'll merge. **Anyone copying snippets**: use the updated §6.2.

## [Hour 5] A7 Files page
- What was done: Files page (DESIGN §5.4) on live metadata with a sample fallback. Tested at 1280×720 on sample data and live after `vault reset` (live buckets, empty state, seed → "not built yet" toast, upload → plain-language error until the gateway exists).
- Files created/changed: web/app/console/files/page.tsx; web/components/files/{FileParts,InspectDrawer,UploadDropzone}.tsx; web/lib/files.ts; web/fixtures/files.json (validated against models.py); web/lib/contracts.ts (+InspectObject, InspectPage and the request types the dashboard sends; header now says it mirrors the browser-facing subset only).
- Endpoints used: metadata `GET /v1/buckets`, `GET /v1/objects/{b}` (fallback when the gateway list isn't there), `GET /v1/inspect/objects`, `GET /v1/objects/{b}/{k}/health`; gateway `PUT/DELETE /{b}/{k}`, `GET /{b}/{k}` (download), `GET /{b}`; supervisor `POST /demo/seed`.
- Known issues / TODO: uploads, downloads and deletes need Soum's gateway (S5); Seed needs Urooz's U2. The top bar still says "Sample data" until Jaiveer's J6 stream is merged, even when the Files page shows live (empty) metadata.
- Anything other teammates must know or do: **Soum**: the page PUTs raw bytes to `/{bucket}/{key}` via XHR (Content-Length set by the browser) and expects `PutResult` JSON, or `ErrorBody` with `error` codes `quorum_not_met` / `not_enough_machines` / `metadata_unavailable` / `no_bucket`. **Jaiveer**: `/v1/inspect/objects` and `/health` power the table and drawer; please keep them fast (called every 3 s and 2 s).

## [Hour 6] Merge point: Jaiveer J6 + live dashboard check
- What was done: merged J6 (detector, /v1/cluster, SSE, φ underflow fix). Checked live: metadata + Jaiveer's fake nodes → the dashboard switched from sample to live data by itself; silencing n3 showed "slow to answer. Checking…" → "stopped responding" → DEAD with VAULT_DEMO=1. With real nodes there are no false cut links and no slow flags (with fake nodes both appear only because fake nodes don't answer pings). Fixed KPIs on an empty cluster (were a red 0 and 0.0×; now "—").
- Known issues / TODO: until Soum's S4 heartbeats land, every real node goes DEAD after the 10 s startup grace and the summary reads "Your data is safe. 0 of 6 machines are healthy." (Jaiveer, low priority; S4 fixes it in practice).

## [Hour 7] A8 Fate page + A10 overlays and settings
- What was done: Fate page (label matrix → PATCH /v1/nodes/{id}/labels, power-strip cards, cut-a-strip buttons, IFL histogram / at-risk / advice from /v1/fate when J9 lands, cluster-wide risk). Power-cut overlay (with "Turn the power back on") and boot overlay ("N of N files present. X lost." from counts before vs after). Settings drawer: Vault/Naive mode with confirm, Explain, presenter, Reset demo with confirm.
- Files: web/app/console/fate/page.tsx; web/components/console/{Overlays,SettingsDrawer}.tsx; TopBar (gear), ConsoleShell (overlays).
- Known issues / TODO: the boot overlay's final line waits for all machines to be ALIVE, so it can't complete until Soum's S4 heartbeats; it times out after 45 s so it never blocks the stage. Repair-speed and grace sliders (P1) are not built.

## [Hour 7] Oracle page (U6 UI, taken over from Urooz)
- What was done: Durability check page (DESIGN §5.6): Vault | Naive columns with the big violation number, breakdown, counters, state pill, Run again (confirm; POST /runs), chaos timeline with violation ticks, "what went wrong" samples. Reads Oracle GET /runs (store) + GET /runs/{id}; falls back to web/fixtures/oracle_runs.json. Fits 1280×720.
- Files: web/app/console/oracle/page.tsx; handoffs/urooz_to_anushka_oracle_page.md; docs/OWNERSHIP.md.
- Anything other teammates must know or do: **Urooz**: the page expects `GET /runs` → RunList, `GET /runs/{id}` → RunStatus (with samples + timeline, `t` in seconds from start), `POST /runs` → RunCreated, exactly as models.py §7.5.

## [Hour 8] Demo run-through on main + rpc fix
- Fixed (mine, rpc.py): one slow ping under load marked a node's link bad for 3 s, and with no relay available every request to it failed instantly (fragment PUTs, repair pulls). Direct is now tried as a last resort. Result: reset stores 200/200 files (was 160–194), 0 quorum failures, 16.5 s; kill-test MTTR 83 s → 40.5 s (detect 1.8 s). Reset now waits for ALIVE + unfenced nodes. Added the "Reconnecting to Vault…" banner.
- Scenes: healthy ✅ (200 files, IFL 3) · corruption ✅ (10 damaged → 10 found by scrub → 10 repaired) · cut cable ✅ (n5 via relay:n1, 0 repairs) · pull the plug ✅ (200/200, all ALIVE in 11 s, meta.recovered) · kill ⚠️ (works, repair still 29 s for 47 MB) · shared fate ❌ (J9) · Oracle ⚠️ (runs end to end; Vault mode reports 24 "lost" that are really temporarily unreadable).
- Bugs for owners: **Jaiveer** repair throughput (Oracle settle ends with 228 under-protected); nodes stay PARTITIONED forever after a metadata-only restart. **Soum** gateway GET 503 for oracle/k000 while metadata has it and ≥1 holder should be up; 502s during the run. **Urooz** checker should retry the final GET for ~10–15 s before calling a key lost, and the sample's technical line says 404 when the response was 503.
