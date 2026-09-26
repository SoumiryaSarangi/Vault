# Handoff: soum -> jaiveer
Date/Time: 2026-09-26
Status: OPEN (FYI; please review)
Topic: LAN mode: metadata now dials every node at its registered address

What I need changed / added: nothing; FYI. With Anushka's approval (relayed by Soum), I made a two-line change in
your files for the 4-laptop demo (`docs/soum_lan_demo.md`).

Exact files or endpoints involved:
- `backend/vault/metadata/jaiveer_cluster.py`, `POST /v1/nodes/register`:
  - before: `brain.rpc.set_addr(req.node_id, req.addr)` ran only for ids not in `vault.yaml`;
  - now: it runs for every registration with an addr.
  - Why: a node on another laptop is in the config but lives at another IP.
- `backend/vault/metadata/jaiveer_app.py`, startup: the same for the persisted `addr` of every known node (it used to cover only non-yaml ones).
- Related, in my own files: `Inventory` has a new optional `sent_at` (node clock), which my reconciler uses for orphan age. You don't need to do anything with it.

Expected input / output (with example): a node registers `addr: "192.168.1.102:7102"`. Metadata's rpc then calls it there, not at `cluster.host:7102`. Single-laptop mode is unchanged: the registered address equals the config one.

Why it is needed (which feature depends on it): the detector's pings and repair calls reach machines on other laptops.

Deadline (hour of hackathon): none.

What I will use as a stub until then: n/a (merged on `soum/data-plane`; 269 tests pass).

Gated? no (your files, but no contract change; Anushka approved the LAN work).
