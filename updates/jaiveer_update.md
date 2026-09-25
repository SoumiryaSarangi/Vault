# Jaiveer: updates

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

## [Hour ? · Sat 26 Sep, ~04:00 IST] J1 phi + J2 placement ring + J3 effective copies (IFL)
- What was done: J1–J3 pure modules implemented (no I/O) with tests. Placeholder signatures unchanged.
- Files created/changed:
  - `backend/vault/common/jaiveer_phi.py`, `backend/vault/tests/test_jaiveer_phi.py` (11 tests)
  - `backend/vault/common/jaiveer_placement.py`, `backend/vault/tests/test_jaiveer_placement.py` (29 tests)
  - `backend/vault/common/jaiveer_fate.py`, `backend/vault/tests/test_jaiveer_fate.py` (21 tests)
- Functions exposed (signatures exactly as in the scaffold):
  - `Phi(window=100, min_std_s=0.2, first_s=0.5)`, `.heartbeat(now)`, `.phi(now) -> float` (0.0 before first heartbeat)
  - `Ring(vnodes_per_node=128)`, `.rebuild(nodes: list[NodeView])`, `.walk(h) -> Iterator[str]`, `.node_ids` (new read-only property)
  - `active_fate_keys(nodes, keys=FATE_KEYS) -> list[str]`
  - `place(ring, nodes: dict[str, NodeView], chunk_id, count, holders=(), exclude=None, fate_keys=None) -> list[str]`
  - `choose_additional(ring, nodes, chunk_id, holders, exclude=None, fate_keys=None) -> Optional[str]`
  - `build_domains(nodes, keys) -> (dict[Domain, set[str]], list[Domain])`, `ifl(holders, needed, domains) -> (int, list[Domain])`, `target_ifl(policy) -> int`
  - `ifl_cache_info()` (new, lru_cache stats for tests/debugging)
