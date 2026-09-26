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
8. **Supervisor addition:** `POST /chaos/corrupt` with `CorruptRequest` (`count`, `mode`) → `CorruptResult`. Damages `count` random copies spread over the running nodes (the dashboard's "Damage copies 1 / 10 / 50"). Per-node damage stays `POST /chaos/node/{pid}` with `action: "corrupt"`.
9. **`FaultReport.kind` values** sent by the supervisor to `POST /v1/incidents/fault`: `node_dead` (kill, freeze), `node_start`, `partition` (link cut; `subject` is `"a|b"`), `link_restored`, `corruption` (`subject` is a node id or `"cluster"`), `slow`, `disk_full`, `clear`, and `power_cut` (A3). Jaiveer uses `node_dead` / `partition` / `corruption` / `power_cut` for incident `fault_at`; the others can be ignored.
10. **Chaos event types** (`ExternalEvent.type`): `chaos.kill`, `chaos.start`, `chaos.link_cut`, `chaos.link_restored`, `chaos.slow`, `chaos.freeze`, `chaos.corrupt`, `chaos.disk_full`, `chaos.clear`, `chaos.power_cut`, `chaos.power_restore`, `chaos.add_node`, `chaos.reset`. `data.at` is the injection time. After a full power cut, the `chaos.power_cut` event and its `FaultReport` are sent once metadata is back, with the original `at`.
11. **`ChaosStep`** (models.py) is the shape of one step of a seeded chaos script: `{t, action, params}`. The action list and params are in its docstring. Urooz's `urooz_scripts.steps(name, seed, nodes) -> list[ChaosStep]` produces them; the supervisor's `POST /chaos/script` runs them. **Supervisor addition:** `POST /chaos/script/{id}/stop`.
12. **Added machines (n7+)** aren't in `vault.yaml`. The supervisor passes their name and labels in env vars; a node gets its identity with `vault.common.config.node_identity(cfg, node_id) -> (display_name, labels)`, which works for every node. Their address is `cfg.addr("n7")` (port `node_base + 6`). Metadata must accept `POST /v1/nodes/register` from a node it hasn't seen.
13. **CORS:** every service allows `web.origin` (`http://localhost:3000`) **and** any `http://localhost:<port>` / `http://127.0.0.1:<port>` (`common/service.py` `LOCAL_ORIGINS`), so opening the dashboard via 127.0.0.1 or on a second dev port works. LAN mode adds private network origins (`10.*`, `192.168.*`, `172.16–31.*`). There is no auth in the demo (PRD non-goal), so this is not a security boundary.
14. **LAN mode** (`docs/soum_lan_demo.md`, Soum, approved by Anushka).
    - **Supervisor routes:** `POST /nodes/join` (`JoinRequest` → `JoinResult`), sent by the node agent every 3 s, idempotent; `POST /nodes/{id}/rename` (`RenameRequest` → `Proc`); `GET /cluster/info` (`ClusterInfo`).
    - **Node agent routes** (`node/soum_agent.py`): `GET /agent/status`, `POST /agent/node/start|stop|restart|wipe`, all → `AgentStatus`.
    - **`Proc`** gains `remote`, `host` and `note` ("asleep or off the network").
    - **`Inventory`** gains `sent_at`, the node's clock, used for orphan age.
    - **Config:** `ports.agent` and `cluster.bind_host`. Env vars `VAULT_HUB` and `VAULT_NODE_ADDR`.

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
