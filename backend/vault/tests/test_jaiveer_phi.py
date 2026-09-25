"""Owner: Jaiveer (J1). Cover: 500 ms heartbeats then silence 1.0 s → φ≈2.2, 1.5 s → ≈7.3, 2.0 s → ≈18
(TECH_STACK §6.2); φ=0 before first heartbeat; φ small right after a heartbeat."""
import pytest

from vault.common.jaiveer_phi import Phi


def _steady(n: int = 50, period: float = 0.5) -> tuple[Phi, float]:
    p = Phi()
    t = 100.0
    for _ in range(n):
        p.heartbeat(t)
        t += period
    return p, t - period   # time of the last heartbeat


def test_zero_before_first_heartbeat():
    p = Phi()
    assert p.phi(0.0) == 0.0
    assert p.phi(1e6) == 0.0


@pytest.mark.parametrize("silence,expected", [(1.0, 2.2), (1.5, 7.3), (2.0, 18.0)])
def test_reference_values_steady_500ms(silence, expected):
    p, last = _steady()
    assert p.phi(last + silence) == pytest.approx(expected, abs=0.5)


def test_bootstrap_after_single_heartbeat():
    # Only the bootstrap window (mean 562 ms): not suspicious at 0.5 s, suspect (φ ≥ 8) by 2 s.
    p = Phi()
    p.heartbeat(10.0)
    assert p.phi(10.5) < 1.0
    assert 1.0 < p.phi(11.0) < 8.0
    assert p.phi(12.0) >= 8.0


def test_small_right_after_heartbeat():
    p, last = _steady()
    assert p.phi(last) < 1.0
    assert p.phi(last + 0.5) < 1.0


def test_crosses_suspect_threshold_near_1_6s():
    p, last = _steady()
    assert p.phi(last + 1.5) < 8.0
    assert p.phi(last + 1.7) >= 8.0


def test_monotonic_in_silence():
    p, last = _steady()
    vals = [p.phi(last + dt / 10) for dt in range(0, 40)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))


def test_heartbeat_resets_suspicion():
    p, last = _steady()
    assert p.phi(last + 3.0) > 8.0
    p.heartbeat(last + 3.0)
    assert p.phi(last + 3.0) < 1.0


def test_window_is_bounded():
    p = Phi(window=10)
    for i in range(100):
        p.heartbeat(i * 0.5)
    assert len(p.iv) == 10


def test_min_std_floor_applies():
    # Perfectly regular heartbeats → pstdev 0; the 200 ms floor keeps φ finite and moderate.
    p = Phi(min_std_s=0.2)
    for i in range(200):
        p.heartbeat(i * 0.5)
    v = p.phi(199 * 0.5 + 1.0)
    assert 1.0 < v < 20.0
