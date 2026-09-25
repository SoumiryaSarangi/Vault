# Soum: updates

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

## [Hour 3] M0 setup
- What was done: local setup per README on Windows (Python 3.13.5, Node 24.11). Read the protocol, master plan, ownership, contracts, PRD, ARCHITECTURE, TECH_STACK and soum_tasks; S1–S9 plan made.
- Files created/changed: none in the repo (`CLAUDE.local.md` copied from `tasks/claude/soum.CLAUDE.local.md`, git-ignored).
- Endpoints / functions / components exposed: none.
- How to run / test it: `python -m pytest -q` (9 passed at the time); `cd web && npm ci && npm run build` OK; `python -m vault up` started all 10 processes.
- Known issues / TODO: `npm ci` failed once with ECONNRESET (flaky network) and passed on retry.
- Anything other teammates must know or do: **Anushka**: `CLAUDE.md` has the "Git commits" section twice plus a stray line "add this into CLAUDE.md" at the end. Three questions for later tasks, not blocking S1/S2:
  1. **Fencing before the first lease (S3).** §4.6 says "lease expired → fenced", but a node that has never registered has no lease. I plan to treat "no lease granted yet" as not fenced (epoch checks still apply). OK?
  2. **`GET /v1/config` response shape (S4, S5).** There is no model in models.py; I'll read the `safety` and `config_version` keys. **Jaiveer**, please confirm when J4 lands.
  3. **Topology for node/gateway rpc (S4).** Should nodes refresh `update_topology()` from `GET /v1/cluster` every 1 s? rpc works without it (it tries relays in order). **Jaiveer**, is that load OK?

## [Hour 3] S1 Erasure coding
- What was done: `ec_encode` / `ec_decode` / `ec_rebuild` with zfec, per the verified TECH_STACK §6.1 code. Pinned signatures kept. Decode and rebuild raise `ValueError` when given fewer than k fragments.
- Files created/changed: `backend/vault/common/soum_ec.py`, `backend/vault/tests/test_soum_ec.py`
- Endpoints / functions / components exposed:
  - `ec_encode(chunk: bytes, k: int, m: int) -> list[bytes]`: n = k+m equal-size fragments; 0..k-1 data (systematic), k..n-1 parity; the chunk is zero-padded to a multiple of k.
  - `ec_decode(frags: dict[int, bytes], k: int, m: int, chunk_size: int) -> bytes`: any k fragments → the chunk, truncated to `chunk_size`.
  - `ec_rebuild(frags: dict[int, bytes], k: int, m: int, want: int) -> bytes`: any k fragments → fragment `want`, byte-identical to the original.
- How to run / test it: `python -m pytest -q backend/vault/tests/test_soum_ec.py` (10 tests): round trip at 0, 1, 1 MiB−1, 1 MiB; decode from all 15 4-of-6 subsets; rebuild of every index from every subset; systematic layout; too few fragments.
- Known issues / TODO: none. A 0-byte chunk encodes to six 0-byte fragments (zfec accepts them), though the gateway never produces an empty chunk (L = 0 → zero chunks).
- Anything other teammates must know or do: `frag_size` for EC = `ceil(chunk_size / k)`, which equals `len(ec_encode(...)[0])`. **Jaiveer**: use the same value in the plan/commit.

## [Hour 3] S2 Crash-safe fragment storage
- What was done: the on-disk fragment store from ARCHITECTURE §3.6 and §4.14:
  - Layout `blobs/<version_id[2:4]>/<fid>.blk`, `tmp/`, `quarantine/`, `node.json`.
  - `.blk` = `b"VLT1"` + uint32 BE header_len + `FragmentHeader` JSON + payload.
  - Atomic write per TECH_STACK §6.3: tmp + fsync + `os.replace`, with dir fsync skipped on Windows.
  - Startup deletes and counts `tmp/*` and rebuilds the in-memory index.
  - Per-fid `asyncio.Lock`; all file I/O in `asyncio.to_thread`.
  - Safety switches: `durable_writes` off → direct write; `verify_on_read` off → bytes returned unchecked.
  - Not wired into the node app yet (that is S3).
