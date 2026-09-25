"""Seeded chaos scripts `standard` and `heavy` (ARCHITECTURE §5). Owner: Urooz. Task U3.

Pure data + a function that yields (t_seconds, action) steps; Anushka's chaos controller executes them.
    def steps(name: str, seed: int, nodes: list[str]) -> list[ChaosStep]
Fault-unit counting (standard ≈ 47, heavy ≈ 500) lives here too: def fault_units(steps) -> int
"""
