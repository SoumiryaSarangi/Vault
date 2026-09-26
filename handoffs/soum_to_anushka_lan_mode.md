# Handoff: soum -> anushka
Date/Time: 2026-09-26
Status: OPEN (FYI + review; nothing blocks)
Topic: LAN mode (4-laptop demo) touched your files. Please review; also two small things for you

What I need changed / added:
- Nothing is required. I edited your files myself, on the approval Soum relayed. Please review the diff on
  `soum/data-plane` (commits `5afc71d`, `dd0f9e5`, `eeccd02`, plus the docs commit) and merge when happy.
- Two follow-ups are yours if you want them:
  1. **2D cluster view:** with 4 machines, the bottom machine's name and status ("Doctor's Desk / Healthy") overlap
     its power-strip label ("Power Strip B"), and the left strip label sits under its node circle. I didn't touch
     `ClusterScene2D.tsx`.
  2. **Fate page:** it still relabels with PATCH meta `/v1/nodes/{id}/labels` directly. Renaming lives on the
     Overview machine cards (pencil). Add it to the Fate page only if you want it there too.

Exact files or endpoints involved (all edited, all yours):
- `backend/vault/common/config.py`:
  - `ClusterCfg.bind_host`, `Ports.agent`, `VaultConfig.listen_host()`, `node_addr()`;
  - env vars `VAULT_HUB` and `VAULT_NODE_ADDR`, both applied in `load_config()`.
  - Single-laptop behaviour is unchanged: `bind_host` defaults to `host`.
- `backend/vault/common/service.py`: uvicorn binds `cfg.listen_host()`. `LOCAL_ORIGINS` also allows private LAN IPs.
- `backend/vault/common/models.py`:
  - `Proc.remote`, `Proc.host`, `Proc.note`, `Inventory.sent_at`;
  - new `JoinRequest`, `JoinResult`, `AgentStatus`, `RenameRequest`, `ClusterInfo`.
- `web/lib/contracts.ts`: mirrors the model changes above.
- `backend/vault/supervisor/anushka_procs.py`:
  - remote children (`Child.host`, `Child.agent`);
  - start/kill go to the agent over rpc (target `agent:<id>`);
  - `poll_agents()` every 1 s;
  - `kill_many`/`start_many` skip a laptop that fails mid-call;
  - `drop_added_nodes` and `kill_all` leave remote machines alone.
- `backend/vault/supervisor/anushka_cluster.py`:
  - `join()`, `rename()` (kept in `renames`, re-applied after every reset), `info()`;
  - reset wipes remote data through the agents.
- `backend/vault/supervisor/anushka_app.py`: `POST /nodes/join`, `POST /nodes/{id}/rename`, `GET /cluster/info`. Starts the agent poller.
- `backend/vault/__main__.py`: `up --lan` and `join`.
- `vault.lan.yaml` (new): only n1 is listed. It uses `data/lan` and `logs/lan`, with `dead_after_s: 8`.
- `web/lib/api.ts`: service URLs default to the page's own host (`localhost` maps to 127.0.0.1), so `http://<hub-ip>:3000` works from any laptop. `NEXT_PUBLIC_*` still wins.
- `web/lib/chaos.ts`: `chaos.rename()` and `chaos.clusterInfo()`.
- `web/components/console/NodeCard.tsx`:
  - a pencil (hover/focus) opens an inline rename;
  - a laptop icon plus IP for joined machines.
- `web/components/console/NodeList.tsx`: passes each card its `Proc`.
- `web/components/console/ChaosDock.tsx`:
  - Add has two tabs, **Simulated** and **Real laptop** (the latter shows the join command with a Copy button);
  - strips come from the live labels;
  - menus say "asleep or off the network" instead of "off".
- `web/next.config.ts`: `allowedDevOrigins` for private LAN IPs. Next 16's dev server otherwise blocks its own assets when the page is opened over an IP.
- Docs: `docs/soum_lan_demo.md` (new), `docs/contracts/README.md` item 14, `docs/MASTER_PLAN.md` §10 (decision noted).

Expected input / output (with example):
- `POST :7070/nodes/join {"node_id":"n2","display_name":"Doctor's Desk","labels":{"power":"B"},"host":"192.168.1.102","agent_port":7071}`
  returns `{"node_id":"n2","port":7102,"display_name":"Doctor's Desk","labels":{…},"new":true}`.
  - 409 if the id runs on the hub, or if another laptop still answers as that id.
  - 502 if the hub can't reach the agent back (firewall).
- `POST :7070/nodes/n4/rename {"display_name":"Pharmacy Laptop"}` returns a `Proc` with the new name. Metadata is updated too.
- `GET :7070/cluster/info` returns `{"hub":"192.168.1.101","lan":true,"agent_port":7071,"next_node_id":"n5"}`.

Why it is needed (which feature depends on it): the 4-laptop demo, node rename, and "add a real laptop".

Deadline (hour of hackathon): review before the demo rehearsal.

What I will use as a stub until then: none. It's built and tested:
- 269 tests pass;
- a live run on one laptop with 3 agents: turn off/on through the agents, a sleep/wake stand-in (OS-suspended agent and node → asleep note → DEAD → rebuild → rejoin), reset and seed, rename, and the dashboard loaded over the LAN IP.

Gated? yes. Soum says Anushka approved this work. Please confirm by setting Status: DONE.