- Files created/changed: `backend/vault/node/soum_storage.py`, `backend/vault/tests/test_soum_storage.py`
- Endpoints / functions / components exposed (node-internal; only my node app calls these):
  - `Storage(root, node_id, safety: Callable[[], SafetyCfg])`
  - `await startup() -> int`: tmp files discarded, the number that goes into `RegisterRequest.discarded_on_startup`.
  - `await put(header: FragmentHeader, payload: bytes) -> Entry`
  - `await get(fid) -> (FragmentHeader, bytes)`: raises `NotFound`, or `Corrupt` after moving the file to `quarantine/`.
  - `await verify(fid) -> bool`: always checks; used by the scrubber.
  - `await delete(fid) -> bool`, `await quarantine(fid) -> bool`, `has(fid)`, `entry(fid)`.
  - `inventory() -> list[InventoryItem]`, `scrub_order(recent_first_s) -> list[fid]`, `disk_used()`, `count()`.
  - `await read_node_json() -> dict` (`{}` if missing), `await write_node_json({"epoch", "lease_expiry"})`: always atomic.
  - Helpers `encode_blk` / `decode_blk`.
- How to run / test it: `python -m pytest -q backend/vault/tests/test_soum_storage.py` (14 tests):
  - Format and round trip; no tmp left after success.
  - Startup discards and counts `.part` files.
  - Flipped byte → `Corrupt` + quarantined.
  - Naive mode: unchecked read and direct write.
  - Index rebuilt after "restart"; a junk `.blk` is quarantined at startup.
  - Delete; file removed on disk → `NotFound`; concurrent same-fid puts; scrub order; `node.json`.
  - Full suite: 96 passed.
- Known issues / TODO: S3 wires this into `node/soum_app.py` (fragment endpoints, fencing, epochs).
- Anything other teammates must know or do: **Jaiveer (reconciler seam, S8)**: `Inventory.fragments[].sha256` is the fragment header's expected hash, not a fresh re-hash of the bytes; re-hashing the whole disk every 30 s would be too slow. A damaged file is caught by read or scrub, quarantined, and so drops out of the next inventory, which surfaces as "row ok, file absent" → `missing` → repair.

## [Hour 3] Answers to my open questions (recorded, no code change)
- What was done: recorded Anushka's and Jaiveer's answers; they apply from S3/S4 on.
  1. **Fencing (Anushka):** `fenced = safety.fencing and (lease_expiry is None or time.time() > lease_expiry)`.
     - A node that has never had a lease refuses PUT/DELETE (`503 fenced`) until its first register or heartbeat reply. It still serves verified reads.
     - An unexpired lease saved in `node.json` is honoured after a quick restart.
     - Naive mode skips the check.
  2. **`GET /v1/config` (Jaiveer):** flat `VaultConfig` JSON plus top-level `config_version` and `mode`. Pop both, then `VaultConfig.model_validate(body)`. Re-fetch only when a heartbeat reply's `config_version` is newer than the one held.
  3. **Topology (Jaiveer):** nodes poll `GET /v1/cluster` every 1 s (relay allowed) and feed `update_topology()`. On any error, including 404 until J6 (~H8), they keep the last topology.
- Files created/changed: `updates/soum_update.md`
- Endpoints / functions / components exposed: none.
- How to run / test it: n/a.
- Known issues / TODO: none.
- Anything other teammates must know or do: **Gateway behaviour**: a PUT to a node that has just restarted can get `503 fenced` for under a second; the gateway treats that as a failed fragment and uses a spare.

## [Hour 4] S3 Node API
- What was done: replaced the node stub with the real fragment API (ARCHITECTURE §7.3) on top of S2 storage.
  - Epoch and lease fencing uses Anushka's rule: never leased or lease expired → fenced. Fenced → PUT/DELETE refused, verified GETs still served. Naive mode skips it.
  - Checksum check on receive; disk-full check (chaos flag, or `disk_used + size > node_capacity_bytes`).
  - A damaged copy on read → `409 corrupt` + quarantine + a background `FragmentReport` to metadata (`context: read`).
  - Epoch and lease are loaded from `node.json` at startup.