- How to run / test it: `python -m pytest -q backend/vault/tests/test_jaiveer_phi.py backend/vault/tests/test_jaiveer_placement.py backend/vault/tests/test_jaiveer_fate.py` → 61 passed. Full suite `python -m pytest -q` → 70 passed.
- Behaviour choices (not spelled out in the docs):
  - `fate_keys=None` → `active_fate_keys(nodes)` (FATE_KEYS minus cluster-wide). `fate_keys=[]` → pure ring order (Naive).
  - "full" isn't a NodeView field: a node is full when `"disk_full" in faults` or `capacity > 0 and disk_used >= capacity`.
  - Capacity weight = `capacity / 2 GiB` (fixed reference, so adding a bigger machine doesn't reshuffle others); capacity 0 → weight 1; capped at 8×.
  - Holders are kept first even if not ALIVE (the caller decides what's durable); new picks still avoid their fate domains.
  - A missing label never "shares" with another node.
  - `ifl` min_cut prefers label domains over single machines (reads "Power Strip A", not "Reception PC") and collapses domains with the same footprint. Cache key includes each domain's footprint, so a relabel can't return a stale IFL.
- Known issues / TODO:
  - **Spec mismatch (needs Anushka's call):** tasks/jaiveer_tasks.md J3 says "ec42 on all 6 → IFL 3". By the ARCHITECTURE §4.12 definition it is **IFL 2** with the default labels: Power Strip A + B take out 4 of 6 fragments and k=4 are needed (it does survive any one strip, and any two machines). IFL 3 needs each machine on its own power/switch/disk batch. Implemented per §4.12; tests cover both cases. Consequence for J9/dashboard: `archive` (ec42) files will show as **Limited (amber)** in the default cluster, and the auditor can't fix it by moving, so it will emit `fate.limited` advice. Options: accept (honest, good Q&A material), or change the demo labels (gated: vault.yaml).
  - Phi: the 2.2 / 7.3 / 18 reference values hold for a steady 500 ms stream. Right after the first heartbeat (bootstrap window only, mean 562 ms) φ is slightly lower (1.5 s → 6.4); still ≥ 8 by 2.0 s.
- Anything other teammates must know or do:
  - Soum: `place` / `choose_additional` are ready on this branch for the rebalancer and repair target. Pass `nodes` as a dict id → NodeView. `choose_additional` returns None when nothing eligible exists.

## [Hour 4] J4 Metadata DB + app skeleton + membership basics
- What was done: SQLite source of truth (schema verbatim from §3.5), the metadata app with a real BrainContext, startup recovery, and the J4 endpoints. Fake nodes stub for testing before S4.
- Files created/changed:
  - `backend/vault/metadata/jaiveer_schema.sql` (§3.5 verbatim), `jaiveer_db.py` (Database: 1 writer behind asyncio.Lock + 1 reader, WAL, `BEGIN IMMEDIATE … COMMIT`, synchronous FULL / OFF in naive)
  - `backend/vault/metadata/jaiveer_app.py` (create_app, `Brain` = BrainContext, lifespan, event sink + name resolver, `meta.recovered`, brain-loop starter)
  - `backend/vault/metadata/jaiveer_cluster.py` (nodes, config, mode routes), `jaiveer_objects.py` (buckets), `jaiveer_stream.py` (EventBus, GET/POST /v1/events)
  - `backend/vault/brain/jaiveer_membership.py` (in-memory NodeView table, register/heartbeat bookkeeping, φ per node)
  - `backend/vault/tests/test_jaiveer_meta.py` (17 tests incl. kill -9), `tests/jaiveer/jaiveer_fake_nodes.py`
- Endpoints (all on :7000):
  - `POST /v1/buckets {name, policy}` → 201 `Bucket` · `409 bucket_exists` · `400 unknown_policy|bad_bucket_name` · `GET /v1/buckets` → `BucketList`
  - `POST /v1/nodes/register` `RegisterRequest` → `RegisterReply` (accepts unknown ids n7+; DEAD → REJOINING)
  - `POST /v1/nodes/{id}/heartbeat` `Heartbeat` → `HeartbeatReply` (records arrival, φ, disk, reach; `X-Vault-Via` → route `relay:<via>`)
  - `POST /v1/nodes/{id}/inventory` `Inventory` → `InventoryResult` (calls `soum_reconciler.reconcile_inventory` once it exists; marks REJOINING → ALIVE)
  - `PATCH /v1/nodes/{id}/labels` `LabelsPatch` → `NodeView` (persists; calls `jaiveer_auditor.request_audit` once J9 exists)
  - `GET /v1/config` → full effective config **flat**, plus `config_version` and `mode` · `PATCH /v1/config {partial}` (cluster/nodes/policies/placement/web → `400 not_patchable`; invalid → `400 bad_config`) · `POST /v1/mode {mode}` → `ModeResult`
  - `GET /v1/events?after_id=&limit=` → `EventList` · `POST /v1/events` `ExternalEvent` → 201 (only `chaos.*` / `oracle.*`)
  - Python: `Brain` implements `brain_api.BrainContext` (`cfg, rpc, db, now(), nodes(), node(), safety(), enqueue_job(...), emit(...)`); `enqueue_job_tx(conn, ...)` for use inside a write transaction.
- How to run / test it:
  - `python -m pytest -q backend/vault/tests/test_jaiveer_meta.py` (17 passed); full suite 89 passed.
  - Live: `python -m vault up`, then `curl -X POST localhost:7000/v1/buckets -H "Content-Type: application/json" -d "{\"name\":\"clinic\",\"policy\":\"rep3\"}"` and `curl localhost:7000/v1/events`.
  - Without Soum's nodes: `python -m vault.metadata` in one terminal, `python tests/jaiveer/jaiveer_fake_nodes.py -v` in another (`--stop n3 --after 5` makes n3 go silent for the J6 kill test). Don't run it next to real nodes.
- Behaviour choices:
  - Heartbeat reply is `state: DEAD` (= "re-register", §4.6) for an unknown node, a DEAD node, a stale epoch, **or a node that hasn't registered since metadata restarted**. Re-registering doesn't change the epoch unless the node really died.
  - Labels and display names: metadata's copy wins after the first register (relabels happen here); addr and capacity come from the node.
  - Startup (§4.14): pending uploads older than `gc.upload_timeout_s` → aborted; running jobs → queued; `meta.recovered` carries files/versions/pending/ms.
  - `POST /v1/mode` always bumps `config_version`; `config.mode_changed` is emitted only when the mode actually changes.
- Known issues / TODO: `/v1/cluster`, SSE, detector (JOINING/ALIVE only for now: no SUSPECT/DOWN/DEAD yet) are J6. Brain loops are started automatically as soon as their modules define `async def run(ctx)`.
- Anything other teammates must know or do:
  - **Soum:** `GET /v1/config` returns the VaultConfig fields flat **plus** `config_version` and `mode`, so strip those two before `VaultConfig.model_validate(...)` (it forbids extra keys). On heartbeat reply `DEAD`, re-register and adopt the returned epoch. Send `X-Vault-Via` only through `rpc.forward` (already does).
  - **Anushka:** reset's bucket creation (409 ignored), `/v1/mode` and `/v1/events` now work. `/v1/cluster` is still 404, so reset's ALIVE-wait is skipped until J6. `/v1/incidents/fault` arrives in J7.
