# Urooz: updates

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

## [H2] U1 · Ledger + checker (pure)

- **What was done:** Implemented the append-only JSONL ledger (`Ledger` class, 100 ms flush+fsync) and the pure checker (`check()`) with all five violation rules: `lost`, `damaged`, `resurrected`, `stale_read`, `phantom_read`. Also implemented `under_protected` counting from `/v1/inspect/objects`. Full test suite with one hand-written ledger per rule and no-false-alarm coverage for `unknown` writes.

- **Files created/changed:**
  - `backend/vault/oracle/urooz_ledger.py` — `Ledger` class + `load_ledger()` helper
  - `backend/vault/oracle/urooz_checker.py` — pure `check(entries, final_reads, inspect) -> (RunViolations, RunHealth, list[ViolationSample])`
  - `backend/vault/tests/test_urooz_checker.py` — 17 hand-written test cases

- **Endpoints / functions exposed:**
  - `check(entries: list[LedgerEntry], final_reads: dict[key, (sha256|None, commit_seq|None)], inspect: list[InspectObject]) -> (RunViolations, RunHealth, list[ViolationSample])`
  - `Ledger(run_dir: Path)` — `.open()`, `.append(entry)`, `.entries()`, `.close()`
  - `load_ledger(run_dir: Path) -> list[LedgerEntry]`

- **How to run / test it:**
  ```
  cd backend
  python -m pytest vault/tests/test_urooz_checker.py -v
  ```

- **Known issues / TODO:** U5 will wire `Ledger` + `check()` + `verify_all_keys()` into the run lifecycle.

- **Anything other teammates must know:** The checker is pure; it has no side effects and no network calls.

## [H2] U2 · Demo seed data (stub, needs gateway)

- **What was done:** Implemented `seed(rpc, bucket, count, seed) -> int`. Generates 200 synthetic clinic filenames (`xray-NNNN.png`, `lab-report-NNNN.pdf`, etc.) with seeded-random content (20 KB–3 MB, weighted). Uploads 8 in parallel via `rpc.request("gw", "PUT", ...)`.

- **Files created/changed:**
  - `backend/vault/supervisor/urooz_seed.py`

- **Endpoints / functions exposed:**
  - `async def seed(rpc: Rpc, bucket: str = "clinic", count: int = 200, seed: int = 42) -> int`

- **How to run / test it:** Will work once Soum's gateway (S5) is merged. Called via `POST :7070/demo/seed` (seam I9, Anushka's reset calls it).

- **Known issues / TODO:** Timing target ≤ 20 s; actual content size is modest (70% files are 20–500 KB). Will benchmark against real gateway at H7.

## [H2] U3 · Workload + chaos scripts (pure scripts, workload ready)

- **What was done:**
  - `urooz_scripts.py`: pure `steps(name, seed, nodes) -> list[ChaosStep]` for `standard` (9 steps, ≈47–49 fault units) and `heavy` (3 × standard cycles with 80 corruptions per burst, `restart_down` at end of each cycle, ≈500 fault units). `fault_units(steps) -> int`.
  - `urooz_workload.py`: async `run_workload(rpc, ledger, keyspace, clients, duration_s, seed, stop_event, counters) -> dict` with 8 client loops, 50% PUT/40% GET/10% DELETE, seeded RNG, outcome ok/fail/unknown per §5. `verify_all_keys(rpc, keyspace) -> dict[key, (sha256|None, commit_seq|None)]` for the verifying phase.

- **Files created/changed:**
  - `backend/vault/supervisor/urooz_scripts.py`
  - `backend/vault/oracle/urooz_workload.py`

- **Endpoints / functions exposed:**
  - `steps("standard"|"heavy", seed, nodes) -> list[ChaosStep]`  ← seam I8 with Anushka
  - `fault_units(steps) -> int`
  - `run_workload(rpc, ledger, ...) -> dict`
  - `verify_all_keys(rpc, keyspace) -> dict`

- **How to run / test it:** `POST :7070/chaos/script {"name":"standard","seed":42}` will call `steps()` once Anushka's runner is wired (seam I8). Workload needs Soum's gateway (S5); `urooz_fake_gateway.py` (U3 remaining) provides an in-memory stand-in.

- **Known issues / TODO:** `urooz_fake_gateway.py` (in-memory FastAPI for local dev) not yet written — will add after H4 merge. U5 (runs API) will import `run_workload`.
