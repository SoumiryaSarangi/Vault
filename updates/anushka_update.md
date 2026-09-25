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
