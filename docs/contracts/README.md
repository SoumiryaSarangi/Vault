# Contracts (frozen)

**Owner:** Anushka. Only Anushka changes these (TEAM_PROTOCOL §1.4, §4). Need a change? Write a handoff to Anushka.

The prose contract is `docs/ARCHITECTURE.md` §7 (API) and §8 (events, snapshot, metrics). The **executable** contract is:

| File | What |
|---|---|
| `backend/vault/common/models.py` | Every payload that crosses a process boundary, as pydantic v2 models |
| `web/lib/contracts.ts` | TypeScript mirror (same field names, snake_case kept) |
| `backend/vault/common/brain_api.py` | `BrainContext`: how brain modules inside the metadata process reach the DB, membership, jobs, events |
| `backend/vault/common/service.py` | Every service exposes `create_app(cfg: VaultConfig, pid: str) -> FastAPI` built with `make_app()` |
| `backend/vault/common/rpc.py` | The only HTTP client: `get_rpc().request(target, method, path, ...)`; `NetworkError` on 599/timeouts |
| `backend/vault/common/events.py` | `emit(type, subject, data, **fields)`; human/technical copy templates (DESIGN §6.3) |
| `web/fixtures/*.json` | Example `Snapshot`, `Event`s, `Metrics`, `FateReport`, validated against models.py |

If this folder, ARCHITECTURE and the code disagree: **stop and ask Anushka** (ARCHITECTURE header).

## Decisions made while writing models.py (read these)

1. `Manifest.policy` is the full `Policy` object, not just the name, because the gateway needs `k`/`n` to decode EC.
2. `RegisterRequest.discarded_on_startup` (int, default 0): the node reports how many `tmp/*.part` files it removed; metadata emits `node.startup_discarded`.
3. `GatewayStats.failover_reads` (node_id → count): the gateway reports reads served elsewhere; metadata emits the aggregated `read.failover`.
4. `ServiceHealth` is the body of `GET /_vault/health` on every service (`make_app` adds it).
5. `ChaosState` is the body of `GET /_chaos` and every `/_chaos/*` POST (`netsim.py`); nodes add `node_faults` via `ns.add_state(...)`.
6. `RunSummary` (rows of `GET /runs`) = `RunStatus` without `samples`/`timeline`, plus `chaos_script`.
7. Every model ignores unknown fields, so adding an optional field later doesn't break older consumers.

## Headers (ARCHITECTURE §4.1–4.7, §7.3)

| Header | Set by | Meaning |
|---|---|---|
| `X-Vault-From` | rpc.py (every request) | sender pid; netsim judges `blocked_in` by it |
| `X-Vault-Hop` | rpc.py | `0` direct, `1` relayed |
| `X-Vault-Origin`, `X-Vault-Via` | rpc.forward (relay) | original sender; relay node. Metadata shows `route: relay:<via>` |
| `X-Vault-Epoch` | gateway / repair → node | node rejects a mismatch with `409 stale_epoch` |
| `X-Vault-Meta` | gateway / pull → node | base64url JSON `FragmentHeader` |
| `X-Vault-Sha256` | both directions | fragment SHA-256 |
| `ETag`, `X-Vault-Seq`, `X-Vault-Commit-Seq`, `X-Vault-Read-Path` | gateway → client | exposed to the browser via CORS |
