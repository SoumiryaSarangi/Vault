# Vault: Product Requirements (PRD)

**Status:** v1.0, frozen for build · **Owner:** Anushka (changes need her approval, see TEAM_PROTOCOL §4) · **Date:** 2026-09-26
**Read with:** `ARCHITECTURE.md` (how it works), `TECH_STACK.md` (what we build it with), `DESIGN.md` (how it looks and talks).

---

## 0. TL;DR

Vault is a **self-healing object store for places that have a few ordinary computers and no cloud or IT staff.** It splits files into checksummed chunks, spreads copies (or erasure-coded fragments) across machines that don't share a power strip, switch or disk batch, detects failures in seconds, repairs itself, and explains every action in plain language.

For the hackathon, the "machines" are 6 storage processes on one laptop, each with its own folder and port. Every failure is injected live from the dashboard.

**Two headline features:**
1. **Shared-Fate Auditor:** checks whether a file's copies can die together, and fixes it.
2. **Durability Oracle:** an independent ledger that checks, live, that nothing acknowledged was ever lost, damaged, brought back after deletion, or served stale.

**Pitch line:** *"Everyone counts copies. Vault checks whether they can die together, and keeps its own passbook to show nothing was lost."*

---

## 1. The problem

**Problem statement (verbatim):**
> Vault: Build a fault-tolerant distributed object storage system capable of storing, replicating, retrieving, and repairing large volumes of data across unreliable and independently failing storage nodes. The system must handle concurrent reads and writes, configurable replication and durability policies, node failures, partial network partitions, data corruption, replica inconsistency, background rebalancing, integrity verification, metadata consistency, and automatic replica repair while maintaining predictable availability and minimizing recovery time and storage overhead.

**In plain words:** machines break all the time (crashes, power cuts, bad disks, broken cables, silent bit rot), but people expect their files to never disappear and never come back wrong. Build storage that stays correct and available while its parts fail, heals itself, and does it without wasting disk or taking long to recover.

---

## 2. Who it's for

**Primary persona: the edge site.** A rural clinic, a factory floor, a school. Three to six ordinary PCs, unreliable power, patchy or no internet, and nobody who knows Linux.
- **Job to be done:** "Keep our files safe on the machines we already have, and tell me in plain words if I need to do anything."
- **What they never want to see:** config files, terminal commands, jargon like "PG degraded".

**Secondary persona: the learner.** A developer who wants to understand how S3-style storage works. Production systems are too big to read, and S3 is a black box. The dashboard's **Explain** mode shows the technical reason behind every plain-language message.

**Why now (verified facts):**
- MinIO's open-source edition was dismantled: admin console removed (May 2025), binaries and Docker images stopped (Oct 2025), maintenance mode (Dec 2025), repository archived (Feb 2026).
- Cloud object storage (S3) needs a reliable internet connection that edge sites don't have.

**Honest landscape** (a judge may know these, so we never claim "nothing like this exists"):

| System | Good at | Gap for our persona |
|---|---|---|
| Ceph | Everything, at scale (CRUSH places replicas across racks) | Needs storage experts to run |
| MinIO CE | Was the easy default | Archived, no admin UI, no official binaries |
| SeaweedFS | Fast, simple for engineers | CLI and config driven |
| Garage | Closest match: small self-hosted clusters, tolerates power cuts and slow links, spreads copies across zones | Operated via CLI and config. No independence audit of arbitrary shared dependencies, no built-in correctness oracle, no plain-language operations |

**Our wedge:** storage that *explains itself*, *audits whether its copies are truly independent*, and *measures its own durability live*.

---

## 3. Product principles

1. **Correct before clever.** Never return wrong bytes. Never acknowledge a write before it is durable on enough machines.
2. **Measure, don't claim.** Every guarantee on the dashboard has a live number next to it. Say "measured" or "checked", never "proven" or "guaranteed".
3. **Explain every automatic action.** Every event has a plain-language line and a technical line.
4. **One command to run.** Sensible defaults, no configuration needed for the demo.
5. **Demo first.** If a feature can't be shown in the 3-minute demo, it's P1 or later.

---

## 4. Unique selling points

### 4.1 Shared-Fate Auditor (headline)

