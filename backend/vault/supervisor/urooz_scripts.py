"""Seeded chaos scripts `standard` and `heavy` (ARCHITECTURE §5). Owner: Urooz. Task U3.

Pure data + a function that yields (t_seconds, action) steps; Anushka's chaos controller executes them.
    def steps(name: str, seed: int, nodes: list[str]) -> list[ChaosStep]
Fault-unit counting (standard ≈ 47, heavy ≈ 500) lives here too: def fault_units(steps) -> int
"""
from vault.common.models import ChaosStep

# ─────────────────────────────────────────────────────────────────────────────
# Standard chaos script (ARCHITECTURE §5, verbatim)
# 9 timed steps; same RNG seed for Vault and Naive so the comparison is fair.
# ─────────────────────────────────────────────────────────────────────────────
_STANDARD_STEPS: list[ChaosStep] = [
    ChaosStep(t=5,  action="corrupt",       params={"count": 20, "mode": "bitflip"}),
    ChaosStep(t=10, action="kill",          params={"pid": "n2"}),
    ChaosStep(t=15, action="link",          params={"a": "gw", "b": "n4", "cut": True, "direction": "both"}),
    ChaosStep(t=20, action="slow",          params={"pid": "n5", "ms": 800}),
    ChaosStep(t=25, action="power_cut",     params={"scope": "label", "label": "power=B", "restore_after_s": 6}),
    ChaosStep(t=35, action="corrupt",       params={"count": 20, "mode": "bitflip"}),
    ChaosStep(t=40, action="kill",          params={"pid": "n6"}),
    ChaosStep(t=45, action="link",          params={"a": "meta", "b": "n1", "cut": True, "direction": "both"}),
    ChaosStep(t=55, action="clear",         params={}),
]

# Fault units for standard (per ARCHITECTURE §5):
#   t=5:  corrupt 20 fragments  → 20
#   t=10: kill n2               → 1
#   t=15: cut gw↔n4 (both dir) → 2
#   t=20: slow n5               → 1
#   t=25: power=B has n3,n4     → 2 killed + restore → 2 machines in cut = 2
#   t=35: corrupt 20 fragments  → 20
#   t=40: kill n6               → 1
#   t=45: cut meta↔n1 (both)   → 2
#   t=55: clear                 → 0
# Total standard ≈ 20+1+2+1+2+20+1+2 = 49  (≈ 47 per spec — close enough; power=B label
# membership is runtime-determined so the spec gives an approximation)
_STANDARD_FAULT_UNITS = 49


def _standard(nodes: list[str]) -> list[ChaosStep]:   # noqa: ARG001
    """Return the standard script steps (nodes is unused; reserved for parameterisation)."""
    return list(_STANDARD_STEPS)


def _heavy(nodes: list[str]) -> list[ChaosStep]:  # noqa: ARG001
    """Heavy script: three 60-second cycles of standard, with 80 corruptions per burst.
    Crashed nodes are restarted at the end of each cycle (restart_down).
    ≈ 500 fault units, ~4 minutes including settling.
    """
    all_steps: list[ChaosStep] = []
    for cycle in range(3):
        offset = float(cycle * 70)   # 70 s per cycle (60 s run + 10 s settling margin)
        # Heavy burst: 80 corruptions at the start
        all_steps.append(ChaosStep(t=offset + 5,  action="corrupt",   params={"count": 80, "mode": "bitflip"}))
        all_steps.append(ChaosStep(t=offset + 10, action="kill",       params={"pid": "n2"}))
        all_steps.append(ChaosStep(t=offset + 15, action="link",       params={"a": "gw", "b": "n4", "cut": True, "direction": "both"}))
        all_steps.append(ChaosStep(t=offset + 20, action="slow",       params={"pid": "n5", "ms": 800}))
        all_steps.append(ChaosStep(t=offset + 25, action="power_cut",  params={"scope": "label", "label": "power=B", "restore_after_s": 6}))
        all_steps.append(ChaosStep(t=offset + 35, action="corrupt",    params={"count": 80, "mode": "bitflip"}))
        all_steps.append(ChaosStep(t=offset + 40, action="kill",       params={"pid": "n6"}))
        all_steps.append(ChaosStep(t=offset + 45, action="link",       params={"a": "meta", "b": "n1", "cut": True, "direction": "both"}))
        all_steps.append(ChaosStep(t=offset + 55, action="clear",      params={}))
        # Restart all stopped nodes at end of cycle (rejoin) — crash n2 and n6
        all_steps.append(ChaosStep(t=offset + 62, action="restart_down", params={}))
    return all_steps


def steps(name: str, seed: int, nodes: list[str]) -> list[ChaosStep]:   # noqa: ARG001
    """Return the named chaos script as an ordered list of ChaosStep.

    Args:
        name:  "standard" | "heavy"
        seed:  Reserved for future per-seed randomisation (currently ignored; scripts are deterministic).
        nodes: List of node pids in the cluster (e.g. ["n1","n2","n3","n4","n5","n6"]).

    Returns:
        List of ChaosStep sorted by t (seconds from script start).
    """
    if name == "standard":
        return _standard(nodes)
    if name == "heavy":
        return _heavy(nodes)
    raise ValueError(f"Unknown chaos script: {name!r}. Valid: 'standard', 'heavy'")


def fault_units(script_steps: list[ChaosStep]) -> int:
    """Count fault units for a list of ChaosStep.

    Counting rules (ARCHITECTURE §5):
      corrupt     → params["count"]
      kill        → 1
      link (cut)  → 2  (both directions)  or 1 (one direction)
      slow        → 1
      freeze      → 1
      power_cut   → count of nodes in the label group (unknown here → 2 per label assuming 2 nodes)
      power_restore, start, restart_down, clear → 0
    """
    total = 0
    for step in script_steps:
        action = step.action
        p = step.params
        if action == "corrupt":
            total += int(p.get("count", 1))
        elif action == "kill":
            total += 1
        elif action == "link":
            if p.get("cut", False):
                direction = p.get("direction", "both")
                total += 2 if direction == "both" else 1
        elif action in ("slow", "freeze"):
            total += 1
        elif action == "power_cut":
            # Without runtime labels we can't know the exact count; assume 2 per label power cut
            total += 2
    return total
