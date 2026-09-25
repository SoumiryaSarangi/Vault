"""Owner: Jaiveer (J3). Cover (ARCHITECTURE §4.12): rep3 on {n1,n3,n5} default labels → IFL 3;
relabel n3,n5 power=A → IFL 1 with min_cut [("power","A")]; version is cluster-wide and excluded;
ec42 on 6 nodes → IFL 3 (m+1) and survives any 1 power strip; rep2 → 2; cache hit on same holder set.

NOTE (flagged to Anushka, see updates/jaiveer_update.md): with the DEFAULT labels, ec42 on all 6 machines
is IFL 2 by the §4.12 definition, not 3: power A + power B remove 4 of 6 fragments and k=4 are needed.
It still survives any 1 power strip. IFL 3 needs every machine on its own power/switch/disk batch;
both cases are tested below."""
from itertools import combinations
from pathlib import Path

import pytest

from vault.common.config import load_config
from vault.common.jaiveer_fate import build_domains, ifl, ifl_cache_info, target_ifl
from vault.common.models import NodeState, NodeView, Policy

REPO = Path(__file__).resolve().parents[3]
CFG = load_config(str(REPO / "vault.yaml"))
KEYS = CFG.fate.keys


def nodes(**relabel) -> list[NodeView]:
    """n1–n6 from vault.yaml. relabel: n3={"power": "A"} merges into that node's labels.
    A special key "_state" sets the state."""
    out = []
    for nid, nc in CFG.nodes.items():
        labels = dict(nc.labels)
        state = NodeState.ALIVE
        if nid in relabel:
            upd = dict(relabel[nid])
            state = upd.pop("_state", state)
            labels.update(upd)
        out.append(NodeView(id=nid, display_name=nc.display_name, addr=CFG.addr(nid), state=state, labels=labels))
    return out


def rep(*ids) -> dict[str, set[int]]:
    return {nid: {i} for i, nid in enumerate(ids)}


def ec_all6() -> dict[str, set[int]]:
    return {f"n{i + 1}": {i} for i in range(6)}


def survives(holders, needed, domains, failed) -> bool:
    dead = set().union(*(domains[d] for d in failed)) if failed else set()
    left = set().union(*(f for n, f in holders.items() if n not in dead)) if holders else set()
    return len(left) >= needed


# ── domains ──

def test_build_domains_default_labels():
    d, cw = build_domains(nodes(), KEYS)
    assert cw == [("version", "1.0")]
    assert ("version", "1.0") not in d
    assert d[("power", "A")] == {"n1", "n2"}
    assert d[("switch", "S3")] == {"n4", "n5"}
    assert d[("disk_batch", "D3")] == {"n3", "n6"}
    assert all(d[("node", f"n{i}")] == {f"n{i}"} for i in range(1, 7))
    assert len(d) == 6 + 3 + 3 + 3


def test_version_stops_being_cluster_wide_after_upgrade():
    d, cw = build_domains(nodes(n6={"version": "1.1"}), KEYS)
    assert cw == []
    assert d[("version", "1.0")] == {"n1", "n2", "n3", "n4", "n5"}
    assert d[("version", "1.1")] == {"n6"}


def test_retired_nodes_ignored():
    d, cw = build_domains(nodes(n6={"_state": NodeState.RETIRED, "version": "0.9"}), KEYS)
    assert ("node", "n6") not in d
    assert ("version", "1.0") in cw           # every non-retired node runs 1.0
    assert d[("power", "C")] == {"n5"}


# ── IFL ──

def test_rep3_independent_triple_is_3():
    d, _ = build_domains(nodes(), KEYS)
    assert ifl(rep("n1", "n3", "n5"), 1, d)[0] == 3
    assert ifl(rep("n2", "n4", "n6"), 1, d)[0] == 3


def test_rep3_shared_power_is_2():
    d, _ = build_domains(nodes(), KEYS)
    level, cut = ifl(rep("n1", "n2", "n3"), 1, d)
    assert level == 2
    assert not survives(rep("n1", "n2", "n3"), 1, d, cut)


def test_demo_relabel_drops_to_1_with_power_a_cut():
    d, _ = build_domains(nodes(n3={"power": "A"}, n5={"power": "A"}), KEYS)
    level, cut = ifl(rep("n1", "n3", "n5"), 1, d)
    assert level == 1
    assert cut == [("power", "A")]


def test_demo_relabel_one_move_raises_to_2():
    # Auditor's fix: replace one holder with a machine off Power Strip A (n4/n6: power B/C).
    d, _ = build_domains(nodes(n3={"power": "A"}, n5={"power": "A"}), KEYS)
    best = max(ifl(rep(*(set(("n1", "n3", "n5")) - {a} | {b})), 1, d)[0]
               for a in ("n1", "n3", "n5") for b in ("n2", "n4", "n6"))
    assert best == 2
    # …and after that move, cutting Power Strip A leaves the chunk readable.
    for moved in (rep("n1", "n3", "n4"), rep("n3", "n5", "n4"), rep("n1", "n5", "n6")):
        assert ifl(moved, 1, d)[0] == 2
        assert survives(moved, 1, d, [("power", "A")])     # n4 is power B, n6 power C


