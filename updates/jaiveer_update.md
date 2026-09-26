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

## [Hour 4] J5 Uploads, commit, objects (seam I1 → Soum's gateway)
- What was done: upload plan, the commit transaction (§4.1 rules 1–5), abort, manifest, list, delete (tombstone), object health (IFL + min_cut), inspect for the Oracle.
- Files created/changed: `backend/vault/metadata/jaiveer_uploads.py`, `backend/vault/metadata/jaiveer_objects.py` (objects part), `backend/vault/tests/test_jaiveer_commit.py` (25 tests).
- Endpoints (all :7000):
  - `POST /v1/uploads` `UploadRequest` → `UploadPlan` · `404 no_bucket` · `400 bad_size` · `412 precondition_failed` (P1 if_match / if_none_match) · `503 not_enough_machines` (fewer than W ALIVE machines)
  - `POST /v1/uploads/{version_id}/commit` `CommitRequest` → `CommitResult` · `409 quorum_not_met` (detail `{got, w, chunk_idx}`; version is aborted; `write.quorum_failed` emitted) · `410 upload_expired` · `404 no_upload` · `400 size_mismatch|bad_commit` · `412`
  - `POST /v1/uploads/{version_id}/abort` → 204 (idempotent)
  - `GET /v1/objects/{bucket}/{key:path}` → `Manifest` (ok fragments only, each with current `addr`, `route`, `slow`) · `404 not_found|no_bucket`
  - `GET /v1/objects/{bucket}?prefix=&limit=1000` → `ObjectList` (live objects, sorted by key)
  - `DELETE /v1/objects/{bucket}/{key:path}` → `DeleteResult` (`{"deleted":false,"commit_seq":null}` for a missing/already-deleted key; writes nothing)
  - `GET /v1/objects/{bucket}/{key:path}/health` → `ObjectHealth` (IFL, target, per-chunk durable/available, shared domains, min_cut)
  - `GET /v1/inspect/objects?cursor=&limit=500` → `InspectPage` (live + deleted; `durable_min`, `target`, `ifl`; cursor = `"<bucket>/<key>"`)
- Rules as implemented:
  - Plan: n targets (frag_idx 0..n-1) + `gateway.spares` (2) spares per chunk, from `place()` (fate-aware; Naive = pure ring), each with the node's current epoch. Only ALIVE, unfenced, not-full machines. Fewer than n but ≥ W → a shorter target list; the chunk gets repaired after commit.
  - Commit counts a fragment only if `ok`, on a known node that isn't DEAD/RETIRED, `frag_idx` in range, and (replication) its sha equals the chunk sha. W is checked on the largest set of distinct frag_idx on distinct nodes (so one node reporting twice counts once). A spare simply reports the frag_idx it stored.
  - W = policy.w, or 1 when `safety.quorum_writes` is off (Naive).
  - Committing an already-committed version returns the same result (safe retry after a timeout).
  - Chunks below n → `repair` jobs (reason `under_replicated`), P0 if durable ≤ needed else P1. J7 dispatches them.
- How to run / test it: `python -m pytest -q backend/vault/tests/test_jaiveer_commit.py` (25 passed); full suite 114 passed.
- **Exact sequence (Soum, Anushka)** — metadata up (`python -m vault up`, or `python -m vault.metadata` + `python tests/jaiveer/jaiveer_fake_nodes.py` in a second terminal so machines are ALIVE). In PowerShell use `curl.exe`, and put JSON bodies in files to avoid quoting trouble:
  ```
  curl.exe -X POST localhost:7000/v1/buckets -H "Content-Type: application/json" -d "@bucket.json"      # {"name":"clinic","policy":"rep3"}
  curl.exe -X POST localhost:7000/v1/uploads -H "Content-Type: application/json" -d "@upload.json"      # {"bucket":"clinic","key":"hello.txt","size":5}
  #  → UploadPlan: version_id, chunks[0].targets = [{frag_idx:0,node_id:"n5",addr,epoch:1}, {1,"n1"}, {2,"n3"}], spares [n6, n2]
  #  PUT each fragment to its target node (S3/S5), then report every attempt:
  curl.exe -X POST localhost:7000/v1/uploads/<version_id>/commit -H "Content-Type: application/json" -d "@commit.json"
  #  commit.json = {"object_sha256":"<sha of whole file>","size":5,"chunks":[{"chunk_id":"<from plan>","sha256":"<chunk sha>",
  #                 "frag_size":5,"fragments":[{"frag_idx":0,"node_id":"n5","sha256":"<frag sha>","ok":true}, …]}]}
  #  → {"version_id":…,"seq":1,"commit_seq":1,"etag":"<object sha>","under_replicated_chunks":0}
  curl.exe localhost:7000/v1/objects/clinic/hello.txt            # Manifest
  curl.exe "localhost:7000/v1/objects/clinic?prefix=hel"         # list
  curl.exe localhost:7000/v1/objects/clinic/hello.txt/health     # IFL 3
  curl.exe -X DELETE localhost:7000/v1/objects/clinic/hello.txt  # {"deleted":true,"commit_seq":2}
  curl.exe localhost:7000/v1/inspect/objects                     # Oracle view
  ```
