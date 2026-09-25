# Vault: Architecture

**Status:** v1.0, frozen for build · **Owner:** Anushka (changes need her approval, TEAM_PROTOCOL §4) · **Date:** 2026-09-26
**Read with:** `PRD.md` (what and why), `TECH_STACK.md` (versions, snippets), `DESIGN.md` (UI).

The Python payload models in `backend/vault/common/models.py` and their TypeScript mirror in `web/lib/contracts.ts` are the executable form of §7. If code and this document disagree, stop and ask Anushka.

---

## 0. Reading guide (save tokens: read only what your component needs)

| You are building | Read |
|---|---|
| Storage node | §1, §2, §3, §4.1, §4.2, §4.5–4.7, §4.9, §4.14–4.16, §7.3, §9, §11 |
| Metadata service | §1, §2, §3, §4 (all), §7.2, §8, §9, §11 |
| Health brain (detector, repair, auditor, rebalancer, GC) | §1, §2, §3, §4.5–4.14, §7.2, §8, §9, §12 |
| Gateway | §1, §2, §3.1, §4.1–4.4, §4.7, §7.1, §7.2 (uploads, objects), §11 |
| Supervisor & chaos | §1, §4.7, §4.14, §6, §7.3 (chaos), §7.4, §9 |
| Durability Oracle | §1, §4.1–4.4, §5, §7.1, §7.5, §8 |
| Dashboard | §1, §7 (read-only view), §8, then `DESIGN.md` |

---

## 1. System overview

```
                         ┌─────────────────────────────┐
   Browser (Next.js) ───▶│  Dashboard  :3000            │
     │  SSE + REST       └─────────────────────────────┘
     │
     ├──────────────▶ Metadata + Health brain  :7000   (source of truth, SQLite WAL, SSE)
     ├──────────────▶ Gateway                  :7080   (public object API)
     ├──────────────▶ Supervisor / chaos       :7070   (process control, fault injection)
     └──────────────▶ Oracle                   :7090   (workload + ledger + checks)

  Gateway ──plan/commit/manifest──▶ Metadata
  Gateway ──fragment PUT/GET──────▶ Nodes n1..n6  :7101..7106
  Nodes   ──heartbeat/inventory───▶ Metadata
  Metadata──pull/delete commands──▶ Nodes
  Nodes   ──fragment pull/relay───▶ Nodes
  Oracle  ──client traffic────────▶ Gateway      (Oracle is never subject to chaos)
  Supervisor starts/kills every process except itself and the dashboard.
```

| Participant ID | Process | Port | Runs |
|---|---|---|---|
| `meta` | `python -m vault.metadata` | 7000 | Metadata API, SQLite, SSE, **health brain** (detector, scheduler, repair, auditor, rebalancer, GC, reconciler, metrics) |
| `gw` | `python -m vault.gateway` | 7080 | Public object API, chunking, EC encode/decode, quorum writes, verified reads |
| `n1`…`n6` | `python -m vault.node --id nX` | 7101…7106 | Fragment storage, scrubber, heartbeats, pings, pull-repair, relay, netsim |
| `oracle` | `python -m vault.oracle` | 7090 | Workload generator, ledger, checker, run management |
| `sup` | `python -m vault up` | 7070 | Process supervisor, chaos controller, reset, seed |
| `web` | `npm run dev` / `start` in `web/` | 3000 | Landing + console |

All inter-process traffic is HTTP/1.1 on `127.0.0.1` through one shared client (`vault/common/rpc.py`) that applies network simulation (§4.7). Every process is a single-worker uvicorn app with asyncio background tasks (§11).

---

## 2. Key design decisions

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| D1 | **One metadata authority** (single process, SQLite WAL, `synchronous=FULL`) that serializes every commit and assigns a global `commit_seq` | Makes "which version is current" linearizable with no split brain. Crash-safe. Buildable in 20 h. Same trade as GFS/HDFS and what makes S3-style strong read-after-write consistency simple. | Leaderless Dynamo metadata (conflicts, vector clocks, slow listing); Raft (too risky to implement in 20 h) |
| D2 | **Immutable, versioned fragments.** A PUT never modifies stored bytes; it creates a new version with new fragment IDs. | Replicas can't drift halfway through an edit. Replica inconsistency reduces to *missing, damaged, orphaned or extra* fragments, which checksums and reconciliation catch. | In-place overwrite with read-repair by timestamp |
| D3 | **Unified fragment model.** A chunk has `n` fragments indexed `0..n-1`. Replication: every fragment is the full chunk. Erasure coding: fragments are the k data + m parity shards. | One code path for placement, repair, scrub, auditor and GC. EC is a variation in encode/decode only. | Separate replica and shard subsystems |
| D4 | **W-of-n write acknowledgement, 1 verified read.** Writes ack after W durable fragments. Reads need one checksum-verified copy (replication) or k verified fragments (EC). | With immutable data and a single commit authority, read quorums add latency without adding correctness. Checksums decide correctness. | R+W>n read quorums |
| D5 | **Consistent hash ring with virtual nodes, fate-aware.** Walk the ring from the chunk's hash, skip nodes that share a fate domain with already-chosen ones, relax constraints only if necessary. | ≈1/N data moves on membership change. Shared-fate avoidance is a filter on the same walk. Visual and recognizable. | Rendezvous/straw2 hashing (equally valid; documented as alternative) |
| D6 | **Phi accrual detector → peer confirmation → grace → dead.** Leases shorter than the grace period; epochs fence dead nodes. | Separates "slow", "unreachable" and "dead". Leases guarantee a cut-off node stops accepting writes before its data is rebuilt elsewhere. | Fixed timeouts |
| D7 | **Application-level network simulation + one-hop relay.** Partitions are enforced inside the shared RPC client and server middleware; relays forward through a node that can reach both sides. | Works on any laptop OS without root, iptables or Docker. Makes asymmetric and partial partitions scriptable. Relay implements the Nifty idea. | iptables/tc, Docker networks |
| D8 | **Pull-based repair.** Metadata tells the *target* node to fetch from verified sources, then verifies and commits. | Target verifies checksum before storing. Metadata stays the only writer of placement. Works through relays. | Push from source |
| D9 | **Naive mode = same binaries, safety switches off.** | Fair comparison for the Oracle. No second system to build. | Separate naive implementation |
| D10 | **Plain-language events emitted by the backend.** Every event carries `human` and `technical` strings. | One source of truth for the timeline, the Oracle and the pitch. | Frontend-only message mapping |

---

## 3. Data model

### 3.1 Identifiers

| Thing | Format | Example |
|---|---|---|
| Node | `n<int>` | `n3` |
| Version | `v_` + 20 hex (random) | `v_4f1c9a0e7b2d33a1c8e0` |
| Chunk | `<version_id>_<idx:05d>` | `v_4f1c…c8e0_00002` |
| Fragment (`fid`) | `<chunk_id>_f<frag_idx>` | `v_4f1c…c8e0_00002_f1` |
| ETag | SHA-256 hex of the whole object | `9b74c9…` |
| Hash for placement | `int.from_bytes(blake2b(s, digest_size=8).digest(), "big")` | never Python's `hash()` (randomized per process) |

### 3.2 Entities

- **Bucket:** name + durability policy.
- **Object:** `(bucket, key)` with a pointer to its current committed version (data or tombstone).
- **Version:** one upload or delete. `kind ∈ {data, tombstone}`, `state ∈ {pending, committed, aborted, superseded}`. `seq` (per key) and `commit_seq` (global) are assigned **at commit**, so both increase in commit order.
- **Chunk:** `idx`, `size` (unpadded), `sha256` of the unpadded chunk bytes, `frag_size`.
- **Fragment row:** one `(chunk_id, frag_idx, node_id)` with a state. A `(chunk_id, frag_idx)` normally lives on one node; two rows exist briefly during a move.

### 3.3 Policies

```yaml
rep2: { name: rep2, type: replication, n: 2, w: 2 }
rep3: { name: rep3, type: replication, n: 3, w: 2 }      # default
ec42: { name: ec42, type: erasure, k: 4, m: 2, n: 6, w: 5 }
```
- Replication: fragment payload = chunk bytes; `frag_sha256 = chunk_sha256`; any 1 verified fragment reconstructs the chunk.
- Erasure: pad the chunk with zeros to a multiple of k; `frag_size = padded_len / k`; fragments `0..k-1` are the data shards (systematic), `k..n-1` parity. Any k verified fragments reconstruct; truncate to `chunk.size`. Each fragment has its own `frag_sha256` recorded at commit.
- **Needed** to read: 1 (replication) or k (EC). **Target**: n distinct `frag_idx` held on non-dead nodes.
- A policy needs at least n eligible nodes to place fully and W to accept writes. Config validation rejects `w > n` or `w < needed`.

