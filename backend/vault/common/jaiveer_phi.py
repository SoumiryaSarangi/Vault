"""Phi accrual failure detector math (ARCHITECTURE §4.5, TECH_STACK §6.2). Owner: Jaiveer. Task J1.

Pure, no I/O. Used by brain/jaiveer_detector.py.

Keeps the last `window` heartbeat inter-arrival times (seconds). The window is bootstrapped with
[first_s, 1.25·first_s] so φ is meaningful from the first heartbeat on; std is floored at `min_std_s`.
With 500 ms heartbeats: silence 1.0 s → φ≈2.2, 1.5 s → φ≈7.3, 2.0 s → φ≈18 (φ ≥ 8 after ~1.6 s).
`now` is any monotonic clock in seconds (the detector passes time.monotonic()).
"""
import math
import statistics
from collections import deque


class Phi:
    def __init__(self, window: int = 100, min_std_s: float = 0.2, first_s: float = 0.5):
        self.iv: deque[float] = deque([first_s, first_s * 1.25], maxlen=window)
        self.last: float | None = None
        self.min_std = min_std_s

    def heartbeat(self, now: float) -> None:
        """Record a heartbeat arrival at time `now` (seconds)."""
        if self.last is not None:
            self.iv.append(now - self.last)
        self.last = now

    def phi(self, now: float) -> float:
        """Suspicion level at `now`. 0.0 before the first heartbeat."""
        if self.last is None:
            return 0.0
        t = now - self.last
        mean = statistics.fmean(self.iv)
        std = max(statistics.pstdev(self.iv), self.min_std)
        y = (t - mean) / std
        # TECH_STACK §6.2 computes e = exp(a) and -log10(e/(1+e)) (both branches are the same quantity). For long
        # silences exp(a) underflows to 0 and log10(0) raises, so use the identical, stable form:
        #   φ = log10(1 + e) − log10(e) = log1p(e)/ln10 − a/ln10
        a = -y * (1.5976 + 0.070566 * y * y)
        e = math.exp(a) if a < 700 else math.inf
        if math.isinf(e):
            return 0.0
        return (math.log1p(e) - a) / math.log(10)
