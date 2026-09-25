"""Phi accrual failure detector math (ARCHITECTURE §4.5, TECH_STACK §6.2). Owner: Jaiveer. Task J1.

Pure, no I/O. Used by brain/jaiveer_detector.py.
"""


class Phi:
    def __init__(self, window: int = 100, min_std_s: float = 0.2, first_s: float = 0.5):
        raise NotImplementedError("J1")

    def heartbeat(self, now: float) -> None:
        raise NotImplementedError("J1")

    def phi(self, now: float) -> float:
        raise NotImplementedError("J1")