### 3.4 Enums

```
NodeState:  JOINING | ALIVE | SUSPECT | PARTITIONED | DOWN | DEAD | REJOINING | DRAINING | RETIRED
  (in memory; DEAD, DRAINING, RETIRED and epoch are also persisted)
FragState:  pending | ok | incoming | corrupt | missing | lost | trim
VersionState: pending | committed | aborted | superseded
JobKind:    repair | move | trim
JobReason:  under_replicated | corrupt | missing | fate | rebalance | drain | over_replicated | orphan | superseded | aborted
JobState:   queued | running | done | failed | cancelled
Severity:   info | success | warn | danger
```

**Derived fragment sets** (computed, never stored):
- **durable** = rows with `state=ok` on nodes not in `{DEAD, RETIRED}`. Repair triggers when distinct durable `frag_idx` < n.
- **available** = rows with `state=ok` on nodes in `{ALIVE, SUSPECT, PARTITIONED}` that are reachable (directly or via relay). A chunk is **readable now** when distinct available `frag_idx` ≥ needed.

### 3.5 SQLite schema (metadata, `data/meta/vault.db`)

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;          -- naive mode: OFF
PRAGMA foreign_keys=ON;

CREATE TABLE nodes (
  id TEXT PRIMARY KEY, addr TEXT NOT NULL, display_name TEXT NOT NULL,
  labels TEXT NOT NULL DEFAULT '{}',            -- JSON {"power":"A","switch":"S1","disk_batch":"D1","version":"1.0"}
  capacity_bytes INTEGER NOT NULL,
  persisted_state TEXT NOT NULL DEFAULT 'ALIVE',-- only DEAD / DRAINING / RETIRED / ALIVE are persisted
  epoch INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);
CREATE TABLE buckets (name TEXT PRIMARY KEY, policy TEXT NOT NULL, created_at REAL NOT NULL);  -- policy JSON
CREATE TABLE objects (
  bucket TEXT NOT NULL, key TEXT NOT NULL,
  current_version_id TEXT,                       -- data or tombstone version; NULL never after first commit
  last_seq INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket, key)
);
CREATE TABLE versions (
  version_id TEXT PRIMARY KEY, bucket TEXT NOT NULL, key TEXT NOT NULL,
  kind TEXT NOT NULL,                            -- data | tombstone
  state TEXT NOT NULL,                           -- VersionState
  seq INTEGER, commit_seq INTEGER,               -- set at commit
  size INTEGER NOT NULL DEFAULT 0, sha256 TEXT, policy TEXT NOT NULL, chunk_size INTEGER NOT NULL,
  created_at REAL NOT NULL, committed_at REAL, superseded_at REAL
);
CREATE INDEX versions_key   ON versions(bucket, key, commit_seq);
CREATE INDEX versions_state ON versions(state, created_at);
CREATE TABLE chunks (
  chunk_id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(version_id),
  idx INTEGER NOT NULL, size INTEGER NOT NULL, sha256 TEXT, frag_size INTEGER NOT NULL
);
CREATE INDEX chunks_version ON chunks(version_id, idx);
CREATE TABLE fragments (
  chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id), frag_idx INTEGER NOT NULL, node_id TEXT NOT NULL,
  state TEXT NOT NULL, sha256 TEXT, updated_at REAL NOT NULL,
  PRIMARY KEY (chunk_id, frag_idx, node_id)
);
CREATE INDEX fragments_node  ON fragments(node_id, state);
CREATE INDEX fragments_state ON fragments(state);
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, reason TEXT NOT NULL,
  chunk_id TEXT, frag_idx INTEGER, source_node TEXT, target_node TEXT,
  priority INTEGER NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  bytes INTEGER NOT NULL DEFAULT 0, error TEXT, incident_id INTEGER,
  created_at REAL NOT NULL, started_at REAL, finished_at REAL
);
CREATE INDEX jobs_queue ON jobs(state, priority, created_at);
CREATE TABLE incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,   -- node_dead | corruption | power_cut | fate | partition
  subject TEXT, fault_at REAL, detected_at REAL, repair_started_at REAL, recovered_at REAL,
  affected_chunks INTEGER NOT NULL DEFAULT 0, remaining_chunks INTEGER NOT NULL DEFAULT 0,
  bytes_repaired INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, type TEXT NOT NULL, severity TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '{}', human TEXT NOT NULL, technical TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);   -- commit_seq, config overrides, cluster_id
```

### 3.6 Fragment on-disk format (node, `data/<node>/`)

```
data/n3/
  blobs/<version_id[2:4]>/<fid>.blk            # durable fragment files (2-hex-char fan-out)
  tmp/<fid>.<rand>.part                        # in-flight writes (deleted on startup)
  quarantine/<fid>.blk                         # damaged fragments moved aside by scrub/read
  node.json                                    # {node_id, epoch, lease_expiry}  (rewritten atomically)
```
`.blk` layout (one file = one atomic unit):
```
bytes 0..3    magic  b"VLT1"
bytes 4..7    header_len (uint32 big-endian)
bytes 8..     header JSON (FragmentHeader, §7.3)
then          payload (frag_size bytes)
```
The header makes every fragment **self-describing** (bucket, key, version, chunk, frag_idx, expected SHA-256), which lets reconciliation, orphan cleanup and scrub work from the node's own disk.

---

## 4. Core flows

### 4.1 Write path (`PUT /{bucket}/{key}`)

```
Client          Gateway                          Metadata                     Nodes
  │ PUT (Content-Length L) │                          │                           │
  │───────────────────────▶│ POST /v1/uploads ────────▶│ validate bucket/policy    │
  │                        │                           │ create version(pending),  │
  │                        │                           │ chunks, fragments(pending)│
  │                        │◀──────── UploadPlan ──────│ placement per chunk (+2 spares)
  │                        │ for each 1 MiB chunk (≤2 in flight):                  │
  │                        │   sha256; EC: pad+encode                              │
  │                        │   PUT /v1/fragments/{fid} ×n  (direct or relay) ─────▶│ verify sha, tmp+fsync+rename+fsync dir
  │                        │   wait ≥W acks; on failure use a spare               ◀│ 201 {durable:true}
  │                        │   <W after spares → abort → 503 quorum_not_met        │
  │                        │ POST /v1/uploads/{v}/commit ─▶│ BEGIN IMMEDIATE           │
  │                        │                           │ check pending & not expired│
  │                        │                           │ each chunk ≥W ok fragments│
  │                        │                           │ assign seq, commit_seq    │
  │                        │                           │ publish pointer; old → superseded
  │                        │                           │ queue repair for chunks <n│
  │                        │◀────── CommitResult ──────│ COMMIT (fsync)            │
  │◀── 200 PutResult ──────│                           │                           │
