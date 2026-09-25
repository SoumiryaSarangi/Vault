"""Owner: Jaiveer (J2). Cover: deterministic walk; distinct nodes; default labels → rep3 picks a fully
independent triple ({n1,n3,n5} or {n2,n4,n6}); constraint relaxation when impossible; exclude/DEAD/full
skipped; adding n7 moves ≈1/N of chunks' preferred sets; fate_keys=[] = pure ring order."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vault.common.config import load_config
from vault.common.hashing import placement_hash
from vault.common.jaiveer_placement import (FATE_KEYS, Ring, active_fate_keys, choose_additional, place)
from vault.common.models import NodeState, NodeView

REPO = Path(__file__).resolve().parents[3]
CHUNKS = [f"v_{i:020x}_{j:05d}" for i in range(400) for j in range(5)]      # 2000 chunk ids
INDEPENDENT = [{"n1", "n3", "n5"}, {"n2", "n4", "n6"}]


def cluster(**overrides) -> dict[str, NodeView]:
    """n1–n6 from vault.yaml, all ALIVE. overrides: n3={"state": "DEAD"} etc."""
    cfg = load_config(str(REPO / "vault.yaml"))
    out = {}
    for i, (nid, nc) in enumerate(cfg.nodes.items()):
        v = NodeView(id=nid, display_name=nc.display_name, addr=cfg.addr(nid), state=NodeState.ALIVE,
                     labels=dict(nc.labels), capacity=cfg.cluster.node_capacity_bytes)
        if nid in overrides:
            v = v.model_copy(update=overrides[nid])
        out[nid] = v
    return out


def ring_for(nodes: dict[str, NodeView]) -> Ring:
    r = Ring(128)
    r.rebuild(list(nodes.values()))
    return r


def shares_any(nodes, a, b, keys):
    return any(nodes[a].labels.get(k) == nodes[b].labels.get(k) for k in keys)


# ── ring ──

def test_walk_yields_every_node_once():
    nodes = cluster()
    r = ring_for(nodes)
    for cid in CHUNKS[:50]:
        w = list(r.walk(placement_hash(cid)))
        assert sorted(w) == sorted(nodes)


def test_walk_empty_ring():
    assert list(Ring().walk(123)) == []


def test_retired_not_on_ring():
    nodes = cluster(n6={"state": NodeState.RETIRED})
    r = ring_for(nodes)
    assert "n6" not in list(r.walk(0))


def test_capacity_weight_scales_vnodes():
    nodes = cluster()
    nodes["n1"] = nodes["n1"].model_copy(update={"capacity": 4 * 1024 ** 3})   # 2× weight
    r = ring_for(nodes)
    assert r._owners.count("n1") == 256
    assert r._owners.count("n2") == 128
    firsts = [place(r, nodes, c, 1, fate_keys=[])[0] for c in CHUNKS]
    share = firsts.count("n1") / len(firsts)
    assert 0.20 < share < 0.38          # ideal 2/7 ≈ 0.286


def test_deterministic_across_processes():
    nodes = cluster()
    r = ring_for(nodes)
    here = [place(r, nodes, c, 3) for c in CHUNKS[:200]]
    code = (
        "import json,sys; sys.path.insert(0, 'backend');"
        "from vault.common.config import load_config;"
        "from vault.common.models import NodeView, NodeState;"
        "from vault.common.jaiveer_placement import Ring, place;"
        "cfg = load_config('vault.yaml');"
        "nodes = {i: NodeView(id=i, display_name=c.display_name, addr='x', state=NodeState.ALIVE,"
        " labels=dict(c.labels), capacity=cfg.cluster.node_capacity_bytes) for i, c in cfg.nodes.items()};"
        "r = Ring(128); r.rebuild(list(nodes.values()));"
        f"ids = {CHUNKS[:200]!r};"
        "print(json.dumps([place(r, nodes, c, 3) for c in ids]))"
    )
    env = dict(os.environ, PYTHONHASHSEED="12345")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True,
                         check=True).stdout
    assert json.loads(out) == here


# ── fate keys ──

def test_version_is_cluster_wide():
    assert active_fate_keys(list(cluster().values())) == ["power", "switch", "disk_batch"]


def test_active_fate_keys_ignores_retired_and_missing_labels():
    nodes = cluster(n6={"labels": {"power": "C", "switch": "S1", "disk_batch": "D3", "version": "2.0"}})
    assert "version" in active_fate_keys(list(nodes.values()))
    nodes = cluster(n6={"state": NodeState.RETIRED, "labels": {"version": "2.0"}})
    assert "version" not in active_fate_keys(list(nodes.values()))
    nodes = cluster(n6={"labels": {"power": "C"}})       # n6 has no version label → not cluster-wide
    assert "version" in active_fate_keys(list(nodes.values()))
    assert active_fate_keys([]) == FATE_KEYS


# ── place ──

def test_rep3_default_labels_fully_independent():
    nodes = cluster()
    r = ring_for(nodes)
    for c in CHUNKS:
        got = place(r, nodes, c, 3)
        assert len(got) == 3 and len(set(got)) == 3
        assert set(got) in INDEPENDENT


def test_both_independent_triples_are_used():
    nodes = cluster()
    r = ring_for(nodes)
    triples = {frozenset(place(r, nodes, c, 3)) for c in CHUNKS}
    assert triples == {frozenset(t) for t in INDEPENDENT}


def test_distinct_nodes_for_six():
    nodes = cluster()
    r = ring_for(nodes)
    for c in CHUNKS[:200]:
        got = place(r, nodes, c, 6)
        assert sorted(got) == sorted(nodes)


def test_first_choice_is_ring_order():
    nodes = cluster()
    r = ring_for(nodes)
    for c in CHUNKS[:200]:
        assert place(r, nodes, c, 3)[0] == next(r.walk(placement_hash(c)))


def test_relaxes_least_important_key_first():
    # n5 dead: no fully independent triple exists. Best available: different power for all three.
    nodes = cluster(n5={"state": NodeState.DEAD})
    r = ring_for(nodes)
    for c in CHUNKS:
        got = place(r, nodes, c, 3)
        assert len(set(got)) == 3 and "n5" not in got
        powers = {nodes[n].labels["power"] for n in got}
        assert len(powers) == 3                      # power (most important) is never relaxed needlessly


def test_relaxes_to_pure_ring_when_nothing_else_fits():
    # Only power A and B alive: rep3 must still get 3 distinct machines.
    nodes = cluster(n5={"state": NodeState.DEAD}, n6={"state": NodeState.DOWN})
    r = ring_for(nodes)
    for c in CHUNKS[:300]:
        got = place(r, nodes, c, 3)
        assert len(set(got)) == 3 and not {"n5", "n6"} & set(got)


def test_no_relaxation_when_not_needed():
    nodes = cluster()
    r = ring_for(nodes)
    for c in CHUNKS[:300]:
        got = place(r, nodes, c, 2)
        assert not shares_any(nodes, got[0], got[1], ["power", "switch", "disk_batch"])


@pytest.mark.parametrize("override", [
    {"state": NodeState.DEAD}, {"state": NodeState.DOWN}, {"state": NodeState.SUSPECT},
    {"state": NodeState.JOINING}, {"fenced": True}, {"faults": ["disk_full"]},
    {"disk_used": 2 * 1024 ** 3},
])
def test_ineligible_never_chosen(override):
    nodes = cluster(n3=override)
    r = ring_for(nodes)
    for c in CHUNKS[:500]:
        assert "n3" not in place(r, nodes, c, 5)


def test_exclude_never_chosen():
    nodes = cluster()
    r = ring_for(nodes)
    for c in CHUNKS[:500]:
        got = place(r, nodes, c, 4, exclude={"n1", "n2"})
        assert not {"n1", "n2"} & set(got) and len(got) == 4


def test_returns_fewer_when_not_enough_nodes():
    nodes = cluster(n4={"state": NodeState.DEAD}, n5={"state": NodeState.DEAD}, n6={"state": NodeState.DEAD})
    r = ring_for(nodes)
    assert sorted(place(r, nodes, CHUNKS[0], 6)) == ["n1", "n2", "n3"]


def test_holders_first_and_respected():
    nodes = cluster()
    r = ring_for(nodes)
    for c in CHUNKS[:300]:
        got = place(r, nodes, c, 3, holders=["n1"])
        assert got[0] == "n1" and set(got) == {"n1", "n3", "n5"}
    assert place(r, nodes, CHUNKS[0], 2, holders=["n4", "n1", "n2"]) == ["n4", "n1"]


def test_holder_that_is_dead_still_counts_as_chosen():
    # A DEAD holder stays in the list (caller decides what's durable); new picks avoid its domains.
    nodes = cluster(n1={"state": NodeState.DEAD})
    r = ring_for(nodes)
    got = place(r, nodes, CHUNKS[0], 3, holders=["n1"])
    assert got[0] == "n1" and set(got) == {"n1", "n3", "n5"}


def test_choose_additional():
    nodes = cluster()
    r = ring_for(nodes)
    for c in CHUNKS[:300]:
        assert choose_additional(r, nodes, c, ["n1", "n3"]) == "n5"
        assert choose_additional(r, nodes, c, ["n2", "n4"]) == "n6"
    # repair after n5 died: third copy goes to a node with a new power supply (C → n6)
    nodes = cluster(n5={"state": NodeState.DEAD})
    r = ring_for(nodes)
    for c in CHUNKS[:300]:
        assert choose_additional(r, nodes, c, ["n1", "n3"], exclude={"n5"}) == "n6"


def test_choose_additional_none_when_no_candidate():
    nodes = cluster(n4={"state": NodeState.DEAD}, n5={"state": NodeState.DEAD}, n6={"state": NodeState.DEAD})
    r = ring_for(nodes)
    assert choose_additional(r, nodes, CHUNKS[0], ["n1", "n2", "n3"]) is None


def test_fate_keys_empty_is_pure_ring_order():
    nodes = cluster()
    r = ring_for(nodes)
    for c in CHUNKS[:300]:
        assert place(r, nodes, c, 3, fate_keys=[]) == list(r.walk(placement_hash(c)))[:3]
    naive = {frozenset(place(r, nodes, c, 3, fate_keys=[])) for c in CHUNKS}
    assert len(naive) > 2          # naive mode happily co-locates on shared power


def test_adding_n7_moves_about_one_seventh():
    nodes = cluster()
    r6 = ring_for(nodes)
    before = {c: place(r6, nodes, c, 1)[0] for c in CHUNKS}
    nodes7 = dict(nodes)
    nodes7["n7"] = NodeView(id="n7", display_name="New PC", addr="x", state=NodeState.ALIVE,
                            labels={"power": "A", "switch": "S2", "disk_batch": "D3", "version": "1.0"},
                            capacity=2 * 1024 ** 3)
    r7 = ring_for(nodes7)
    after = {c: place(r7, nodes7, c, 1)[0] for c in CHUNKS}
    moved = [c for c in CHUNKS if before[c] != after[c]]
    assert all(after[c] == "n7" for c in moved)           # only moves onto the new node
    frac = len(moved) / len(CHUNKS)
    assert 0.09 < frac < 0.20, frac                        # ideal 1/7 ≈ 0.143
