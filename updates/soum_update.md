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