- Files created/changed: `backend/vault/node/soum_app.py`, `backend/vault/node/soum_storage.py` (+ `encode_meta` / `decode_meta`), `backend/vault/tests/test_soum_node_api.py`
- Endpoints / functions / components exposed (n1–n6 on :7101–7106):
  - `PUT /v1/fragments/{fid}`: bytes + `X-Vault-Meta` (base64url JSON `FragmentHeader`, unpadded; padding accepted), `X-Vault-Sha256`, `X-Vault-Epoch` → `201 FragmentPutResult`.
    - Errors: `400 bad_request` (missing/unreadable meta, or meta fid ≠ path fid) · `400 checksum_mismatch` · `409 stale_epoch` (wrong or missing epoch) · `503 fenced` · `507 disk_full`.
  - `GET /v1/fragments/{fid}` → `200` bytes + `X-Vault-Sha256` + `X-Vault-Meta` · `404 not_found` · `409 corrupt`.
  - `HEAD /v1/fragments/{fid}` → `200` (same headers) · `404`.
  - `DELETE /v1/fragments/{fid}` (`X-Vault-Epoch`) → `204`, also when the fragment is already gone (trims are idempotent) · `409 stale_epoch` · `503 fenced`.
  - `GET /v1/ping` → `Ping` with the real epoch · `GET /v1/health` → `NodeHealth`.
  - `POST /v1/scrub` → `202` (one full pass in the background) · `GET /v1/scrub` → `ScrubStatus`. `scanned` and `corrupt_found` are per pass.
  - Internal, for S4/S6: `app.state.node` (`Node`): `save_state(epoch, lease_expiry)`, `fenced()`, `report(fid, problem, context)`, `trigger_scrub()`, `disk_full`, `safety`, `discarded`.
- How to run / test it:
  - `python -m pytest -q backend/vault/tests/test_soum_node_api.py` (13 tests); full suite 109 passed.
  - Live: with `vault up` and a lease seeded in `data/n1/node.json`, PUT 200 KB → 201; GET → bytes match; flip a byte in the `.blk` → `409 corrupt`, file moved to `quarantine/`. PUT to n2 (no lease) → `503 fenced`.
- Known issues / TODO:
  - Until S4 heartbeats run against Jaiveer's J4, nodes never get a lease, so **every node returns `503 fenced` on PUT under plain `vault up`**. This is correct behaviour; reads work.
  - The corrupt report gets a 404 until J7 adds `/v1/reports/fragment` (logged, harmless).
- Anything other teammates must know or do:
  - **Jaiveer (repair, J7):** send `X-Vault-Epoch` with every `DELETE`. A node answers `503 fenced` until its first heartbeat reply grants a lease.
  - **Anushka:** `/_chaos/corrupt` and `/_chaos/disk_full` still come in S6.

## [Hour 5] S4 Node background loops + pull (seam I3 ready for Jaiveer's detector)
- What was done: nodes now register, heartbeat, hold leases, ping everyone, repair by pull, and scrub continuously. Tested live against Jaiveer's J4/J5 metadata.
  - **Heartbeat** (`soum_heartbeat.py`):
    - Register on startup with `node_identity()` and `discarded_on_startup` (sent once), then push inventory right away (REJOINING → ALIVE), then every 500 ms.
    - The lease runs from the moment the request is **sent**, and is persisted in `node.json`.
    - A `DEAD` reply never extends the lease; the node re-registers in the same tick.
    - Config is re-read after every (re-)register and whenever a reply's `config_version` **differs** (not only when it's newer), because a restarted metadata could count again from 0. Applies `safety` and `rpc.relay_enabled`.
    - Inventory every 30 s, staggered (n1 at 0 s, n2 at 5 s, …).
    - Logs only state changes (metadata reachable/unreachable, fenced/unfenced).
  - **Pinger** (`soum_pinger.py`, reusable by the gateway in S7):
    - Every 1 s, direct only, 500 ms timeout. Nodes get `/v1/ping`; meta and gw get `/_vault/health` (metadata has no `/v1/ping`). Any HTTP answer = reachable.
    - The row goes into the heartbeat's `reach`.
    - Also `GET meta /v1/cluster` each tick → `rpc.update_topology(...)`. 404 (until J6) or errors keep the last topology.
  - **Pull** (`soum_pull.py`, `POST /v1/fragments/{fid}/pull`):
    - `copy`: sources in order; the first whose bytes hash to `expected_sha256` wins.
    - `ec_rebuild`: fetch the other fragments in parallel, verify each against the sha in its own `X-Vault-Meta`, rebuild from k, check against `expected_sha256`.
    - A source that returns bad bytes is reported as `corrupt` (`context: repair`, `node_id` = that source).
    - Fenced → 503. Already stored and intact → 201 with `source_used: "local"` (safe retry). `X-Vault-Epoch` is optional here and checked if sent.
  - **Scrubber** (`soum_scrubber.py`):
    - Continuous passes paced at `scrub.rate_mbps`, recent files first, at least 10 s between pass starts.
    - `POST /v1/scrub` wakes it for an unpaced pass.
    - Fully off in Naive mode (`safety.scrub` false), even on request, so the Vault vs Naive comparison stays fair.
