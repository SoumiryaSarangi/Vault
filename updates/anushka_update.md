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