- Known issues / TODO: conditional-write conditions (P1) are held in memory between plan and commit, so a metadata restart mid-upload drops them. Fragment rows of aborted versions are left for Soum's GC (S8) to trim. Health/inspect compute IFL on request (fine for hundreds of files; J9 adds caching via the auditor).
- Anything other teammates must know or do:
  - **Soum:** switch the gateway from `soum_fake_meta.py` to the real metadata once this is on `main`. On `< W` after spares you can simply POST the commit (metadata answers 409, aborts the version and emits `write.quorum_failed`, which is the event your task mentions), or call `/abort` (no event). `write.quorum_failed` only fires from commit.
  - **Urooz:** `GET /v1/inspect/objects` is ready for the Oracle checker (I7).

## [Hour 5] J6 Detector, cluster snapshot, SSE stream
- What was done: failure detection (§4.5 state machine, §4.6 epochs), metadata's own pings (§4.7), the cluster snapshot (§8.2) with summary levels and sentences (DESIGN §6.2), abnormal links with relays, the live SSE stream, ground-truth fault reports and gateway participant reports. **Plus a J1 bug fix** (below).
- Files created/changed:
  - `backend/vault/brain/jaiveer_detector.py` (tick every 100 ms + pinger), `jaiveer_membership.py` (state machine support, reach rows, evidence), `jaiveer_incidents.py` (FaultLog + open_incident_tx), `jaiveer_summary.py` (levels + sentence; J8 refines)
  - `backend/vault/metadata/jaiveer_cluster.py` (snapshot builder, `GET /v1/cluster`, recovered-in-grace), `jaiveer_stream.py` (`GET /v1/stream`), `jaiveer_reports.py` (`/v1/incidents/fault`, `/v1/participants/{pid}/report`), `jaiveer_app.py` (Brain fields, `start_loops` flag)
  - `backend/vault/common/jaiveer_phi.py` (**fix**), `backend/vault/tests/test_jaiveer_detector.py` (15 tests), `test_jaiveer_phi.py` (+2), test_jaiveer_meta/commit (loops off in tests)