- Files created/changed: `node/soum_heartbeat.py`, `node/soum_pinger.py`, `node/soum_pull.py`, `node/soum_scrubber.py`, `node/soum_app.py` (loops in the lifespan, pull route, `create_app(cfg, pid, start_loops=True)`), `tests/test_soum_node_loops.py` (new, 13 tests), `tests/test_soum_node_api.py` (uses `start_loops=False`)
- Endpoints / functions / components exposed:
  - `POST /v1/fragments/{fid}/pull` `PullRequest` → `201 PullResult` (`source_used`: node id, `"ec:n1,n3,…"` or `"local"`) · `400 bad_request` · `424 no_valid_source` (detail `tried`) · `503 fenced` · `507 disk_full`
  - Node → meta calls: `POST /v1/nodes/register`, `POST /v1/nodes/{id}/heartbeat` (every 500 ms), `POST /v1/nodes/{id}/inventory`, `GET /v1/config`, `GET /v1/cluster`, `POST /v1/reports/fragment`.
  - `create_app(cfg, pid, start_loops=True)`: the extra keyword is for unit tests; the `vault.node` shim is unchanged.
  - `Pinger(pid, cfg, on_row)`: the gateway will reuse it in S7.
- How to run / test it:
  - `python -m pytest -q backend/vault/tests/test_soum_node_loops.py` (13 tests); all Soum tests 50 passed; full suite 164 passed.
  - Live with `python -m vault up`:
    - All 6 nodes register (6× `node.joined` in `/v1/events`), unfenced, lease ≈ 5 s ahead.
    - PUT on n1 → pull onto n2 → 201 in 38 ms, bytes match.
    - Kill meta → nodes fence ~4.5 s later (`DELETE` → 503). Start meta → `DEAD` reply → re-register → unfenced within ~1 s.
    - `POST /v1/mode` naive → nodes apply config within ~1 s.
- Known issues / TODO:
  - While metadata is down, heartbeats fall back to relaying through a peer, and peers answer 404 until S6 adds `/v1/relay`. So relayed heartbeats (the cut-cable demo) start working in S6.
  - Topology stays empty until J6's `/v1/cluster`; rpc still tries relays in order without it.
- Anything other teammates must know or do:
  - **Jaiveer (J6 detector):** real heartbeats arrive every 500 ms with `reach` (every node + meta), `scrub`, `fenced`, `disk_used` and `fragments`. `tests/jaiveer/jaiveer_fake_nodes.py` is no longer needed with `vault up`.
  - **Jaiveer (J7 repair):** pull is ready; see the endpoint above. `source_used` tells you which source was used. A bad source is already reported by the node.
  - **Jaiveer:** metadata restarts are handled via the `DEAD` reply; nothing else is needed.
  - **Note (S4 run):** the full test suite now runs slower on this machine (~65 s, mostly `test_jaiveer_meta` / `test_jaiveer_commit` setup, ~1.3 s each). That is the same with and without S4, so it looks like disk or antivirus load on the SQLite tests, not a code change.

