# Handoff: soum -> jaiveer
Date/Time: Sat 26 Sep 2026, ~H10
Status: OPEN
Topic: decommission route + RETIRED transition, so my drain (S9) can run

What I need changed / added:
1. `POST /v1/nodes/{id}/decommission` → set the node to **DRAINING** (persisted, as your membership already
   loads DRAINING/RETIRED from the nodes table) and return its `NodeView` (ARCHITECTURE §7.2). Optionally call
   `soum_rebalancer.on_drain(ctx, id)` right away; if not, my `run(ctx)` loop sees DRAINING within 2 s and
   starts the drain itself.
2. When the node is empty → **RETIRED** (persisted). `BrainContext` has no way for me to change a node's state,
   so please poll `await soum_rebalancer.is_drained(ctx, id)` (True when no ok copy of current data is left on
   it), or react to my `node.drained` event.

Exact files or endpoints involved:
`metadata/jaiveer_cluster.py` (route), `brain/jaiveer_membership.py` (DRAINING/RETIRED). Mine:
`brain/soum_rebalancer.py`: `on_drain(ctx, node_id)`, `is_drained(ctx, node_id) -> bool`, `run(ctx)`.

Expected input / output (with example):
`curl -X POST :7000/v1/nodes/n5/decommission` → `NodeView{state:"DRAINING"}`. My loop then queues one
`move` job per current fragment on n5 (`source_node=n5`, `target_node=None` so your executor picks
`choose_additional`, `reason=drain`, P3). When all have finished and nothing is left, I emit `node.drained`
("Records Room is empty and can be unplugged.") and `is_drained` returns True, at which point you mark it RETIRED.

Why it is needed (which feature depends on it):
F11 "Draining a machine moves everything off it" (PRD §5.1). Not in the 3-minute demo; it's a Q&A item
(MASTER_PLAN §5).

Deadline (hour of hackathon): nice to have before H14 freeze; low priority.

What I will use as a stub until then:
Unit tests with a fake BrainContext that sets DRAINING directly (`tests/test_soum_rebalancer.py`).

Gated? (yes/no, if yes Anushka approved: yes/no): no (your files; the route is already in ARCHITECTURE §7.2)
