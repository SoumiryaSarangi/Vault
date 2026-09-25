# Soum: Data plane ("the bytes")

You own everything that touches bytes: erasure coding, the storage node (crash-safe disk format, verification, scrubbing, heartbeats, pull-repair, relay, node-level chaos) and the gateway (streaming PUT/GET, quorum writes, EC, verified reads with failover). Later: reconciliation, garbage collection and rebalancing (brain modules that run inside the metadata process).

**Read first (only these):** TEAM_PROTOCOL.md · MASTER_PLAN.md · OWNERSHIP.md · docs/contracts/README.md · ARCHITECTURE §0 rows "Storage node" and "Gateway" · `common/models.py` · `common/rpc.py` · `common/netsim.py` · `common/service.py`.
**Branch per task:** `soum/<task>` e.g. `soum/s1-ec`. Commit `soum: <what>`. PR to `main`, tell Anushka.
**After every task:** append to `updates/soum_update.md` (TEAM_PROTOCOL §6).

---

## ▶ START NOW: S1–S2 need nothing but the scaffold

Paste into Claude Code:
```
Read CLAUDE.md, docs/TEAM_PROTOCOL.md, tasks/soum_tasks.md, docs/ARCHITECTURE.md §3.3, §3.6, §4.4, §4.9, §4.14
and TECH_STACK §6.1 and §6.3. Implement task S1, then S2, exactly as specified in tasks/soum_tasks.md.
Keep the function signatures in backend/vault/common/soum_ec.py. Write the tests listed in each test
file's docstring. Run `python -m pytest -q` until green.
```

### S1. Erasure coding (≈30 min) · target H2.5
- **Files:** `backend/vault/common/soum_ec.py`, `backend/vault/tests/test_soum_ec.py`
- **Spec:** verified code in TECH_STACK §6.1; ARCHITECTURE §3.3 (pad to a multiple of k; fragments 0..k-1 data, k..n-1 parity; truncate to chunk size).
- **Done when:** round trip for sizes 0, 1, 1 MiB − 1, 1 MiB; decode from any 4 of 6; `ec_rebuild(want=i)` is byte-identical to fragment i.

### S2. Crash-safe fragment storage (≈1.5 h) · target H4
- **Files:** `backend/vault/node/soum_storage.py`, `backend/vault/tests/test_soum_storage.py`
- **Spec:** ARCHITECTURE §3.6 (layout `data/<node>/blobs/<version_id[2:4]>/<fid>.blk`, `tmp/`, `quarantine/`, `node.json`; `.blk` = `b"VLT1"` + uint32 BE header_len + header JSON (`FragmentHeader`) + payload), §4.14 node startup (delete `tmp/*`, count them, scan `blobs/`, build in-memory index `fid → (path, header)`), atomic write from TECH_STACK §6.3 (skip dir fsync on Windows), blocking I/O via `asyncio.to_thread`.
- **Suggested API** (yours to shape; the node app is the only caller): `Storage(root, node_id)`, `startup() -> discarded:int`, `put(header, payload, durable=True)`, `get(fid) -> (header, payload)` raising `Corrupt` after quarantine on SHA mismatch (when `verify_on_read`), `delete(fid)`, `inventory() -> list[InventoryItem]`, `disk_used()`, `count()`, `read_node_json()/write_node_json()`, per-fid `asyncio.Lock`.
- **Safety switches:** `durable_writes` off → direct write, no fsync, no rename. `verify_on_read` off → return bytes unchecked.
- **Done when:** tests cover round trip, no tmp left after success, startup deletes and counts `tmp/*.part`, corrupt payload → quarantined + error, index rebuilt from disk after "restart".

---

## Then, in order