**The hidden problem:** "3 copies on 3 different machines" doesn't mean "3 independent copies". If all 3 machines share a power strip, a switch, a disk batch or a buggy software version, one event destroys all 3. You really have 1 copy.

**What Vault does:**
1. Every machine carries labels: `power`, `switch`, `disk_batch`, `version`. They are editable live.
2. **Effective copies** for each file = *the smallest number of independent failures that destroys every copy* (technical name: IFL, independent failures to loss). Three copies on one power strip = 1. Three copies on three separate strips, switches and batches = 3.
3. New writes are placed so copies don't share a dependency when possible.
4. When labels change, the auditor re-scores every file, moves copies to raise effective copies (make-before-break), and when the machines themselves make full independence impossible, says so plainly: *"Power Strip A now feeds 4 machines. Give Lab Laptop its own power supply to get 3 fully independent copies."*
5. A dependency shared by **every** machine (for example, all run the same software version) can't be fixed by moving copies. It is reported as a **cluster-wide risk**, not scored.

**Positioning:** production systems already spread copies across racks (Ceph CRUSH, HDFS rack awareness, Garage zones). Vault generalizes this to *any* shared dependency, audits existing placements continuously, and shows the result. Research basis: *Heading Off Correlated Failures through Independence-as-a-Service* (INDaaS), OSDI 2014.

### 4.2 Durability Oracle (headline)

**The hidden problem:** storage bugs are silent. A "saved" file is quietly missing, has wrong bytes, or a deleted file comes back. A demo where "everything looks fine" shows nothing.

**What Vault does:**
1. A **test client** (not Vault) runs concurrent uploads, downloads and deletes, and writes every result to an append-only **ledger** after it gets Vault's reply. Vault never writes the ledger, so it can't grade its own homework.
2. Operations that time out are recorded as **unknown** (they may or may not have happened), and the checks accept either outcome for them. This avoids false alarms.
3. While a **seeded, scripted chaos run** crashes machines, cuts links, corrupts data and cuts power, and after the system settles, the Oracle checks:
   - **Lost:** an acknowledged file is missing.
   - **Damaged:** returned bytes don't match anything ever written to that key.
   - **Resurrected:** an acknowledged delete came back.
   - **Stale read:** a read returned an older version than one already acknowledged before the read started.
   - **Under-protected:** after settling, a file has fewer healthy copies than its policy (a health issue, counted separately from data loss).
4. **Vault vs Naive:** the same code with safety switches off (no checksum verification, write to 1 machine, no repair, no crash-safe writes, no fate-aware placement, no fencing, no relay), running the same seeded chaos script. The dashboard shows both counters side by side.

**Wording rule:** "{N} faults injected, 0 violations, measured against an independent ledger" (the `heavy` script injects ≈ 500 fault units). Never "proven".
**Research basis:** Fawkes (SOSP 2025) found 48 data-durability bugs in real databases by checking recovered state against expected state. Amazon S3's ShardStore team checks the implementation against executable reference models (SOSP 2021).
**Bonus:** it is our safety net for AI-written code. The Oracle runs from hour 6 onward, not only in the demo.

### 4.3 Supporting differentiators

| Differentiator | One line | Basis |
|---|---|---|
| **"Can't reach" is not "dead"** | Detects partial network partitions and routes around them through a machine that can reach both sides, instead of rebuilding data on a machine that's alive | Nifty, OSDI 2020 |
| **Suspicion, not timeouts** | Phi-accrual failure detector, peer confirmation, then a grace period before any expensive repair | Cassandra, Akka |
| **Honest power cut** | Crash-safe writes and startup recovery. P1: a simulated page cache so killing a process loses unsynced data exactly like real power loss | S3 ShardStore crash-consistency work |
| **Plain-language operations** | Every event reads like a sentence a clinic manager understands, and Explain mode shows the mechanism | Persona |
| **Measured storage overhead** | Replication (2×, 3×) and erasure coding 4+2 (1.5×) side by side, live | Azure LRC, MinIO |

---

## 5. Scope

### 5.1 P0: must ship (the demo breaks without these)