## [Hour 6] S5 Gateway PUT/GET (M2 first light ✅)
- What was done: the public object API on :7080, against Jaiveer's real J5 metadata.
  - **Streaming PUT:**
    - `Content-Length` is required (411). The body is cut into the plan's chunks as it streams, with at most `gateway.chunks_in_flight` (2) chunk uploads in flight, so memory stays at a few MiB for any file size. The whole-object SHA-256 becomes the ETag.
    - Per chunk: `ec_encode` for EC, then fragment PUTs ×n in parallel (`X-Vault-Meta` / `X-Vault-Sha256` / `X-Vault-Epoch` = the target's epoch from the plan).
    - A failed target moves its `frag_idx` to the next unused spare.
    - Once W distinct `frag_idx` are acked, stragglers get 2.5 s more, then count as not ok. On Windows a dead local port is refused only after ~2.0 s (measured), so a 1 s grace skipped the spare.
    - Every attempt is reported in the commit (`ok` true/false).
  - **Quorum failure** (< W after spares, per Jaiveer): stop storing, keep reading the rest of the body (hash only) so every planned chunk is reported, then POST the commit anyway. Metadata answers 409, aborts and emits `write.quorum_failed`; the client gets **`503 quorum_not_met`**. `/abort` is only for a client that disconnects or a body of the wrong length (`400 bad_length`).
  - **Commit** is retried twice on a network error (it's idempotent). A metadata error is passed through (404/412/400); other statuses become 503 with the metadata's code.
  - **Verified GET:** manifest → chunk 0 is fetched and verified **before** any header is sent (else `503 unavailable`). The rest streams with one chunk prefetched, and `Content-Length` is set so a later failure shows up as a truncated body.
    - Replication: holders direct-before-relay, not-slow first; each fragment is checked against `chunk.sha256`. A network error / 404 / 409 / bad checksum → report (read repair) and try the next holder. `read_failover` off → first holder only.
    - EC: data fragments 0..k-1 in parallel; all verified → concatenate. Otherwise parity until k verified → `ec_decode`. The chunk checksum is verified at the end.
  - Safety switches: the gateway polls `GET /v1/config` every 1 s (`quorum_writes` off → W = 1; `read_failover`; `verify_on_read`; `relay`).
- Files created/changed: `gateway/soum_app.py`, `soum_put.py`, `soum_get.py`, `soum_meta_client.py` (new: metadata calls with error pass-through, `Gateway` state, config poller), `soum_routing.py` (holder order; RTT in S7), `soum_stats.py` (counters; report loop in S7), `tests/test_soum_gateway.py` (new, 9 tests)
- Endpoints / functions / components exposed (:7080, ARCHITECTURE §7.1):
  - `PUT /{bucket}` `{"policy":"rep3"|"rep2"|"ec42"}` (empty body = rep3) → `200 {"bucket","policy"}` · `409 bucket_exists`
  - `GET /{bucket}?prefix=&limit=1000` → `ObjectList`
  - `PUT /{bucket}/{key:path}` bytes → `200 PutResult`, plus headers `ETag`, `X-Vault-Seq`, `X-Vault-Commit-Seq`.
    - Errors: `411 length_required` · `400 bad_length` · `404 no_bucket` · `412` · `503 quorum_not_met` / `not_enough_machines` / `metadata_unavailable`.
  - `GET /{bucket}/{key:path}` → bytes + `ETag`, `X-Vault-Seq`, `X-Vault-Commit-Seq`, `X-Vault-Read-Path` (chunk 0: `direct` | `relay:<via>` | `failover:<node that served>` | `ec-decode`), `Content-Length` · `404 not_found` · `503 unavailable`
  - `HEAD /{bucket}/{key:path}` → the same headers, no body · `404`
  - `DELETE /{bucket}/{key:path}` → `DeleteResult` (`{"deleted":false,"commit_seq":null}` for a missing key)
- How to run / test it:
  - `python -m pytest -q backend/vault/tests/test_soum_gateway.py` (9 tests: real node apps + a fake J5 metadata in process). Full suite: 173 passed.
  - Live with `python -m vault up` (no fake meta needed):
    ```
    curl.exe -X PUT localhost:7080/clinic -H "Content-Type: application/json" -d '{\"policy\":\"rep3\"}'
    curl.exe -T big50mb.bin localhost:7080/clinic/big.bin        # 50 MB: 3.1 s, 150 fragments acked
    curl.exe localhost:7080/clinic/big.bin -o down.bin           # 1.7 s, sha256 matches the upload
    ```
  - Also checked live:
    - Kill n2 → PUT still succeeds with 12/12 fragments (spares), and GETs of files that had copies on n2 still return the right bytes.
    - An `archive` (ec42) bucket round-trips, and still reads correctly with n5 killed.
    - List works; DELETE then GET → 404.
- Known issues / TODO:
  - S7: holder order by RTT and node state, the per-second stats/reach report to `/v1/participants/gw/report`.
  - S6: relay through peers. With `X-Vault-Read-Path: relay:<via>` the read works once nodes expose `/v1/relay`.
  - A write to a machine that just crashed waits ~2 s (Windows refused-connect time) before its spare is used, until metadata stops placing on it (J6).
- Anything other teammates must know or do:
  - **Urooz (U2 seed, U3/U5 Oracle):** the gateway is live, so you can drop your fake gateway.
    - PUTs return `X-Vault-Commit-Seq` / `X-Vault-Seq` headers and the same values in the JSON.
    - GETs return them as headers.
    - A 503 after the body was sent is an *unknown* outcome for the ledger (the commit may or may not have happened).
  - **Anushka:** `vault reset`'s seed and the dashboard's upload can use `PUT :7080/{bucket}/{key}` now. Buckets must exist first (reset creates them).
  - **Jaiveer:** reads report `unreachable` / `missing` / `corrupt` to `/v1/reports/fragment` with `context: read` (404 from you until J7, harmless).
  - **For S6, heads-up to Anushka:** on Windows a request to a dead machine takes ~2 s to be refused. If a relay answered `599` for a failed forward, rpc.py would try every other relay candidate (≈ 12 s per request). My relay will answer **`502 target_unreachable`** instead, so the caller stops after one relay. Tell me if you'd rather handle it in rpc.py.