def test_version_is_excluded_from_cuts():
    # If version counted, every chunk would have IFL 1 (one bad release kills all machines).
    d, _ = build_domains(nodes(), KEYS)
    level, cut = ifl(rep("n1", "n3", "n5"), 1, d)
    assert level == 3 and all(k != "version" for k, _ in cut)


def test_min_cut_prefers_label_domains():
    d, _ = build_domains(nodes(), KEYS)
    level, cut = ifl(rep("n1", "n3", "n5"), 1, d)
    assert cut == [("power", "A"), ("power", "B"), ("power", "C")]


def test_rep2():
    d, _ = build_domains(nodes(), KEYS)
    assert ifl(rep("n1", "n3"), 1, d)[0] == 2
    assert ifl(rep("n1", "n2"), 1, d) == (1, [("power", "A")])


def test_single_copy_and_empty():
    d, _ = build_domains(nodes(), KEYS)
    assert ifl(rep("n4"), 1, d)[0] == 1
    assert ifl({}, 1, d) == (0, [])
    assert ifl({"n1": set()}, 1, d) == (0, [])


def test_duplicate_frag_idx_counts_once():
    # Two rows of frag 0 during a move + frag 1 elsewhere: EC needs 2 distinct.
    d, _ = build_domains(nodes(), KEYS)
    assert ifl({"n1": {0}, "n3": {0}, "n5": {1}}, 2, d)[0] == 1


def test_ec42_default_labels_is_2_and_survives_any_power_strip():
    d, _ = build_domains(nodes(), KEYS)
    level, cut = ifl(ec_all6(), 4, d)
    assert level == 2
    assert len(cut) == 2 and not survives(ec_all6(), 4, d, cut)
    for p in ("A", "B", "C"):
        assert survives(ec_all6(), 4, d, [("power", p)])
    for a, b in combinations([("node", f"n{i}") for i in range(1, 7)], 2):
        assert survives(ec_all6(), 4, d, [a, b])       # any 2 machines: fine (m=2)


def test_ec42_independent_labels_reaches_target_3():
    own = {f"n{i}": {"power": f"P{i}", "switch": f"S{i}", "disk_batch": f"D{i}"} for i in range(1, 7)}
    d, _ = build_domains(nodes(**own), KEYS)
    level, cut = ifl(ec_all6(), 4, d)
    assert level == 3 == target_ifl(Policy(name="ec42", type="erasure", k=4, m=2, n=6, w=5))
    for p in own.values():
        assert survives(ec_all6(), 4, d, [("power", p["power"])])


def test_min_cut_is_a_real_minimum_bruteforce():
    d, _ = build_domains(nodes(n3={"power": "A"}), KEYS)
    doms = list(d)
    for holders, needed in [(rep("n1", "n3", "n5"), 1), (rep("n2", "n4", "n6"), 1), (ec_all6(), 4),
                            (rep("n1", "n2", "n5"), 1)]:
        level, cut = ifl(holders, needed, d)
        assert not survives(holders, needed, d, cut)
        for size in range(1, level):
            assert all(survives(holders, needed, d, list(c)) for c in combinations(doms, size))


def test_cache_hit_on_same_holder_set():
    d, _ = build_domains(nodes(), KEYS)
    h = rep("n2", "n4", "n6")
    ifl(h, 1, d)
    before = ifl_cache_info().hits
    for _ in range(5):
        assert ifl({"n2": {0}, "n4": {1}, "n6": {2}}, 1, d)[0] == 3
    assert ifl_cache_info().hits == before + 5


def test_cache_not_stale_after_relabel():
    h = rep("n1", "n3", "n5")
    d1, _ = build_domains(nodes(), KEYS)
    assert ifl(h, 1, d1)[0] == 3
    d2, _ = build_domains(nodes(n3={"power": "A"}, n5={"power": "A"}), KEYS)
    assert ifl(h, 1, d2)[0] == 1


def test_result_list_is_a_fresh_copy():
    d, _ = build_domains(nodes(), KEYS)
    _, cut = ifl(rep("n1", "n2"), 1, d)
    cut.append(("x", "y"))
    assert ifl(rep("n1", "n2"), 1, d)[1] == [("power", "A")]


@pytest.mark.parametrize("policy,expected", [
    (Policy(name="rep2", type="replication", n=2, w=2), 2),
    (Policy(name="rep3", type="replication", n=3, w=2), 3),
    (Policy(name="ec42", type="erasure", k=4, m=2, n=6, w=5), 3),
])
def test_target_ifl(policy, expected):
    assert target_ifl(policy) == expected
    assert target_ifl(CFG.policy(policy.name)) == expected