| ID | Feature | Acceptance criteria |
|---|---|---|
| F1 | **Object API** (gateway) | `PUT/GET/HEAD/DELETE /{bucket}/{key}`, `GET /{bucket}` list, `PUT /{bucket}` create. Streams bodies in 1 MiB chunks; a 100 MB upload never holds more than ~4 MiB in gateway memory. Downloaded bytes' SHA-256 equals uploaded. |
| F2 | **Durability policies** | Per bucket: `rep2`, `rep3` (default), `ec42` (Reed-Solomon 4+2 via zfec). Write quorum W per policy. Overhead shown per policy. |
| F3 | **Commit protocol** | Fragments are durable on ≥W machines before metadata commits. A version becomes visible atomically. Versions per key are monotonic. Deletes are tombstones. |
| F4 | **Crash-safe storage node** | Write to temp file, fsync, rename, fsync directory. SHA-256 verified on receive and on read. On startup, removes incomplete writes and reports them. |
| F5 | **Metadata service** | SQLite in WAL mode with `synchronous=FULL`. Survives kill -9 with no committed object lost. Serves cluster snapshot, events, and an SSE stream. |
| F6 | **Failure detection and fencing** | 500 ms heartbeats, phi accrual (suspect at φ ≥ 8), peer confirmation, `DOWN` then `DEAD` after a grace period. Node epochs and leases: a fenced node refuses writes. A crash is detected in ≤ 3 s. |
| F7 | **Partial-partition handling** | Connectivity matrix from all participants. One-hop relay through a node that reaches both sides. Cutting one link (e.g. metadata ↔ one node) triggers **zero** repairs. |
| F8 | **Automatic repair** | Priority queue (chunks one failure from loss first), throttled bandwidth, copies only from a verified source, EC rebuild via decode. Incident tracking with detect, grace and repair times. |
| F9 | **Integrity verification** | Background scrubber on every node, plus verification on every read. Damaged copies are quarantined and repaired (read repair). "Scrub now" button. |
| F10 | **Reconciliation and garbage collection** | Nodes report their inventory. Metadata marks missing copies for repair and orphans for removal. Superseded versions and aborted uploads are cleaned up after a grace period. A rejoining node's extra copies are trimmed. |
| F11 | **Placement and rebalancing** | Consistent hash ring with virtual nodes, fate-aware. Adding a machine moves roughly 1/N of the data (shown vs ideal). Draining a machine moves everything off it. Moves are make-before-break and throttled. |
| F12 | **Shared-Fate Auditor** | §4.1. Effective copies per file and cluster. Relabeling triggers re-audit and fixes within 10 s. Unfixable limits and cluster-wide risks are explained. |
| F13 | **Durability Oracle** | §4.2. Seeded chaos script, Vault vs Naive, live counters, violation samples with file names. |
| F14 | **Chaos control** | Kill or restart a machine, power cut (all, or by label such as `power=A`), cut link (both directions or one), slow, freeze, corrupt N copies, disk full, relabel, add machine, clear all. |
| F15 | **Dashboard** | Landing page plus console (Overview, Files, Fate, Oracle). Plain language with an Explain toggle. KPI strip. Fits 1280×720 with no scrolling on Overview. |
| F16 | **One-command launch** | `python -m vault up` starts metadata, gateway, 6 nodes, oracle and supervisor. `python -m vault reset` restores a clean seeded state in ≤ 20 s. |

### 5.2 P1: should ship if on schedule

- **Simulated page cache** (so a power cut loses unsynced data in Naive mode, exactly like real power loss)
- **Conditional writes** (`If-Match`, `If-None-Match: *`)
- **Hedged reads** (ask a second copy if the first is slow)
- **Read-only mode** when metadata is down (gateway manifest cache)
- **Merkle-style inventory digests** instead of full inventory lists
- **Oracle ablation runs** (one safety switch off at a time, showing which protection prevents which violation)
- **Tradeoff sliders:** repair bandwidth and grace period
- **Advisor** messages ("add a machine on a different power supply")
- Landing page "proof band" pulls the latest Oracle numbers live
- `vault up` also starts the dashboard and opens the browser

### 5.3 P2: stretch

Local Reconstruction Codes, a standby metadata replica, a remote site over a simulated WAN link with offline sync (hinted handoff), an S3-compatible API subset, multiple gateways, a guide for real multi-machine deployment, encryption at rest.

### 5.4 Non-goals (say no if asked)

Authentication, users, ACLs, multi-tenancy · Kubernetes or a Docker requirement · real hardware for the demo · version history UI (we keep the latest version only) · byte-range reads · S3 multipart API (we stream instead) · production hardening.