- Endpoints:
  - `GET /v1/cluster` → `Snapshot` (cached 500 ms; also refreshes metadata's rpc topology)
  - `GET /v1/stream` → SSE: on connect the last 100 `event`s, then `snapshot` every 500 ms and an `event` per new Event (`data` = JSON of `Snapshot` / `Event`)
  - `POST /v1/incidents/fault` `FaultReport` → 204 (fault_at for incidents; `slow`/`disk_full` become node `faults` chips; `clear` clears them)
  - `POST /v1/participants/{pid}/report` `ParticipantReport` → 204 (gateway reach row → links; `stats` → snapshot `traffic`). read.failover aggregation comes in J7.
- State machine (tick 100 ms): ALIVE → SUSPECT at φ ≥ 8 (~1.6–2.0 s of silence) → PARTITIONED if any participant reached it ok in the last 2 s, else DOWN after confirm_ms (1 s) → DEAD after dead_after_s from the **last sign of life** (heartbeat or peer evidence). DEAD: epoch++, fragments → `lost`, incident (`fault_at` from the supervisor's `node_dead`/`power_cut` report if present, else last heartbeat), repair jobs for every fragment no longer durable elsewhere (P0 if one copy left, else P1). Heartbeat recoveries: SUSPECT/PARTITIONED → ALIVE at once; DOWN → ALIVE after 3 heartbeats in 2 s (`node.recovered_in_grace`, counted in kv `repairs_avoided`). Startup grace (10 s): no DOWN/DEAD; a node that never heartbeats after the grace → DOWN. Flags: `slow` (p50 RTT > 300 ms or ≥ 20% loss from others' pings) → `node.slow`; `fenced` → `node.fenced`; route direct↔relay → `link.relayed` / `link.restored`.
- **J1 fix (φ):** the TECH_STACK §6.2 formula computes `exp(-y·…)`, which underflows to 0 after ~6 s of silence, and then `log10(0)` raises. The detector tick would have failed on every pass, so a killed machine would have sat at DOWN forever and never been rebuilt. Now uses the identical but stable form `φ = (log1p(e) − a)/ln10`; a test checks it matches the original formula exactly on 0–3 s and never raises up to an hour.
- How to run / test it:
  - `python -m pytest -q` → 131 passed.
  - Live (what I ran): `VAULT_DEMO=1`, `python -m vault.metadata`, `python tests/jaiveer/jaiveer_fake_nodes.py --stop n3 --after 6`, 3 files stored, `curl -N localhost:7000/v1/stream` → `node.suspect` → `node.down` → `node.dead` with **detect = 2.61 s**, DEAD at +8.0 s, 2 repair jobs queued (P1), summary "Your data is safe, but 2 files have fewer copies than usual…", 44 snapshots in 22 s.
- Known issues / TODO: repair dispatch (jobs stay queued) is J7. `repair.mbps`, job progress and `traffic` fill in as J7 and Soum's S7 land. DRAINING/REJOINING machines aren't failure-detected yet (rare in the demo).
- Anything other teammates must know or do:
  - **Anushka:** `/v1/cluster` exists now, so reset's "wait for ALIVE" works (with real heartbeats). SSE is at `GET /v1/stream` (events `snapshot` and `event`). `links` lists abnormal links only, with a `relay` when some node can reach both ends. Node `faults` come from your `FaultReport`s (`slow`, `disk_full`).
  - **Soum:** heartbeats now drive SUSPECT/DOWN/DEAD for real; send `reach` rows for every peer + `meta` in each heartbeat (PARTITIONED and links depend on them). Gateway: `POST /v1/participants/gw/report` with `reach` + `stats` once a second (S7). Metadata pings every node at `GET /v1/ping` once a second.

## [Hour 6] J7 Scheduler + repair + reports
- What was done: the rebuild loop. The scheduler scans every 500 ms and queues repairs; the dispatcher runs them against the nodes (pull / move / trim) within the limits; incidents track progress and close with MTTR. Fragment reports turn corrupt/missing copies into repairs. Plus Anushka's note: a gentler sentence while no machine has checked in yet.
- Files created/changed: `backend/vault/brain/jaiveer_scheduler.py`, `jaiveer_repair.py`, `jaiveer_incidents.py` (progress/complete/corruption incident), `jaiveer_summary.py` (startup sentence), `backend/vault/metadata/jaiveer_reports.py`, `backend/vault/tests/test_jaiveer_repair.py` (7 tests).
- Endpoints:
  - `POST /v1/reports/fragment` `FragmentReport` → 202. corrupt/missing → row state, `fragment.corrupt`/`fragment.missing`, repair job (P0 if ≤ needed durable copies left, else P1) in an open `corruption` incident. `unreachable`/`slow` are ignored (the detector handles them).
  - `GET /v1/repair` → `RepairStatus` (queued by priority, active, last 20 finished).
  - `POST /v1/scrub {nodes|null}` → 202, fans out `POST /v1/scrub` to the nodes (default: every live one).
  - `POST /v1/participants/{pid}/report` now also aggregates `stats.failover_reads` into one `read.failover` per node per 10 s.
- Behaviour: scan = chunks of current committed versions with distinct durable frag_idx < n → one repair per missing frag_idx (chunks already below `needed` are skipped: nothing can rebuild them). Dispatcher: max 8 running, 2 per source/target node, 50 MB/s token bucket, 3 attempts (1/3/9 s), then `repair.failed` (the next scan re-queues it). Target = `choose_additional` (fate-aware); sources best-first (ALIVE, direct, not slow); EC uses `ec_rebuild` with every other piece. The target's returned sha must equal the expected one, or the attempt fails (never trusted). Success → row ok, old corrupt/missing rows for that piece dropped. Move = pull to the target, then `DELETE` on the source (unreachable → a P4 trim job). `safety.repair` off → nothing dispatched (jobs still visible in `/v1/repair`). Events: `repair.started` (once per incident), `repair.progress` (≤ 1/s), `repair.completed` with `mttr`, `d`, `g`, `r`, `bytes` (DESIGN §6.3), `repair.blocked` (≤ 1 per 10 s per reason), `repair.failed`.
- How to run / test it: `python -m pytest -q` → 175 passed. The tests run the real scheduler + dispatcher against a fake node API (kill → all chunks back to 3 copies, no pulls to the dead node, incident closed with MTTR; naive = no dispatch; retries → failed; bad sha rejected; corrupt report → rebuilt from a verified copy; move = pull then delete).
- Known issues / TODO: **the live kill → rebuild run needs Soum's `POST /v1/fragments/{fid}/pull` (S4)**, which is still a placeholder on main. Until then real repairs fail with 404/405 and retry. `scrub.completed` events come with J8.
- Anything other teammates must know or do:
  - **Soum (S4 pull):** I call `POST /v1/fragments/{fid}/pull` on the target with `PullRequest` JSON and header `X-Vault-Epoch: <target epoch>`, timeout 20 s. Replication: `mode:"copy"`, `sources` = other copies best-first; each source has **its own** fid (`<chunk>_f<its frag_idx>`), so fetch that fid and store the bytes under the request's `fid`. EC: `mode:"ec_rebuild"`, `ec:{k,n,frag_idx}`, sources = the other pieces. Reply `201 PullResult`; I check `sha256 == expected_sha256`. Moves and trims send `DELETE /v1/fragments/{fid}` with `X-Vault-Epoch`, and 204/404 both count as done.
  - **Anushka:** `repair` in the snapshot now fills (`active`, `mbps`), and `incident` closes with `recovered_at`. Before any machine heartbeats, the top bar says "Vault is starting: waiting for machines to check in (0 of 6 so far)."

## [Hour 6] J8 Incidents, metrics, summary
- What was done: `GET /v1/metrics` (PRD §8 definitions, `Metrics` §8.3), `GET /v1/incidents`, `scrub.completed` events, corrupt-copy counter. The summary level + sentence (DESIGN §6.2) already shipped in J6/J7: ok → degraded (rebuilding, % done) → ok, at_risk, critical (names the machines to turn back on), Naive prefix, startup "waiting for machines".
- Files created/changed: `backend/vault/brain/jaiveer_metrics.py`, `backend/vault/metadata/jaiveer_cluster.py` (+ routes, scrub.completed), `backend/vault/metadata/jaiveer_reports.py` (availability window, corrupt counter), `backend/vault/tests/test_jaiveer_repair.py` (+2 tests).
- Endpoints: `GET /v1/metrics` → `Metrics` · `GET /v1/incidents?limit=20` → `IncidentList` (newest first).
- Definitions as implemented: availability_60s = gateway ok ÷ (ok + failed) over 60 s of `POST /v1/participants/gw/report` stats (1.0 with no traffic); readable_now_pct = live files with ≥ needed pieces on reachable machines; overhead = healthy raw bytes ÷ logical bytes, cluster + per policy; last_incident = newest incident with something to repair, detect = detected − fault, grace = repair_started − detected, repair = recovered − repair_started, mttr = recovered − fault (**the three add up**; a test checks it); incidents_avg_mttr_s over recovered ones; unnecessary_repairs_avoided = DOWN machines that came back before dead_after_s; repair bytes_total + MB/s now; rebalance.last from kv `rebalance_last` (**Soum:** write `RebalanceLast` JSON there from S9); ifl histogram/min/at-risk; scrub checked_last_pass (sum of nodes' last pass) + corrupt_found_total.
- How to run / test it: `python -m pytest -q` → 177 passed. Test: kill → degraded → rebuild → ok, `last_incident` parts add up to MTTR, `/v1/incidents` shows the closed `node_dead` incident.
- Known issues / TODO: `scrub.completed` has no pass duration/MB yet (the heartbeat's `ScrubStatus` doesn't carry them).
- Anything other teammates must know or do: **Anushka:** MTTR tile = `metrics.last_incident` (`detect_s`, `grace_s`, `repair_s`, `mttr_s`). All inputs are live except availability, which needs Soum's gateway stats (S7).

## [Hour 6] Fix: false node.slow at boot (Soum's handoff `soum_to_jaiveer_slow_flag_at_startup.md`)
- What was done: the slow flag no longer fires while machines are booting. No slow checks during `startup_grace_s`; a machine isn't judged until it has been ALIVE for 5 s; reach rows from peers that are themselves still starting (or not ALIVE) are ignored; the ping-loss rule needs ≥ 2 peers. `node.slow` now carries `data.why`, e.g. "p50 rtt 420ms > 300ms" or "ping loss 3/5 ≥ 20%".
- Files created/changed: `backend/vault/brain/jaiveer_detector.py`, `backend/vault/tests/test_jaiveer_detector.py` (+2 tests: boot with every ping failing → no slow; loss rule needs 2 peers and says why).
- How to run / test it: `python -m pytest -q` → 179 passed.
- Anything other teammates must know or do: **Anushka:** the `node.slow` technical template in `common/events.py` is still "p50 rtt {ms}ms > {limit}ms", which reads wrong for the loss rule. Suggest changing it to `"{why}"` (every `node.slow` event now carries `why`). Your file, so your call.

## [Hour 7] Repair speed (Anushka: 47 MB took 64.9 s, MTTR 83 s)
- What was done: measured on the real stack (supervisor + 6 real nodes + gateway, 200 seeded files, `VAULT_DEMO=1`, kill n3), then removed four bottlenecks:
  1. The scheduler scan opened a write transaction (an fsync'd commit) every 500 ms even with nothing new → now it only writes jobs that aren't already queued/running.
  2. The dispatcher idled up to 200 ms after every finished job → it now wakes the moment a job ends.
  3. It re-planned every queued job each pass even when all machines were at their 2-job limit → stops as soon as no slot is free (and plans at most 3× the free slots).
  4. Sources always started from the same machine → least-busy source first.
- Result (same machine, with 2 live-stream clients + /metrics, /repair, /cluster, /fate polled every second): **51.7 MB rebuilt in 1.8 s, MTTR 10.1 s (detect 2.6 + wait 5.6 + rebuild 1.8)**. Before the fix, without that load: 6.4 s rebuild, MTTR 16.3 s.
- Note: after n3 dies, every rebuild must land on n4 (the only machine left on its own power strip), so `repair.per_node: 2` caps it at 2 at a time. If Windows is still slow (fsync is much slower there), raising `repair.per_node` to 4 in vault.yaml is the next lever (your file, your call).
- Files: `backend/vault/brain/jaiveer_repair.py`, `backend/vault/brain/jaiveer_scheduler.py`.

## [Hour 7] J9 Shared-Fate Auditor (headline feature)
- What was done: audit every 5 s and on `request_audit` (label PATCH already calls it); per-file effective copies, levels safe/limited/at_risk, greedy make-before-break moves (never lower IFL), advice, cluster-wide risks, `GET /v1/fate`.
- Files created/changed: `backend/vault/brain/jaiveer_auditor.py`, `backend/vault/metadata/jaiveer_cluster.py` (`GET /v1/fate`), `backend/vault/tests/test_jaiveer_auditor.py` (5 tests).
- Endpoints: `GET /v1/fate` → `FateReport` (fate_keys, domains incl. cluster-wide, histogram, at-risk files with shared domains, advice, last_audit_at). `GET /v1/objects/{b}/{k}/health` (J5) shows IFL + min_cut.
- Behaviour: for each chunk below target, try every single move a → b (a in the chunk's min cut, b ALIVE/unfenced/not full) and take one that strictly raises IFL; equally good moves are spread over machines. `move` job P2 (IFL 1) or P3 (only if b < 85% full); the J7 dispatcher pulls to b, verifies, then deletes a. No moves in Naive mode (report only) or while the chunk has repair work in flight. Advice (`fate.limited`, once per layout): D1 sentence for ec42 exactly as in MASTER_PLAN; otherwise "{Power Strip A} feeds 4 of 6 machines. Give {Lab Laptop or Records Room} its own power supply to get 3 independent copies." Events: `fate.at_risk` (per shared domain, when it appears), `fate.fixed` (when it's gone), `fate.limited`, `fate.cluster_wide` (once).
- How to run / test it: `python -m pytest -q` → 238 passed. The test runs the §4.12 demo with the real dispatcher on a fake node API: relabel n3, n5 → power A → the files on {n1,n3,n5} drop to IFL 1 (`fate.at_risk`: "… on Power Strip A") → moves raise them all to 2 (`fate.fixed`) → one advice line (exact text above) → every move was pull-then-delete → with Power Strip A off, 0 unreadable files. ec42: IFL 2, no moves, one D1 advice line.
- Anything other teammates must know or do: **Anushka (Fate page):** `GET /v1/fate`; advice in `advice[].human`; the demo relabel is `PATCH /v1/nodes/{id}/labels`. **Urooz (pitch):** the advice sentence is exactly the one above.
