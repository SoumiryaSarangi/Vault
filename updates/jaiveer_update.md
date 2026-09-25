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