---

## 6. Problem-statement coverage matrix

Every keyword in the PS, how we solve it, and how the judges see it.

| PS keyword | Mechanism | Shown in demo by | Metric on screen |
|---|---|---|---|
| Storing & retrieving large volumes | Chunked streaming (1 MiB), fragments on disk, verified reassembly | Upload a 50 MB file, download it | Throughput, SHA-256 match |
| Replicating | `rep2`/`rep3` fragments on distinct machines, W acks | Files → Inspect grid | Copies per chunk |
| Repairing | Prioritized repair loop, EC rebuild | Kill a machine | Recovery time breakdown |
| Concurrent reads & writes | Metadata serializes commits (global `commit_seq`), immutable versions, atomic publish | Oracle: 8 concurrent clients | Stale reads = 0 |
| Configurable replication & durability | Per-bucket policy (`rep2`, `rep3`, `ec42`) and W | Files: bucket policy badges | Overhead per policy |
| Node failures | Phi accrual, peer confirmation, grace, epochs | Kill a machine | Detection time |
| Partial network partitions | Connectivity matrix, one-hop relay, leases and fencing, single commit authority | Cut one link | Unnecessary repairs = 0, relay badge |
| Data corruption | SHA-256 on receive, read and scrub; quarantine; repair from a verified copy | Corrupt 10 copies | Damaged copies found and fixed |
| Replica inconsistency | Immutable versioned fragments, inventory reconciliation, read repair, rejoin trim | Restart a dead machine | Extra copies trimmed; Oracle stale/phantom = 0 |
| Background rebalancing | Ring with virtual nodes, throttled make-before-break moves | Add a machine | Data moved vs ideal (≈1/N) |
| Integrity verification | Background scrubber plus on-demand scrub | Scrub now | Copies checked, damaged found |
| Metadata consistency | Single-writer SQLite WAL, data-before-metadata commit, orphan GC, reconciliation | Power cut everything | "Recovered N files, 0 lost" |
| Automatic replica repair | Same as repairing, plus read repair | Corrupt, then read | Repairs completed |
| Predictable availability | Stated guarantees (§7) plus live measurement | Availability tile during chaos | Availability % |
| Minimizing recovery time | Fast detection, parallel prioritized repair, tunable grace and bandwidth | Kill a machine | Detect + grace + repair seconds |
| Minimizing storage overhead | Erasure coding 4+2 vs replication | Policy comparison | 1.5× vs 3.0× |

---

## 7. Guarantees ("predictable availability")

For the default cluster of 6 machines. These are the promises the dashboard shows and the Oracle checks.

| Policy | Storage overhead | A write needs | No data loss with, at full health | Readable while |
|---|---|---|---|---|
| `rep2` (n=2, W=2) | 2.0× | metadata + 2 machines | 1 machine failure | ≥1 verified copy of each chunk is reachable |
| `rep3` (n=3, W=2) | 3.0× | metadata + 2 machines | 2 simultaneous machine failures | ≥1 verified copy of each chunk is reachable |
| `ec42` (k=4, m=2, W=5) | 1.5× | metadata + 5 machines | 2 simultaneous machine failures | ≥4 verified fragments of each chunk are reachable |

**Honest caveats (say these before a judge does):**
1. **"At full health" means after repair completes.** Right after an acknowledged write with W < n, a chunk has W pieces until repair adds the rest (seconds). In that window, `rep3` and `ec42` tolerate 1 failure.
2. **Guarantees count machines, not dependencies.** If machines share power, one outage can take several. That's exactly what effective copies measure. On the default layout, `ec42` survives any 1 power-strip failure (its 6 fragments need all 6 machines, and each strip feeds 2).
3. **Metadata is one service.** It's crash-safe (write-ahead log), so no committed data is lost when it dies. While it's down, writes are unavailable, and reads are unavailable in P0 (P1 adds cached read-only mode). This is a deliberate choice, the same one GFS and HDFS made.
4. **Partitions:** the side that can reach metadata (directly or through one relay) keeps working. A node cut off from metadata for longer than its lease stops accepting writes (fenced), so there is never a second version of the truth (no split brain).
5. **Damaged bytes are never served** while `verify_on_read` is on: every piece is checked against its SHA-256 before it leaves the system.
6. **Timing:** a crash is confirmed in about 2.5 s. Repair starts after a 15 s grace period (8 s in the demo) so a machine that is just rebooting doesn't trigger a full rebuild.