```
Rules:
1. `Content-Length` is required (411 otherwise). `L = 0` is valid: zero chunks.
2. The plan lists n targets and up to 2 spares per chunk. Targets carry the node's current `epoch`; nodes reject a mismatched epoch (409).
3. The gateway computes the whole-object SHA-256 while streaming; it becomes the ETag.
4. The gateway reports every fragment attempt at commit (`ok` true/false, actual node). Metadata never trusts a count; it checks distinct `frag_idx` with `ok` on distinct, non-dead nodes.
5. Conditional writes (P1) are checked at begin (fast fail) **and** re-checked inside the commit transaction.

### 4.2 Read path (`GET /{bucket}/{key}`)

1. Gateway → `GET /v1/objects/{bucket}/{key}` → Manifest of the current committed version (404 if none or tombstone).
2. For each chunk in order (prefetch 1 ahead):
   - **Replication:** order holders: available → direct route before relay → not slow → lowest RTT. `GET /v1/fragments/{fid}` from the first; verify SHA-256 against `chunk.sha256`. On timeout, 404, 409 corrupt or checksum mismatch: report (`POST /v1/reports/fragment`) and try the next holder. **Hedged read (P1):** if no response in 150 ms, ask the next holder in parallel; use the first verified answer.
   - **EC:** fetch data fragments `0..k-1`; if all verify, concatenate (no decode). Otherwise fetch parity fragments until k verified, decode with zfec, truncate to `chunk.size`, verify `chunk.sha256`.
3. Headers are sent only after the first chunk is verified, so an unreadable object returns a clean `503 {"error":"unavailable"}`. A failure after streaming has started aborts the connection (the client sees a truncated response, and the Oracle records it as an error).
4. Response headers: `ETag`, `X-Vault-Seq`, `X-Vault-Commit-Seq`, `X-Vault-Read-Path` (`direct`, `failover:<n>`, `relay:<node>`, `ec-decode`).
5. Every report from a read enqueues a priority repair (**read repair**).

### 4.3 Delete

`DELETE /{bucket}/{key}` → metadata commits a **tombstone** version (new `seq`, `commit_seq`) and points the object at it; the previous data version becomes `superseded` and is garbage-collected after `gc.superseded_grace_s`. Deleting a key that doesn't exist returns `200 {"deleted": false}` and writes nothing.

### 4.4 Crash points (what happens if the power goes at each step)

| Crash while… | State left behind | Outcome |
|---|---|---|
| Node writing a fragment (before rename) | `tmp/*.part` | Deleted on startup (`node.startup_discarded` event). Gateway didn't get an ack; spare or failure. |
| After node ack, before gateway commit | Durable fragments, version `pending` | Upload expires after `gc.upload_timeout_s` → `aborted`; fragments trimmed. Client saw an error or timeout (Oracle: *unknown*). |
| Metadata mid-commit | SQLite transaction not committed | Rolled back automatically. Same as above. |
| After metadata commit, before gateway replies | Committed version | Data readable. Client saw a timeout (Oracle: *unknown*, accepts either outcome). |
| Gateway anywhere | Nothing in gateway is durable | Same as the rows above. |
| Whole cluster (power cut) | Any of the above | Startup recovery (§4.14). No acknowledged write is lost because every ack happened after fsync + commit. |

### 4.5 Node lifecycle and failure detection

**Heartbeat:** every node → `POST /v1/nodes/{id}/heartbeat` every **500 ms** (via relay if the direct link fails). Payload includes epoch, disk usage, fragment count, its reachability row and scrub status.

**Phi accrual** (per node, in metadata): keep the last 100 inter-arrival times; `mean`, `std = max(std, 200 ms)`;
```
y = (t_since_last - mean) / std
e = exp(-y * (1.5976 + 0.070566 * y*y))
phi = -log10(e / (1 + e))        if t_since_last > mean
phi = -log10(1 - 1 / (1 + e))    otherwise
```
Bootstrap with mean 500 ms and std 125 ms. Detector tick: every 100 ms.

```
JOINING ──first heartbeat──▶ ALIVE ◀────────── heartbeat (3 in 2 s from DOWN) ─────────┐
ALIVE ──φ ≥ 8──▶ SUSPECT ──any participant reached it in last 2 s──▶ PARTITIONED        │
                   │  └── heartbeat arrives ──▶ ALIVE                                    │
                   └──no evidence for confirm_ms (1 s)──▶ DOWN ──────────────────────────┤
PARTITIONED ──heartbeats resume (direct or relayed)──▶ ALIVE                              │
PARTITIONED ──nobody reaches it any more──▶ DOWN                                          │
DOWN ──dead_after_s (15 s; demo 8 s) without heartbeat──▶ DEAD   (epoch++, fragments → lost, incident, repair)
DEAD ──node heartbeats──▶ reply {state:"dead"} ──▶ node re-registers ──▶ REJOINING
REJOINING ──inventory reconciled (§4.10)──▶ ALIVE
ALIVE ──decommission──▶ DRAINING ──all fragments moved──▶ RETIRED
```
- **Slow** is a flag, not a state: p50 RTT > 300 ms or ≥ 20% ping loss. Slow nodes are read last and never chosen as repair sources when others exist.
- **Startup grace** (`detector.startup_grace_s`, 10 s): after metadata starts, nodes begin as `JOINING` and none can become `DOWN`/`DEAD` until the grace ends. Prevents a repair storm after a full power restore.
- A `DOWN → ALIVE` return before `dead_after_s` emits `node.recovered_in_grace` ("avoided an unnecessary rebuild") and counts in metrics.

### 4.6 Epochs, leases and fencing

- Every heartbeat reply grants a **lease**: `lease_ttl_ms` (5 s). The node persists `lease_expiry` in `node.json`.
- A node whose lease has expired is **fenced**: it refuses `PUT` and `DELETE` (`503 fenced`) but still serves verified reads (fragments are immutable and checksummed, so reads are safe).
- Config validation enforces `lease_ttl_ms < dead_after_s × 1000`. So a node cut off from metadata always stops accepting writes **before** metadata declares it dead and rebuilds its data elsewhere.
- On `DEAD`, metadata increments the node's `epoch`. Every write or delete to a node carries `X-Vault-Epoch`; the node rejects a mismatch with `409 stale_epoch`. A returning node learns it is dead from the heartbeat reply, re-registers, and receives the new epoch.

### 4.7 Connectivity, network simulation and relay

**Connectivity matrix.** Every participant pings every node (and nodes ping `meta`) every **1 s** with `GET /v1/ping` (timeout 500 ms). Rows are reported to metadata: nodes in heartbeats, gateway in `POST /v1/participants/gw/report`, metadata measures its own. An entry `{ok, rtt_ms, at}` older than 3 s is `unknown`. The matrix is part of the cluster snapshot and cached by every RPC client (refresh 1 s).

**Network simulation (`vault/common/netsim.py`)**, present in every Python process:
```
NetSim state: blocked_out: set[pid], blocked_in: set[pid], delay_ms: int, frozen_until: float
Client side (rpc.py):  if target ∈ blocked_out → raise NetworkError immediately
Server middleware:     if X-Vault-From ∈ blocked_in → respond 599 + X-Vault-Netsim: blocked
                       sleep(delay_ms); if now < frozen_until → sleep until then (requests hang → client timeouts)
rpc.py converts 599 and connect/read timeouts into NetworkError (same thing, as far as callers know)
```
Every request carries `X-Vault-From: <pid>` and `X-Vault-Hop: 0|1`. The dashboard's browser traffic is never blocked.

**Routing (`rpc.request(target, ...)`):**
1. If matrix `self→target` is ok or unknown: try **direct**.
2. On `NetworkError` (or if the matrix already says the link is down): pick a relay `r` (a node ≠ self, target) with `self→r` ok and `r→target` ok, lowest summed RTT; send `METHOD /v1/relay/{target}/{path}` to `r` with `X-Vault-Hop: 1`. Mark `self→target` bad for 3 s.
3. The relay endpoint accepts only `X-Vault-Hop: 1`, applies its own netsim rules for `r→target`, forwards directly (never relays again), and streams the response back.
4. No relay available → `NetworkError`.
5. When `safety.relay` is off (Naive), step 2 is skipped.

Metadata records how each heartbeat arrived (`X-Vault-Via`) and shows the node as `route: relay:<r>`. A node cut from metadata but reachable through a peer stays **ALIVE** with a relay badge, and **no repair is triggered**. That is the partial-partition demo.

### 4.8 Repair scheduler (brain)

**Scan** every 500 ms (SQL over committed, current data versions):
- chunk has distinct durable `frag_idx` < n → one `repair` job per missing `frag_idx`
- fragment rows `corrupt` or `missing` → `repair` for that `frag_idx`

**Priority** (lower runs first):

| P | Condition |
|---|---|
| 0 | One failure from loss: durable = 1 (replication) or durable = k (EC) |
| 1 | Other under-replicated, corrupt or missing |
| 2 | Shared fate: chunk effective copies = 1 while n ≥ 2 |
| 3 | Shared fate improvement (effective copies < target), rebalance, drain |
| 4 | Trim over-replication, garbage collection |

**Dispatch:** at most `repair.max_concurrent` (8) running jobs, at most `repair.per_node` (2) per source or target node, and a global token bucket of `repair.bandwidth_mbps` (50 MB/s): a job of `b` bytes waits for `b` tokens. Retries: 3 attempts with backoff (1 s, 3 s, 9 s), then `repair.failed` event.

**Execution** of `repair (chunk, frag_idx)`:
1. **Target** = `placement.choose_additional(chunk_id, holders=distinct durable nodes, exclude=not-ALIVE ∪ fenced ∪ full)` (§4.11). None → job waits (`repair.blocked` event with the reason, e.g. "only 2 machines are on").
2. Insert fragment row `(chunk, frag_idx, target, incoming)`.
3. `POST target /v1/fragments/{fid}/pull` with sources (available holders, verified-first, non-slow) and the expected SHA-256:
   - replication: `mode:"copy"` → fetch any source, verify, store.
   - EC: `mode:"ec_rebuild"` → fetch k other fragments, decode, re-encode only `frag_idx`, verify, store.
4. Success → row `ok`, job `done`, incident progress updated. A source that returned bad bytes is reported as `corrupt`.
5. Failure → row deleted, job retried.

**Incidents:** a node reaching `DEAD`, a corruption burst, a power cut or a fate violation opens an incident with `fault_at` (from the supervisor's ground-truth report if present, else the last heartbeat or detection time), `detected_at`, `repair_started_at`, and `recovered_at` when `remaining_chunks = 0`. MTTR = `recovered_at − fault_at`.

### 4.9 Integrity verification

1. **On receive:** node recomputes SHA-256 of the payload before writing; mismatch → `400 checksum_mismatch`.
2. **On read:** node verifies before serving; mismatch → moves the file to `quarantine/`, returns `409 {"error":"corrupt"}`, and reports it. The gateway verifies again (end to end).
3. **Scrubber:** each node walks all `.blk` files continuously at `scrub.rate_mbps` (20 MB/s), recently written files first after a restart. Mismatch → quarantine + `POST /v1/reports/fragment {problem:"corrupt", context:"scrub"}`. `POST /v1/scrub` on a node (or `/v1/scrub` on metadata for all) runs a full pass now.
4. Metadata marks the row `corrupt`, emits `fragment.corrupt`, and queues a P1 (or P0) repair. Repair never copies from a fragment that failed verification.

### 4.10 Reconciliation and garbage collection

**Inventory** (P0 full list): each node pushes `POST /v1/nodes/{id}/inventory` on register, on rejoin and every 30 s (staggered). Metadata compares it with the node's fragment rows:

| Case | Action |
|---|---|
| Row `ok`, file absent | Row → `missing`, repair queued |
| File present, SHA ≠ expected | Row → `corrupt`, node told to quarantine, repair queued |
| Row `lost` (node was dead), file present and verified | Row → `ok` (copy is back). May cause over-replication → trim |
| File present, no row, version committed and current, SHA matches | **Adopt** as `ok` (e.g. a write the gateway thought failed). Over-replication → trim |
| File present, version aborted/superseded or unknown | **Orphan**: delete after `gc.orphan_grace_s` (by file mtime) |

P1: Merkle-lite digests. The node sends 256 bucket hashes (bucket = first byte of `blake2b(fid)`); metadata compares against expected digests and requests full lists only for mismatched buckets.

**GC loop** (every 5 s): pending versions older than `gc.upload_timeout_s` → `aborted`; fragments of aborted versions and of superseded versions older than `gc.superseded_grace_s` → `trim` jobs (P4). Trim = `DELETE /v1/fragments/{fid}` with the node's epoch, then delete the row. **Over-replication:** when a `frag_idx` has more than one `ok` copy, keep the copy that maximizes effective copies (ties: the ring-preferred node), trim the rest.

### 4.11 Placement

**Ring:** `vnodes_per_node` (128) points per node at `hash(f"{node_id}#{i}")`, scaled by capacity weight. `ring.walk(hash(chunk_id))` yields distinct nodes clockwise.

```python
def place(chunk_id, count, holders=(), exclude=set(), fate_keys=FATE_KEYS):
    """Return up to `count` nodes total (including existing holders)."""
    chosen = list(holders)
    eligible = [n for n in ring.walk(h(chunk_id))
                if n.state == ALIVE and not n.fenced and not n.full and n.id not in exclude]
    # Relax constraints from least to most important; fate_keys ordered most→least important.
    for keys in [fate_keys[:i] for i in range(len(fate_keys), -1, -1)]:
        for n in eligible:
            if len(chosen) == count: break
            if n in chosen: continue
            if not any(shares(n, c, keys) for c in chosen):   # same value for any key, or same node
                chosen.append(n)
        if len(chosen) == count: break
    return chosen
```
- `FATE_KEYS = ["power", "switch", "disk_batch", "version"]` minus **cluster-wide** domains (a label value held by every non-retired node).
- `choose_additional(chunk_id, holders, exclude)` = `place(chunk_id, len(holders)+1, holders, exclude)[-1]`.
- With `safety.fate_aware_placement` off (Naive), `fate_keys = []`: pure ring order.

### 4.12 Shared-Fate Auditor

**Domains:** one per machine (`("node", id)`) plus one per label value for each fate key (`("power","A")`, …). A domain that contains every non-retired node is **cluster-wide**: reported separately (e.g. "all machines run version 1.0"), excluded from scoring and placement.

**Effective copies (IFL)** of a chunk with durable holders `H` (node → set of `frag_idx`):
```
lost(S) = distinct frag_idx remaining after removing every node covered by domain set S  < needed
IFL = min |S| such that lost(S)        # the S found is the file's `min_cut`, shown in the Inspect drawer
```
Compute by iterative deepening over combinations of the domains that touch `H` (at most ~25 domains, depth ≤ n−needed+1 ≤ 3). Cache by `frozenset(H)`: most chunks share a handful of holder sets. Per file: minimum over its chunks. Target: `n` for replication, `m+1` for EC.

**Audit** runs on label change, membership change, and every 5 s.

| Level | Condition | UI | Action |
|---|---|---|---|
| Safe | IFL ≥ target | green | none |
| Limited | 2 ≤ IFL < target | amber | P3 improvement move **only** if no node would exceed 85% full and imbalance stays < 20%; otherwise explain the limit |
| At risk | IFL = 1 and n ≥ 2 | red | P2 move |

**Fix** (greedy, one move at a time): for each holder `a` in the most-shared domain and each eligible non-holder `b`, evaluate IFL of `H − a + b`; take the best (tie: least full `b`). Enqueue `move(chunk, frag_idx, a → b)`, which is make-before-break: pull to `b`, verify, mark `ok`, then trim `a`. A move never lowers IFL.

**Advice:** when no single move can reach the target, emit `fate.limited` with the reason, e.g. "Power Strip A feeds 4 of 6 machines. Give Lab Laptop or Records Room its own power supply to get 3 independent copies."

**Default labels** (Latin square: two fully independent triples exist):

| Node | Display name | power | switch | disk_batch | version |
|---|---|---|---|---|---|
| n1 | Reception PC | A | S1 | D1 | 1.0 |
| n2 | Doctor's Desk | A | S2 | D2 | 1.0 |
| n3 | Lab Laptop | B | S2 | D3 | 1.0 |
| n4 | Pharmacy PC | B | S3 | D1 | 1.0 |
| n5 | Records Room | C | S3 | D2 | 1.0 |
| n6 | Admin Office | C | S1 | D3 | 1.0 |

`{n1,n3,n5}` and `{n2,n4,n6}` are fully independent (IFL 3 for `rep3`). `version` is cluster-wide. **Demo relabel:** set `power=A` on n3 and n5 → files on `{n1,n3,n5}` drop to IFL 1 → one move each raises them to 2 → advice explains why 3 needs another power supply → cutting Power Strip A leaves every file readable.

### 4.13 Rebalancer (placement reconciler)

Same move primitive as the auditor, different triggers:
- **Node added** (new `ALIVE` node with no fragments): for each current chunk, if `place(chunk_id, n)` includes the new node and the chunk has no fragment there, move one fragment from a holder that is *not* in the desired set to the new node, provided IFL doesn't drop. Expect ≈ 1/N of bytes to move. Report `rebalance.completed` with `moved_bytes / total_bytes` next to the ideal `1/N`.
- **Drain** (`DRAINING`): every fragment on the node moves to `choose_additional(...)`, then the node becomes `RETIRED`.
- **Imbalance** (P1): if max/min used ratio > 1.2, move from the fullest to the least full node where the target is in the chunk's desired set.

Rule for every mover: **never move a fragment unless it fixes redundancy, raises IFL, drains a node, or fixes imbalance; and never lower IFL.** Moves run at P3, below repair.

### 4.14 Startup and power-cut recovery

**Node startup:**
1. Delete `tmp/*` (count → `node.startup_discarded`).
2. Scan `blobs/`, read headers, build the in-memory index `fid → (path, header)`.
3. Read `node.json` (epoch). Register with metadata (`POST /v1/nodes/register`), push inventory.
4. Start scrubber with files modified in the last 5 minutes first.

**Metadata startup:**
1. Open SQLite (WAL recovery is automatic). Count committed objects → `meta.recovered` event with timing.
2. Load nodes; all start as `JOINING` for `startup_grace_s`.
3. Expire stale pending uploads. Resume queued jobs.

**Gateway/oracle startup:** stateless (P1 gateway cache is rebuilt lazily).

The dashboard shows these events on the boot screen (DESIGN §5.7).

### 4.15 Safety switches (Vault vs Naive)

| Switch (`safety.*`) | Vault | Naive | Effect when off |
|---|---|---|---|
| `verify_on_receive` | on | off | Node stores whatever arrives |
| `verify_on_read` | on | off | Node and gateway return bytes without checking |
| `durable_writes` | on | off | No fsync, no temp+rename (direct write) |
| `quorum_writes` | on | off | Ack after 1 fragment (W = 1) |
| `read_failover` | on | off | Read only the first holder; any error fails the read |
| `repair` | on | off | No repair jobs are dispatched |
| `scrub` | on | off | Scrubber paused |
| `fate_aware_placement` | on | off | Pure ring placement, auditor reports but never moves |
| `fencing` | on | off | No lease expiry, no epoch checks |
| `relay` | on | off | No relay routing |

`POST /v1/mode {"mode":"vault"|"naive"}` sets all switches; `PATCH /v1/config` sets individual ones (Oracle ablation, P1). Config changes bump `config_version`; nodes see it in the heartbeat reply and re-fetch `GET /v1/config`; the gateway polls every 1 s. Applies within ~1 s.

### 4.16 Simulated page cache (P1)

Storage writes go through `DiskIO`:
- **RealDisk** (default): write temp file → `fsync(file)` → `os.replace` → `fsync(dir)` (skipped on Windows).
- **SimCacheDisk** (`storage.sim_page_cache: true`, used in Oracle runs): unsynced writes are held **in process memory**; `fsync()` writes them to disk. A background flusher writes dirty data after `dirty_expire_ms` (5 s), and may write a file's first half, then its second half 200 ms later. So a process kill behaves like power loss: in Vault mode (fsync before ack) nothing acknowledged is lost; in Naive mode the last few seconds of acknowledged writes vanish and some files are torn, which the Oracle counts.

---

## 5. Durability Oracle

**Process:** `vault.oracle` on :7090. Talks only to the gateway (client traffic), metadata (`/v1/inspect/objects`, `/v1/mode`) and the supervisor (reset, chaos script). Never killed by chaos.

**Run lifecycle:** `preparing` (supervisor reset, set mode, seed buckets) → `running` (workload + chaos script, 60 s) → `settling` (chaos cleared, crashed nodes stay down, wait until repair queue is empty or 60 s) → `verifying` → `done`.

**Workload:** `clients` (8) concurrent async loops, seeded RNG, keyspace of 200 keys in bucket `oracle` (`rep3`). Mix: 50% PUT (1 KB–2 MB random bytes), 40% GET, 10% DELETE. Every op completion is appended to `oracle_runs/<run_id>/ledger.jsonl` (flush + fsync every 100 ms):
```json
{"op":"put","key":"k042","invoke":1727330000.120,"complete":1727330000.310,
 "outcome":"ok|fail|unknown","sha256":"…","commit_seq":1234,"seq":7,"http":200}
```
`unknown` = timeout, connection reset or 5xx after the request body was fully sent. `fail` = definite rejection before any effect (4xx other than 404/412, or connection refused before sending).

**Checks** (per key; `acked` = outcome ok):

| Violation | Rule |
|---|---|
| `stale_read` | A GET returned `commit_seq c`, and some acked write on the key completed before the GET was invoked with `commit_seq > c` |
| `phantom_read` | A GET returned bytes whose SHA-256 matches no PUT attempted on that key before the GET completed |
| `lost` | Final GET returns 404 or unavailable, but the acceptable final states don't include "absent" |
| `damaged` | Final GET returns bytes matching no acceptable final value |
| `resurrected` | Final GET returns data, but the acceptable final states are only "absent" |
| `under_protected` (health, separate) | `/v1/inspect/objects` shows a live object below its target after settling |

**Acceptable final states** for a key = {the result of the acked write with the highest `commit_seq`} ∪ {the result of every `unknown` write invoked after that acked write's invocation}. A DELETE's result is "absent".

**Chaos script `standard`** (seeded; same for Vault and Naive; times in seconds from run start):
```
t=5   corrupt 20 random fragments
t=10  crash n2 (stays down)
t=15  cut link gw↔n4 (both directions)
t=20  slow n5 by 800 ms
t=25  power cut label power=B for 6 s (n3, n4), then restore
t=35  corrupt 20 random fragments
t=40  crash n6 (stays down)
t=45  cut link meta↔n1
t=55  clear links and slowness
```
**Chaos script `heavy`** (for the recorded pitch runs): three 60 s cycles of `standard` with 80 corruptions per burst; machines crashed in a cycle are restarted at its end (they rejoin). ≈ 500 fault units, ~4 minutes including settling.

**Counting faults:** `faults_injected` counts fault *units*: each corrupted fragment, each killed process, each cut link direction, each slowed machine, and each machine in a power cut. `standard` ≈ 47 units.

**API:** §7.5. **UI:** DESIGN §5.6.

---

## 6. Supervisor and chaos controller

`python -m vault up` starts the supervisor (:7070), which starts metadata, gateway, n1–n6 and the oracle as child processes (`asyncio.create_subprocess_exec`, logs in `logs/<pid>.log`), and restarts nothing automatically (crashes are demo events). Process kill uses `proc.kill()` (SIGKILL on POSIX, TerminateProcess on Windows).

Every chaos action:
1. is applied (process control, or `/_chaos/*` on the affected participants),
2. is reported to metadata as ground truth: `POST /v1/incidents/fault {kind, subject, at}` and an event `chaos.*` (the timeline shows "You cut the cable between Doctor's Desk and Lab Laptop"),
3. is listed in `GET /chaos` until cleared.

| Action | Implementation |
|---|---|
| Kill / start / restart machine | Process control |
| Power cut (all) | Kill metadata, gateway and all nodes at once; optional auto-restore after N s |
| Power cut (label, e.g. `power=A`) | Kill every node whose label matches (labels from `GET /v1/cluster`) |
| Cut link A–B (both / one way) | `A /_chaos/block {peers:[B], direction}` and the mirror on B |
| Slow / freeze machine | `/_chaos/slow {ms}`, `/_chaos/freeze {seconds}` |
| Corrupt N copies | `node /_chaos/corrupt {count, mode:"bitflip"}` on random nodes (flips bytes in payloads, header intact) |
| Disk full | `node /_chaos/disk_full {on}` → PUT returns 507 |
| Relabel | `PATCH meta /v1/nodes/{id}/labels` |
| Add machine | Spawn `n7` (next free port) with given labels and display name |
| Reset | Kill all, wipe `data/`, start all, create default buckets (`clinic` rep3, `archive` ec42, `scratch` rep2, `oracle` rep3), optionally seed |
| Seed | Upload ~200 synthetic files (names like `xray-0042.png`, `lab-report-0113.pdf`, 20 KB–3 MB random content) through the gateway |

---

## 7. API contract v1

Conventions: JSON bodies unless marked *bytes*. Errors are `{"error": "<code>", "message": "<human>", "detail": {...}}`. All services send `Access-Control-Allow-Origin: http://localhost:3000`. Times are Unix seconds (float). Sizes are bytes.

### 7.1 Gateway (:7080), public

| Method & path | Request | Response |
|---|---|---|
| `PUT /{bucket}` | `{"policy":"rep3"}` | `200 {"bucket","policy"}` · `409 bucket_exists` |
| `GET /{bucket}?prefix=&limit=1000` | | `200 {"bucket","objects":[{"key","size","etag","seq","commit_seq","policy","updated_at"}]}` |
| `PUT /{bucket}/{key:path}` | *bytes*, `Content-Length` required; P1: `If-Match`, `If-None-Match: *` | `200 PutResult` · `411` · `412 precondition_failed` · `503 quorum_not_met` / `metadata_unavailable` / `not_enough_machines` |
| `GET /{bucket}/{key:path}` | | `200` *bytes* + headers (§4.2) · `404 not_found` · `503 unavailable` |
| `HEAD /{bucket}/{key:path}` | | `200` headers · `404` |
| `DELETE /{bucket}/{key:path}` | | `200 {"deleted":bool,"commit_seq":int\|null}` |
| `GET /_vault/health` | | `200 {"pid":"gw","ok":true,"config_version"}` |

```json
PutResult = {"bucket":"clinic","key":"xray-0042.png","version_id":"v_…","seq":3,"commit_seq":1207,
             "etag":"9b74…","size":482113,"policy":"rep3",
             "stored":{"chunks":1,"fragments_acked":3,"w":2,"relayed":0}}
```

### 7.2 Metadata (:7000)

**Cluster and stream**

| Method & path | Request | Response |
|---|---|---|
| `GET /v1/cluster` | | `Snapshot` (§8.2) |
| `GET /v1/stream` | | SSE: `event: snapshot` every 500 ms; `event: event` per new Event; on connect, last 100 events |
| `GET /v1/events?after_id=&limit=200` | | `{"events":[Event]}` |
| `POST /v1/events` | `{type:"chaos.*"\|"oracle.*", severity, subject, human, technical, data}` | `201` (external emitters: supervisor, oracle only) |
| `GET /v1/config` · `PATCH /v1/config` | partial config (§9) | full config + `config_version` |
| `POST /v1/mode` | `{"mode":"vault"\|"naive"}` | `{"mode","config_version"}` |

**Nodes and participants**

| Method & path | Request | Response |
|---|---|---|
| `POST /v1/nodes/register` | `{"node_id","addr","capacity_bytes","display_name","labels"}` | `{"epoch","state","lease_ttl_ms","config_version"}` |
| `POST /v1/nodes/{id}/heartbeat` | `Heartbeat` | `HeartbeatReply` |
| `POST /v1/nodes/{id}/inventory` | `Inventory` | `{"missing":int,"adopted":int,"orphans":int,"corrupt":int}` |
| `PATCH /v1/nodes/{id}/labels` | `{"labels":{…},"display_name"?}` | `NodeView` (triggers re-audit) |
| `POST /v1/nodes/{id}/decommission` | | `NodeView` (→ DRAINING) |
| `POST /v1/participants/{pid}/report` | `{"reach":{pid:Reach},"stats"?:GatewayStats}` | `204` |
| `POST /v1/incidents/fault` | `{"kind","subject","at"}` | `204` (supervisor ground truth) |

```json
Heartbeat = {"node_id":"n3","epoch":4,"disk_used":81234567,"capacity":2147483648,"fragments":812,
             "fenced":false,"reach":{"n1":{"ok":true,"rtt_ms":1.2},"meta":{"ok":false,"rtt_ms":null}},
             "scrub":{"last_pass_at":1727330000.0,"scanned":812,"corrupt_found":0,"running":true}}
HeartbeatReply = {"state":"ALIVE","epoch":4,"lease_ttl_ms":5000,"config_version":12}
Inventory = {"node_id":"n3","epoch":4,"fragments":[{"fid":"v_…_00000_f1","sha256":"…","size":1048576,"mtime":1727330000.1}]}
GatewayStats = {"window_s":1,"ok":57,"failed":1,"by_op":{"put":{"ok":20,"failed":1},"get":{"ok":37,"failed":0}}}
```

**Buckets, uploads, objects** (uploads are gateway-internal)

| Method & path | Request | Response |
|---|---|---|
| `POST /v1/buckets` · `GET /v1/buckets` | `{"name","policy"}` | `{"name","policy"}` · `{"buckets":[…]}` |
| `POST /v1/uploads` | `{"bucket","key","size","if_match"?,"if_none_match"?}` | `UploadPlan` · `404 no_bucket` · `412` · `503 not_enough_machines` |
| `POST /v1/uploads/{version_id}/commit` | `CommitRequest` | `CommitResult` · `409 quorum_not_met` · `410 upload_expired` · `412` |
| `POST /v1/uploads/{version_id}/abort` | | `204` |
| `GET /v1/objects/{bucket}/{key:path}` | | `Manifest` · `404` |
| `GET /v1/objects/{bucket}?prefix=&limit=` | | list (same shape as gateway list) |
| `DELETE /v1/objects/{bucket}/{key:path}` | | `{"deleted","commit_seq"}` |
| `GET /v1/objects/{bucket}/{key:path}/health` | | `{"ifl","target","chunks":[{"idx","needed","durable","available","fragments":[FragLoc]}],"shared":[{"key","value"}],"min_cut":[{"key","value"}]}` (`min_cut` = one smallest set of domains whose failure loses the file; `key:"node"` for a single machine) |

```json
UploadPlan = {"version_id":"v_…","chunk_size":1048576,
  "policy":{"name":"rep3","type":"replication","n":3,"w":2},
  "chunks":[{"chunk_id":"v_…_00000","idx":0,"size":1048576,
    "targets":[{"frag_idx":0,"node_id":"n1","addr":"127.0.0.1:7101","epoch":1}, …],
    "spares":[{"node_id":"n2","addr":"127.0.0.1:7102","epoch":1}]}]}
CommitRequest = {"object_sha256":"…","size":482113,
  "chunks":[{"chunk_id":"v_…_00000","sha256":"…","frag_size":482113,
    "fragments":[{"frag_idx":0,"node_id":"n1","sha256":"…","ok":true}, …]}]}
CommitResult = {"version_id":"v_…","seq":3,"commit_seq":1207,"etag":"…","under_replicated_chunks":0}
Manifest = {"bucket","key","version_id","seq","commit_seq","size","sha256","policy","chunk_size",
  "chunks":[{"chunk_id","idx","size","sha256","frag_size",
    "fragments":[{"frag_idx","node_id","addr","state","sha256","route":"direct|relay:n2","slow":false}]}]}
```

**Health brain and inspection**

| Method & path | Response |
|---|---|
| `POST /v1/reports/fragment` `{"fid","node_id","problem":"corrupt\|missing\|unreachable\|slow","observed_by","context":"read\|scrub\|repair"}` | `202` |
| `GET /v1/repair` | `{"queued":{"p0":0,…},"active":[Job],"recent":[Job]}` |
| `GET /v1/fate` | `FateReport` (§8.3) |
| `GET /v1/metrics` | `Metrics` (§8.3) |
| `GET /v1/incidents?limit=20` | `{"incidents":[Incident]}` |
| `POST /v1/scrub` `{"nodes":["n1"]\|null}` | `202` |
| `POST /v1/rebalance` | `202` |
| `GET /v1/inspect/objects?cursor=&limit=500` | `{"objects":[{"bucket","key","state":"live\|deleted","seq","commit_seq","sha256","size","durable_min","target","ifl"}],"next_cursor"}` |

### 7.3 Storage node (:7101+)

| Method & path | Request | Response |
|---|---|---|
| `PUT /v1/fragments/{fid}` | *bytes*; headers `X-Vault-Meta` (base64url JSON `FragmentHeader`), `X-Vault-Sha256`, `X-Vault-Epoch` | `201 {"fid","sha256","size","durable"}` · `400 checksum_mismatch` · `409 stale_epoch` · `503 fenced` · `507 disk_full` |
| `GET /v1/fragments/{fid}` | | `200` *bytes* + `X-Vault-Sha256`, `X-Vault-Meta` · `404` · `409 corrupt` |
| `HEAD /v1/fragments/{fid}` | | `200` · `404` |
| `DELETE /v1/fragments/{fid}` | header `X-Vault-Epoch` | `204` · `409 stale_epoch` · `503 fenced` |
| `POST /v1/fragments/{fid}/pull` | `PullRequest` | `201 {"fid","sha256","size","ms","source_used"}` · `424 no_valid_source` |
| `GET /v1/ping` | | `{"pid","epoch","ts"}` |
| `GET /v1/health` | | `{"pid","epoch","fenced","lease_expiry","disk_used","capacity","fragments","scrub":{…}}` |
| `POST /v1/scrub` · `GET /v1/scrub` | | `202` · scrub status |
| `ANY /v1/relay/{target}/{path:path}` | forwarded as-is, requires `X-Vault-Hop: 1` | the target's response, streamed |

```json
FragmentHeader = {"fid","chunk_id","frag_idx","version_id","bucket","key","policy":"rep3",
                  "chunk_sha256","frag_sha256","chunk_size","frag_size","epoch"}
PullRequest = {"fid","header":FragmentHeader,"expected_sha256":"…","mode":"copy|ec_rebuild",
  "sources":[{"node_id":"n2","addr":"127.0.0.1:7102","fid":"v_…_00000_f0"}],
  "ec":{"k":4,"n":6,"frag_idx":5} }          // ec only for ec_rebuild; sources then lists k+ other fragments
```

**Chaos endpoints** (all Python participants; enabled when `chaos.enabled: true`):

| Method & path | Body |
|---|---|
| `GET /_chaos` | → current NetSim + node faults |
| `POST /_chaos/block` · `POST /_chaos/unblock` | `{"peers":["n3"],"direction":"in\|out\|both"}` · `{"peers":[…]\|null}` |
| `POST /_chaos/slow` | `{"ms":800}` (0 clears) |
| `POST /_chaos/freeze` | `{"seconds":10}` |
| `POST /_chaos/corrupt` (nodes) | `{"count":10}` or `{"fids":[…]}`, `"mode":"bitflip\|zero\|truncate\|delete"` → `{"corrupted":[fid]}` |
| `POST /_chaos/disk_full` (nodes) | `{"on":true}` |
| `POST /_chaos/clear` | clears everything above |

### 7.4 Supervisor (:7070)

| Method & path | Request | Response |
|---|---|---|
| `GET /procs` | | `{"procs":[{"pid":"n3","port":7103,"state":"running\|stopped","os_pid","uptime_s","display_name"}]}` |
| `POST /procs/{pid}/kill` · `/start` · `/restart` | | proc |
| `POST /power/cut` | `{"scope":"all"\|"label","label":"power=A","restore_after_s":8}` | `{"killed":[pid]}` |
| `POST /power/restore` | `{"scope":"all"\|"label","label"}` | `{"started":[pid]}` |
| `POST /chaos/link` | `{"a":"meta","b":"n5","cut":true,"direction":"both\|a_to_b"}` | active faults |
| `POST /chaos/node/{pid}` | `{"action":"slow\|freeze\|corrupt\|disk_full\|clear","params":{…}}` | active faults |
| `GET /chaos` · `POST /chaos/clear_all` | | `{"faults":[{"id","kind","subject","since","params"}]}` |
| `POST /chaos/script` | `{"name":"standard","seed":42}` | `{"script_id"}` |
| `POST /nodes/add` | `{"display_name","labels"}` | proc |
| `POST /cluster/reset` | `{"seed":true,"mode":"vault"}` | `{"ok":true,"elapsed_s"}` |
| `POST /demo/seed` | `{"bucket":"clinic","count":200}` | `{"uploaded":200}` |

### 7.5 Oracle (:7090)

| Method & path | Request | Response |
|---|---|---|
| `POST /runs` | `{"mode":"vault\|naive","seed":42,"duration_s":60,"clients":8,"keyspace":200,"chaos_script":"standard\|heavy"}` | `{"run_id"}` · `409 run_active` (runs are sequential: each resets the cluster) |
| `GET /runs` | | `{"runs":[RunSummary]}` (latest first) |
| `GET /runs/{id}` | | `RunStatus` |
| `GET /runs/{id}/stream` | | SSE `event: status` every 500 ms |
| `POST /runs/{id}/stop` | | `RunStatus` |

```json
RunStatus = {"run_id","mode","seed","state":"preparing|running|settling|verifying|done|failed",
  "started_at","elapsed_s",
  "counters":{"ops":4812,"acked_puts":2311,"acked_deletes":470,"reads":1920,"unknown":14,"failed":37,"faults_injected":47},
  "violations":{"lost":0,"damaged":0,"resurrected":0,"stale_read":0,"phantom_read":0},
  "health":{"under_protected":0},
  "samples":[{"kind":"lost","key":"k042","human":"k042 was saved at 12:03:11 and is now missing.","technical":"acked put commit_seq=1811; final GET 404"}],
  "timeline":[{"t":10.0,"kind":"chaos","label":"crash n2"}]}
```

---

## 8. Events, snapshot, metrics

### 8.1 Event

```json
{"id":1234,"ts":1727330000.4,"type":"node.down","severity":"warn",
 "subject":{"node":"n3"},
 "human":"Lab Laptop stopped responding. Your files are still readable.",
 "technical":"φ=9.3 ≥ 8; peers n1,n2,n4 cannot reach n3 (confirm 1.0 s)",
 "data":{"phi":9.3}}
```
Event types (human templates live in DESIGN §6.3; backend fills them in `vault/common/events.py`):
`node.joined · node.suspect · node.partitioned · node.down · node.recovered_in_grace · node.dead · node.rejoined · node.fenced · node.slow · node.drained · link.relayed · link.restored · write.quorum_failed · read.failover · fragment.corrupt · fragment.missing · repair.started · repair.progress · repair.completed · repair.blocked · repair.failed · scrub.completed · fate.at_risk · fate.fixed · fate.limited · fate.cluster_wide · rebalance.started · rebalance.completed · gc.cleaned · meta.recovered · node.startup_discarded · config.mode_changed · chaos.* · oracle.run_started · oracle.violation · oracle.run_completed`

High-frequency activity (individual writes and reads) is **not** an event. It appears as counters in the snapshot. `repair.progress` is emitted at most once per second per incident.

### 8.2 Snapshot (`GET /v1/cluster`, SSE `snapshot`)

```json
{"ts":1727330000.5,"mode":"vault","config_version":12,
 "summary":{"level":"ok|degraded|at_risk|critical","human":"Your data is safe. 6 of 6 machines are healthy.",
            "technical":"min IFL 3; 0 chunks below target",
            "files":204,"logical_bytes":312000000,"raw_bytes":936000000,"overhead":3.0,
            "under_replicated_chunks":0,"at_risk_files":0,"unreadable_files":0,"min_ifl":3},
 "nodes":[{"id":"n1","display_name":"Reception PC","addr":"127.0.0.1:7101","state":"ALIVE","phi":0.4,
           "route":"direct","slow":false,"fenced":false,"epoch":1,
           "labels":{"power":"A","switch":"S1","disk_batch":"D1","version":"1.0"},
           "disk_used":150000000,"capacity":2147483648,"fragments":402,
           "faults":[]}],
 "control":[{"id":"meta","ok":true},{"id":"gw","ok":true}],
 "links":[{"a":"meta","b":"n5","a_to_b":false,"b_to_a":false,"relay":"n2"}],
 "repair":{"queued":{"p0":0,"p1":0,"p2":0,"p3":0,"p4":0},"active":[{"job_id","kind","reason","src","dst","bytes","progress"}],"mbps":0.0},
 "incident":{"id":7,"kind":"node_dead","subject":"n3","fault_at","detected_at","repair_started_at","recovered_at":null,
             "affected_chunks":214,"remaining_chunks":80},
 "traffic":{"puts_per_s":3.2,"gets_per_s":5.1}}
```
`links` lists only abnormal links (blocked in either direction), each with the relay in use, if any.

**Summary level:** `critical` if any live file is unreadable now; else `at_risk` if any chunk is one failure from loss or IFL = 1; else `degraded` if any chunk is below target; else `ok`.

### 8.3 Metrics and fate report

```json
Metrics = {"availability_60s":0.998,"readable_now_pct":100.0,
  "overhead":{"cluster":2.41,"by_policy":{"rep3":3.0,"ec42":1.5,"rep2":2.0}},
  "last_incident":{"id":7,"detect_s":1.9,"grace_s":8.0,"repair_s":4.1,"mttr_s":14.0,"bytes":220000000},
  "incidents_avg_mttr_s":14.6,"unnecessary_repairs_avoided":3,
  "repair":{"bytes_total":880000000,"mbps_now":0.0},
  "rebalance":{"last":{"moved_fraction":0.15,"ideal_fraction":0.143}},
  "ifl":{"histogram":{"1":0,"2":0,"3":204},"min":3,"at_risk_files":0},
  "scrub":{"checked_last_pass":2412,"corrupt_found_total":10}}
FateReport = {"fate_keys":["power","switch","disk_batch"],
  "domains":[{"key":"power","value":"A","nodes":["n1","n2"],"cluster_wide":false}],
  "cluster_wide":[{"key":"version","value":"1.0","human":"All machines run the same software version (1.0). One bug could affect all of them."}],
  "files":{"histogram":{"1":0,"2":0,"3":204},"at_risk":[{"bucket","key","ifl","target","shared":[{"key","value"}]}]},
  "advice":[{"human","technical"}],"last_audit_at":1727330000.0}
```

---

## 9. Configuration (`vault.yaml`, repo root)

```yaml
cluster:
  data_dir: ./data
  logs_dir: ./logs
  host: 127.0.0.1
  ports: { supervisor: 7070, metadata: 7000, gateway: 7080, oracle: 7090, node_base: 7101 }
  chunk_size: 1048576
  node_capacity_bytes: 2147483648
  default_buckets: { clinic: rep3, archive: ec42, scratch: rep2, oracle: rep3 }

nodes:   # id: display name + labels
  n1: { display_name: "Reception PC",  labels: { power: A, switch: S1, disk_batch: D1, version: "1.0" } }
  n2: { display_name: "Doctor's Desk", labels: { power: A, switch: S2, disk_batch: D2, version: "1.0" } }
  n3: { display_name: "Lab Laptop",    labels: { power: B, switch: S2, disk_batch: D3, version: "1.0" } }
  n4: { display_name: "Pharmacy PC",   labels: { power: B, switch: S3, disk_batch: D1, version: "1.0" } }
  n5: { display_name: "Records Room",  labels: { power: C, switch: S3, disk_batch: D2, version: "1.0" } }
  n6: { display_name: "Admin Office",  labels: { power: C, switch: S1, disk_batch: D3, version: "1.0" } }

policies:
  rep2: { type: replication, n: 2, w: 2 }
  rep3: { type: replication, n: 3, w: 2 }
  ec42: { type: erasure, k: 4, m: 2, w: 5 }

fate:
  keys: [power, switch, disk_batch, version]     # most → least important
  improve_max_node_fill: 0.85
  improve_max_imbalance: 0.20

placement: { vnodes_per_node: 128 }

detector:
  heartbeat_ms: 500
  ping_ms: 1000
  window: 100
  min_std_ms: 200
  phi_suspect: 8.0
  confirm_ms: 1000
  dead_after_s: 15          # demo: 8
  lease_ttl_ms: 5000        # must be < dead_after_s * 1000
  startup_grace_s: 10
  slow_rtt_ms: 300

repair: { scan_ms: 500, max_concurrent: 8, per_node: 2, bandwidth_mbps: 50, max_attempts: 3 }
scrub:  { rate_mbps: 20, recent_first_minutes: 5 }
gc:     { interval_s: 5, upload_timeout_s: 60, superseded_grace_s: 30, orphan_grace_s: 60 }
inventory: { interval_s: 30 }
gateway: { chunks_in_flight: 2, spares: 2, hedge_ms: 150, timeout_control_s: 1.0, timeout_data_s: 10.0 }

safety:                      # Naive mode turns every switch off
  verify_on_receive: true
  verify_on_read: true
  durable_writes: true
  quorum_writes: true
  read_failover: true
  repair: true
  scrub: true
  fate_aware_placement: true
  fencing: true
  relay: true

storage: { sim_page_cache: false, dirty_expire_ms: 5000 }   # P1; Oracle runs set true
chaos:   { enabled: true }
oracle:  { clients: 8, keyspace: 200, duration_s: 60, settle_max_s: 60, seed: 42, script: standard }
web:     { origin: "http://localhost:3000" }
```
Environment overrides: `VAULT_CONFIG=path/to/vault.yaml`; `VAULT_DEMO=1` sets `dead_after_s: 8`.

---

## 10. Repository layout

Component folders. Owner-name prefixes for owner-specific files are added in the task split (TEAM_PROTOCOL §2). Shared files marked ★ belong to Anushka.

```
vault/
  vault.yaml ★                 requirements.txt ★           README.md ★
  vault, vault.cmd ★           # launchers: `./vault up` → python -m vault up
  docs/ ★                      # PRD, ARCHITECTURE, TECH_STACK, DESIGN, MASTER_PLAN, OWNERSHIP, contracts/
  backend/vault/
    __main__.py                # CLI: up | reset | seed | status
    common/                    # ★ shared contracts and utilities
      models.py ★              # pydantic models = §7 contract
      config.py  ids.py  hashing.py  rpc.py  netsim.py  events.py  placement.py  fate.py  phi.py  ec.py  log.py
    node/        app.py  storage.py  scrubber.py  heartbeat.py  pinger.py  pull.py  relay.py  chaos.py
    metadata/    app.py  db.py  schema.sql  uploads.py  objects.py  cluster.py  stream.py  reports.py
    brain/       detector.py  membership.py  scheduler.py  repair.py  auditor.py  rebalancer.py
                 reconciler.py  gc.py  incidents.py  metrics.py  summary.py
    gateway/     app.py  put.py  get.py  routing.py  stats.py
    supervisor/  app.py  procs.py  chaos_ctl.py  scripts.py  seed.py
    oracle/      app.py  workload.py  ledger.py  checker.py  runs.py
    tests/       test_placement.py  test_fate.py  test_phi.py  test_ec.py  test_commit.py  test_storage.py
  web/
    app/         (landing)/page.tsx   console/{page,files,fate,oracle}/page.tsx   layout.tsx   globals.css
    components/  brand/  console/  scene/  files/  fate/  oracle/  ui/
    lib/         contracts.ts ★  api.ts  sse.ts  store.ts  format.ts
    public/brand/vault-mark.svg
  samples/                     # generated synthetic files (git-ignored)
  data/  logs/  oracle_runs/   # git-ignored
```

---

## 11. Process and concurrency model

- One uvicorn worker per process (in-memory state must not be split). Background loops start in the FastAPI `lifespan`.
- One shared `httpx.AsyncClient` per process (`limits=httpx.Limits(max_connections=200, max_keepalive_connections=50)`), control timeout 1 s, data timeout 10 s, always through `rpc.py`.
- **Metadata DB:** one writer connection guarded by an `asyncio.Lock` (serializes commits, which is what gives `commit_seq` its order) plus a separate read connection (WAL allows concurrent readers). Use `aiosqlite`. Every multi-row change is one `BEGIN IMMEDIATE … COMMIT`.
- **Blocking work off the event loop:** `fsync`, `os.replace`, directory scans and scrub reads via `asyncio.to_thread`. SHA-256 of 1 MiB (~2 ms) and zfec encode/decode (~2 ms) can run inline.
- **Node write path:** per-fid lock so a concurrent pull and PUT of the same fid can't interleave.
- **Windows:** `os.replace` for renames, skip directory fsync, `proc.kill()` for crashes.

---

## 12. Failure-mode table (what the judges can do, and what happens)

| Fault | Detected by | System response | User sees |
|---|---|---|---|
| Machine crash | Heartbeats stop → φ → no peer reaches it | DOWN → grace → DEAD (epoch++) → prioritized repair | "Lab Laptop stopped responding… rebuilding 214 copies (63%)" + MTTR |
| Machine reboot within grace | Heartbeats resume | Back to ALIVE, no repair | "Lab Laptop is back. No rebuild needed." |
| Machine freeze (hang) | Same as crash | Same as crash; lease expires so it fences itself | Same, plus "fenced" badge if it wakes |
| Slow machine | RTT > 300 ms | Read last, not a repair source, hedged reads (P1) | Snail badge |
| One link cut (meta↔node) | Direct heartbeat fails, peers still reach it | Relay via a peer, no repair | Relay badge, "0 unnecessary rebuilds" |
| Gateway↔node cut | Write/read NetworkError | Relay; else spare target or next holder | Nothing breaks |
| Asymmetric cut (P1) | One direction fails | Relay in the failing direction only | Relay badge |
| Node isolated from everyone | No heartbeats, no peer reach | DOWN → DEAD; node fences itself after 5 s | As crash |
| Silent corruption | Scrubber or read verification | Quarantine, repair from a verified copy | "Found a damaged copy of xray-0042.png on Records Room. Replaced it." |
| Missing fragment (file deleted on disk) | Inventory or read 404 | Mark missing, repair | Same as above |
| Disk full | PUT 507 | Placement excludes full node; spare used | "Pharmacy PC is full" |
| Power cut (label) | Many nodes DOWN at once | Grace; if restored in time nothing happens; if not, repair in priority order | Power-strip island goes dark in 3D view |
| Power cut (all) | Everything restarts | Startup recovery (§4.14), startup grace prevents repair storm | Boot screen: "Recovered 204 files… 0 lost" |
| Metadata crash | Gateway gets NetworkError | Writes 503 until restart; recovery from WAL | "Vault's index is restarting…" |
| Dead node returns | Heartbeat from a DEAD node | Re-register, new epoch, reconcile, trim extra copies | "Lab Laptop rejoined. Removed 214 extra copies." |
| Label change (shared fate) | Auditor | Move copies (make-before-break) or explain the limit | "143 files had all copies on Power Strip A. Moved…" |
| Add machine | Membership change | Rebalance ≈1/N | "Moved 15% of data to the new machine (ideal 14%)" |

---

## 13. Known limitations (state them honestly)

1. **Single metadata service.** Crash-safe, not highly available. While it's down, writes (and P0 reads) are unavailable. Next step: a standby via WAL shipping, then Raft.
2. **One gateway.** No multi-gateway coordination.
3. **Latest version only.** Old versions are garbage-collected after 30 s; no version history UI.
4. **Relay is one hop.** A partition that needs two intermediate hops is treated as unreachable.
5. **Oracle checks single-key histories.** It doesn't check multi-key transactions (Vault has none). It is evidence, not proof.
6. **Simulated machines.** Processes on one laptop share a real disk and CPU. Labels simulate power and network topology. The same code runs across real machines given real addresses.
7. **Process kill ≠ power loss** unless `sim_page_cache` is on (P1).
8. **Scale:** designed and tested for thousands of objects; SQLite metadata is the ceiling.