### S3. Node API · H4 → H5
- **Files:** `node/soum_app.py` (replace the stub; keep `create_app(cfg, pid)`)
- **Spec:** ARCHITECTURE §7.3 table: `PUT/GET/HEAD/DELETE /v1/fragments/{fid}` with headers `X-Vault-Meta` (base64url JSON `FragmentHeader`), `X-Vault-Sha256`, `X-Vault-Epoch`; errors `400 checksum_mismatch`, `409 stale_epoch`, `409 corrupt`, `503 fenced`, `507 disk_full` (use `VaultHTTPError`); `GET /v1/ping` (real epoch), `GET /v1/health` (`NodeHealth`), `POST/GET /v1/scrub`.
- **Fencing:** lease expired (from `node.json`) → refuse PUT/DELETE, still serve verified GETs (§4.6). `safety.fencing` off → no lease/epoch checks. `verify_on_receive` off → store whatever arrives.
- **Done when:** with `vault up`, curl a fragment into n1 and back; flip a byte in the `.blk` on disk → GET returns `409 corrupt` and the file moves to `quarantine/`.

### S4. Node background loops + pull · H5 → H6.5 · **Jaiveer's detector waits on heartbeats (seam I3)**
- **Files:** `node/soum_heartbeat.py`, `node/soum_pinger.py`, `node/soum_pull.py`, `node/soum_scrubber.py`
- **Heartbeat** (§4.5, §4.6, §7.2): register on startup (send `discarded_on_startup`), `Heartbeat` every 500 ms via `get_rpc()` (the rpc relays automatically), persist lease expiry in `node.json`, reply `state: DEAD` → re-register and adopt the new epoch, push `Inventory` on register/rejoin and every 30 s (staggered). Watch `config_version` in replies → re-fetch `GET /v1/config` and apply safety switches (also set `get_rpc().relay_enabled`).
- **Pinger** (§4.7): every 1 s ping every node + meta (timeout 500 ms); reach row goes in the heartbeat; feed `get_rpc().update_topology(...)`.
- **Pull** (§4.8 step 3, §7.3): `POST /v1/fragments/{fid}/pull` with `PullRequest`: `copy` (fetch from sources in order, verify, store) or `ec_rebuild` (fetch k others, `ec_rebuild`, verify, store) → `PullResult` or `424 no_valid_source`.
- **Scrubber** (§4.9): walk all `.blk` at `scrub.rate_mbps`, recently modified first after restart; mismatch → quarantine + `POST /v1/reports/fragment {problem:"corrupt", context:"scrub"}`; paused when `safety.scrub` off.
- **Done when:** with `vault up` all 6 nodes heartbeat (Jaiveer's log/snapshot shows them); a pull copies a fragment between nodes and verifies it.

### S5. Gateway PUT/GET · H6.5 → H8.5 · **M2 first light**
- **Files:** `gateway/soum_app.py`, `gateway/soum_put.py`, `gateway/soum_get.py`
- **Spec:** ARCHITECTURE §4.1 (write path diagram + rules), §4.2 (read path), §4.3, §7.1 table. Stream the request body in 1 MiB chunks (never more than ~4 MiB in memory, ≤ 2 chunks in flight); whole-object SHA-256 → ETag; fragment PUTs ×n in parallel, wait ≥ W acks, use a spare on failure, `< W` after spares → abort → `503 quorum_not_met` (+ `write.quorum_failed` is emitted by metadata). EC: `ec_encode` per chunk; read data fragments 0..k-1 and concatenate, else decode. Headers only after the first chunk verifies. `X-Vault-Read-Path`. Every bad fragment → `POST /v1/reports/fragment` (read repair).
- **Safety switches:** `quorum_writes` off → ack after 1; `read_failover` off → first holder only; `verify_on_read` off → no gateway check.
- **Stub until J5 lands (~H6.5):** `tests/soum/soum_fake_meta.py`, a tiny FastAPI on :7000 that returns an `UploadPlan` (ring order from config) and accepts commits in memory. Delete it from your workflow once J5 is on `main`.
- **Done when:** `curl -T big50mb.bin :7080/clinic/big.bin` then `curl :7080/clinic/big.bin | sha256sum` matches; kill n2 → PUT still succeeds (spare), GET fails over; `ec42` bucket round-trips.

### S6. Relay + node chaos · H8.5 → H9.5
- **Files:** `node/soum_relay.py`, `node/soum_chaos.py`
- **Relay** (§4.7): `ANY /v1/relay/{target}/{path:path}`; require `X-Vault-Hop: 1` (else 400); forward once with `get_rpc().forward(target, method, "/"+path, origin=<X-Vault-From>, content=body, headers=..., params=...)`; stream back status, headers and body.
- **Node chaos** (§7.3): `POST /_chaos/corrupt` (`count` random or `fids`; `bitflip|zero|truncate|delete` on the payload, header intact) → `CorruptResult`; `POST /_chaos/disk_full {on}` → PUT returns 507. Register `ns.on_clear(...)` and `ns.add_state(...)` (see `common/netsim.py`) so `/_chaos/clear` and `GET /_chaos` include them.
- **Done when:** supervisor "cut meta↔n5" → n5 heartbeats arrive with `X-Vault-Via` (relay) and **no repair** is queued; corrupt 10 → scrub finds them.

### S7. Gateway routing + stats · H9.5 → H10.5
- **Files:** `gateway/soum_routing.py`, `gateway/soum_stats.py`
- **Spec:** §4.2 holder order (available → direct before relay → not slow → lowest RTT); per-second `GatewayStats` + gateway reach row → `POST /v1/participants/gw/report` (availability metric depends on it; include `failover_reads`). Hedged reads are P1: skip.

### S8. Reconciler + GC · H10.5 → H12 · runs inside metadata (seam I4)
- **Files:** `brain/soum_reconciler.py`, `brain/soum_gc.py`
- **Spec:** ARCHITECTURE §4.10 table (missing, corrupt, lost → ok, adopt, orphan) and GC loop (abort expired pending uploads, trim aborted + superseded after grace, trim over-replication keeping the copy with max IFL via `jaiveer_fate.ifl`). Use only `BrainContext` (`ctx.db`, `ctx.enqueue_job`, `ctx.emit`). Emit `node.rejoined` (kept/trimmed) and aggregated `gc.cleaned`.
- **Entry points** (fixed in `common/brain_api.py`): `reconcile_inventory(ctx, inv) -> InventoryResult`, `run(ctx)`.
- **Done when:** restart a DEAD node → its copies come back as ok or are trimmed; extra copies trimmed; superseded versions disappear after 30 s. Unit tests with a fake `BrainContext`.

### S9. Rebalancer · H12 → H13
- **Files:** `brain/soum_rebalancer.py`
- **Spec:** §4.13: `on_node_added` (move one fragment per chunk whose desired set now includes the new node; never lower IFL; report `moved_bytes / total_bytes` vs ideal 1/N), `on_drain` (move everything off, then RETIRED), `run(ctx)`. Moves at P3 via `enqueue_job(kind=move, reason=rebalance|drain)`.
- **Done when:** "Add machine" moves ≈ 1/7 of bytes and emits `rebalance.completed` with both numbers.

---

## Seams you own or depend on

| Seam | Direction | With |
|---|---|---|
| I1 plan/commit/manifest | Jaiveer → you | ~H6.5 (use `soum_fake_meta.py` before) |
| I2 fragment API + pull | you → your gateway, Jaiveer's repair | by H5 |
| I3 register/heartbeat/inventory/reports | you → Jaiveer | by H6 |
| I4 BrainContext | Jaiveer hosts your reconciler/gc/rebalancer | H10 |
| I5 chaos | Anushka's supervisor → your `/_chaos/corrupt`, `/_chaos/disk_full` | by H9 |
| I7 client traffic | Urooz's Oracle → your gateway | by H9 |

## Gated items in your lane (ask Anushka first)
The `.blk` format is in the architecture: don't change it · any new field in models.py · any new dependency · headers not listed in docs/contracts/README.md.