---

## 8. Metrics (exact definitions)

| Metric | Definition | Where |
|---|---|---|
| **Effective copies** | Per chunk: minimum number of independent failures (machines or shared dependencies) whose loss makes the chunk unrecoverable. Per file: minimum over its chunks. Cluster: distribution plus the minimum. | KPI strip, Files, Fate |
| **Recovery time (MTTR)** | Per incident: `recovered_at − fault_at`, shown as **detect** (`detected_at − fault_at`, where detected = confirmed DOWN) + **grace** + **repair** (`recovered_at − repair_started_at`). `fault_at` is the supervisor's injection timestamp (ground truth) or, for organic failures, the last heartbeat. "Recovered" = every affected chunk back at its policy's target. | KPI strip, incident card |
| **Availability** | Rolling 60 s: successful client operations ÷ all client operations at the gateway (404s and precondition failures count as successes, since the system answered correctly). Also **readable now**: % of live files with enough verified pieces on reachable machines. | KPI strip |
| **Storage overhead** | Raw bytes of healthy pieces for live versions ÷ logical bytes of live objects. Cluster-wide and per policy. | KPI strip, Files |
| **Durability violations** | Oracle counters: lost, damaged, resurrected, stale read, phantom read. Under-protected is reported separately. | KPI strip, Oracle |
| **Repair activity** | Queue by priority, active jobs, bytes moved, MB/s. | Overview |
| **Rebalance efficiency** | Bytes moved after adding a machine ÷ total bytes, next to the ideal 1/N. | Event + Overview |

---

## 9. Demo script (3 minutes)

**Setup before judging:** `python -m vault reset`, seeded with ~200 synthetic clinic files (no real patient data). Grace period set to 8 s. A Naive Oracle run is already recorded.

| Time | Scene | What the judges see | Line (Urooz) |
|---|---|---|---|
| 0:00 | **Hook** | Landing: liquid-metal Vault logo | "This clinic has six old PCs, unreliable power and no internet. Where do the X-rays go?" |
| 0:15 | **Healthy** | Console all green: 6 machines, effective copies 3 | "Every file is split, checksummed and copied to machines that don't share power." |
| 0:30 | **Kill a machine** | Lab Laptop: suspect → confirming → down → rebuilding. Files stay readable. MTTR tile: detect ~2 s + grace 8 s + repair ~4 s | "Vault doesn't guess. It gets suspicious, asks the other machines, waits a moment, then rebuilds." |
| 1:00 | **Silent corruption** | Corrupt 10 copies. "Found 10 damaged copies, replaced from healthy ones." | "Disks lie quietly. Vault checks every byte." |
| 1:15 | **Cut one cable** | Cut metadata ↔ Records Room. Relay badge, 0 rebuilds. | "It's not dead, it's unreachable. Vault routes around it instead of copying terabytes." |
| 1:35 | **Shared fate** | Relabel: Lab Laptop and Records Room moved onto Power Strip A. ~half the files drop to effective copies 1. Vault moves copies, score rises to 2, and it gives advice. Cut Power Strip A: everything still readable. | "Three copies on one power strip is one copy. Vault catches that." |
| 2:00 | **Pull the plug** | Power cut everything. Boot screen: "Recovered 204 files from the log. Discarded 1 unfinished write. 204 of 204 files present." | "Power cuts are normal here. Vault comes back exactly where it was." |
| 2:20 | **The Oracle** | Side by side (both runs recorded before judging, `heavy` script ≈ 500 faults): Vault 0 violations vs Naive's count | "Everyone claims no data loss. We keep an independent ledger and check it live." |
| 2:45 | **Close** | Tagline | "Vault: S3-grade durability on a few ordinary machines. No cloud. No sysadmin." |

**Fallbacks:** a recorded backup video of the full run · `python -m vault reset` restores a known state in ≤ 20 s · a 2D toggle if the 3D view stutters on the venue projector · every chaos action is also a single button, so no typing on stage.

---

## 10. Pitch notes (for Urooz)

**Problem:** unreliable machines, no cloud, no IT staff, and data that must not be lost.
**Why it matters:** edge sites hold critical records. The easy self-hosted option (MinIO CE) is gone, and cloud needs internet.
**What we built:** self-healing storage that detects, repairs and explains, plus two things no one else checks: whether copies can die together, and whether anything acknowledged was ever lost.
**Proof:** live chaos, live numbers, an independent ledger.
**Impact:** storage you can trust on hardware you already own.

**Likely judge questions:**

| Question | Answer |
|---|---|
| What if the metadata server dies? | It's crash-safe (SQLite write-ahead log, fsync on commit), so nothing committed is lost. While it's down, writes pause. Same trade as GFS and HDFS. Next step: a standby replica via log shipping. |
| Why not Raft for metadata? | In 20 hours, a correct single writer beats a buggy consensus implementation. We made it crash-safe and measured it. Raft is the documented next step. |
| Killing a process isn't a real power cut. | Correct: the OS page cache survives a process kill. Vault fsyncs before acknowledging, so it's safe either way. The P1 simulated page cache makes a kill lose unsynced data like real power loss, which is how the Naive comparison shows the difference. |
| How is this different from Ceph CRUSH or rack awareness? | Those spread copies across fixed hierarchy levels (host, rack). We handle any shared dependency, including labels that change later, audit existing data continuously, and explain the result. |
| How do you know it's correct? | An independent ledger written by the test client, checked against the system under seeded chaos. It's strong evidence, not a mathematical proof, and it checks single-key histories. |
| What's your CAP choice? | Consistency for commits (one authority), availability within quorum limits for data. The side that can't reach metadata refuses writes instead of forking the truth. |
| Why W=2 of 3? | Acknowledging after 2 of 3 survives one failure immediately and keeps writes working with a machine down. The third copy is added by repair within seconds. |
| How do you tell dead from unreachable? | Phi accrual suspicion, then we ask the other machines. If any of them can reach it, it's a partition: we relay, and we don't rebuild. |
| Why erasure coding? | 1.5× overhead instead of 3× for the same 2-failure tolerance. The cost is repair traffic: rebuilding one fragment reads 4. Azure's LRC work optimizes exactly that. |
| Does this run on real machines? | Yes. Everything is HTTP between addresses in `vault.yaml`. The demo runs on one laptop so we can break things on stage. |
| How far does it scale? | Tested at thousands of objects. The single metadata service is the ceiling. We'd shard it by bucket or replicate it. |

---

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Scope creep | P0 list is frozen. New ideas go to P1/P2 via Anushka. Feature freeze at hour 14. |
| Integration breaks late | Frozen contracts (`backend/vault/common/models.py` and `web/lib/contracts.ts`), mocks from hour 2, merge every 2 hours. |
| Subtle bugs in AI-written code | Oracle runs continuously from hour 6. Violations are bugs, fixed before features. |
| Demo timing is flaky | Seeded chaos script, `vault reset`, shortened grace for the demo, recorded backup video. |
| 3D is slow on the projector | Single canvas with capped pixel ratio, and a 2D fallback toggle. |
| Install problems | Python 3.12 pinned (zfec has no 3.14 wheel), exact npm versions pinned and build-tested (TECH_STACK). |
| Token budget | Docs are the context; each person reads only the sections their component needs (ARCHITECTURE §0). |

---

## 12. Glossary

| Term | Meaning |
|---|---|
| Machine / node | One storage process with its own folder and port (simulates one PC) |
| Object | A file stored under `bucket/key` |
| Version | One immutable write of an object. Each PUT creates a new version. |
| Chunk | A 1 MiB piece of a version |
| Fragment | What a machine actually stores: a full copy of a chunk (replication) or one of k+m coded pieces (erasure coding) |
| Policy | How a bucket protects data: `rep2`, `rep3` or `ec42` |
| W | Write quorum: pieces that must be durable before a write is acknowledged |
| IFL / effective copies | Independent failures to loss. See §4.1. |
| Phi (φ) | Suspicion level from the failure detector. Higher means more likely dead. |
| Epoch | A node's generation number. It changes when the node is declared dead, which fences old writes. |
| Fenced | A node that has lost contact with metadata and refuses writes |
| Relay | Sending a request through a third machine because the direct link is cut |
| Naive mode | Same code with every safety switch off, used for comparison |
