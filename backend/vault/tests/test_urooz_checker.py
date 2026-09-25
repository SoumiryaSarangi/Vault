"""Owner: Urooz (U1). Cover every rule in ARCHITECTURE §5 with hand-written ledgers: lost, damaged,
resurrected, stale_read, phantom_read; unknown writes widen the acceptable final states (no false alarms).
"""
import pytest

from vault.common.models import InspectObject, LedgerEntry, RunViolations
from vault.oracle.urooz_checker import check

# ── helpers ──────────────────────────────────────────────────────────────────

T = 1_700_000_000.0   # arbitrary epoch anchor


def put(key: str, sha: str, commit_seq: int, invoke_offset: float = 0.0,
        complete_offset: float = 0.1, outcome: str = "ok") -> LedgerEntry:
    return LedgerEntry(
        op="put", key=key, sha256=sha,
        invoke=T + invoke_offset, complete=T + complete_offset,
        outcome=outcome, commit_seq=commit_seq, seq=commit_seq, http=200 if outcome == "ok" else None,
    )


def get(key: str, sha: str | None, commit_seq: int | None,
        invoke_offset: float = 0.5, complete_offset: float = 0.6,
        outcome: str = "ok") -> LedgerEntry:
    return LedgerEntry(
        op="get", key=key, sha256=sha,
        invoke=T + invoke_offset, complete=T + complete_offset,
        outcome=outcome, commit_seq=commit_seq, http=200 if sha else 404,
    )


def delete(key: str, commit_seq: int, invoke_offset: float = 0.0,
           complete_offset: float = 0.1, outcome: str = "ok") -> LedgerEntry:
    return LedgerEntry(
        op="delete", key=key,
        invoke=T + invoke_offset, complete=T + complete_offset,
        outcome=outcome, commit_seq=commit_seq, http=200 if outcome == "ok" else None,
    )


def inspect_obj(key: str, durable_min: int = 3, target: int = 3,
                state: str = "live") -> InspectObject:
    return InspectObject(bucket="oracle", key=key, state=state,
                         seq=1, commit_seq=1, sha256="aa", size=100,
                         durable_min=durable_min, target=target, ifl=durable_min)


NO_INSPECT: list[InspectObject] = []


# ── lost ──────────────────────────────────────────────────────────────────────

def test_lost_triggers_when_acked_put_missing():
    """An acked PUT whose key is 404 at verification is a lost violation."""
    entries = [put("k1", "sha_a", 10)]
    final_reads = {"k1": (None, None)}
    v, h, s = check(entries, final_reads, NO_INSPECT)
    assert v.lost == 1
    assert s[0].kind == "lost"
    assert "k1" in s[0].human


def test_lost_no_alarm_when_absent_is_acceptable():
    """If the only acked write is a DELETE, absent is acceptable (no lost)."""
    entries = [put("k1", "sha_a", 5), delete("k1", 10)]
    final_reads = {"k1": (None, None)}
    v, _, _ = check(entries, final_reads, NO_INSPECT)
    assert v.lost == 0


def test_lost_no_alarm_for_unknown_write():
    """A key with ONLY unknown writes — absent is acceptable (no false alarm)."""
    entries = [put("k1", "sha_a", None, outcome="unknown")]
    final_reads = {"k1": (None, None)}
    v, _, _ = check(entries, final_reads, NO_INSPECT)
    assert v.lost == 0


# ── damaged ──────────────────────────────────────────────────────────────────

def test_damaged_triggers_when_wrong_bytes_returned():
    """Final GET returns a sha256 that doesn't match any acceptable write."""
    entries = [put("k1", "sha_correct", 10)]
    final_reads = {"k1": ("sha_wrong", 10)}
    v, _, s = check(entries, final_reads, NO_INSPECT)
    assert v.damaged == 1
    assert s[0].kind == "damaged"


def test_damaged_no_alarm_when_sha_matches():
    """No damaged when final sha256 matches the acked put."""
    entries = [put("k1", "sha_correct", 10)]
    final_reads = {"k1": ("sha_correct", 10)}
    v, _, _ = check(entries, final_reads, NO_INSPECT)
    assert v.damaged == 0


def test_damaged_no_alarm_unknown_write_matches():
    """Unknown write sha matches the final read → no damaged violation."""
    entries = [
        put("k1", "sha_acked", 5),
        put("k1", "sha_unknown", None, invoke_offset=0.2, outcome="unknown"),
    ]
    final_reads = {"k1": ("sha_unknown", 5)}
    v, _, _ = check(entries, final_reads, NO_INSPECT)
    assert v.damaged == 0


# ── resurrected ───────────────────────────────────────────────────────────────

def test_resurrected_triggers_when_deleted_key_returns():
    """A key deleted (and no unknown puts after) that returns data = resurrected."""
    entries = [
        put("k1", "sha_a", 5),
        delete("k1", 10, invoke_offset=0.2, complete_offset=0.3),
    ]
    final_reads = {"k1": ("sha_a", 5)}
    v, _, s = check(entries, final_reads, NO_INSPECT)
    assert v.resurrected == 1
    assert s[0].kind == "resurrected"


def test_resurrected_no_alarm_when_unknown_put_after_delete():
    """Unknown PUT after the delete makes data acceptable (no resurrected)."""
    entries = [
        put("k1", "sha_a", 5),
        delete("k1", 10, invoke_offset=0.2, complete_offset=0.3),
        put("k1", "sha_unknown", None, invoke_offset=0.4, outcome="unknown"),
    ]
    final_reads = {"k1": ("sha_unknown", 5)}
    v, _, _ = check(entries, final_reads, NO_INSPECT)
    assert v.resurrected == 0


# ── stale_read ────────────────────────────────────────────────────────────────

def test_stale_read_triggers():
    """GET returned commit_seq=5, but an acked write with commit_seq=10 completed first."""
    entries = [
        put("k1", "sha_new", 10, invoke_offset=0.0, complete_offset=0.2),   # completed before GET
        get("k1", "sha_old", 5, invoke_offset=0.5, complete_offset=0.6),    # GET invoked after
    ]
    final_reads = {"k1": ("sha_new", 10)}
    v, _, s = check(entries, final_reads, NO_INSPECT)
    assert v.stale_read == 1
    assert s[0].kind == "stale_read"


def test_stale_read_no_alarm_when_write_after_get():
    """If the write with higher commit_seq completed AFTER the GET was invoked, no stale_read."""
    entries = [
        put("k1", "sha_new", 10, invoke_offset=0.6, complete_offset=0.9),  # after GET
        get("k1", "sha_old", 5, invoke_offset=0.5, complete_offset=0.7),
    ]
    final_reads = {"k1": ("sha_new", 10)}
    v, _, _ = check(entries, final_reads, NO_INSPECT)
    assert v.stale_read == 0


# ── phantom_read ──────────────────────────────────────────────────────────────

def test_phantom_read_triggers():
    """GET returned bytes that were never PUT to this key."""
    entries = [
        put("k1", "sha_real", 5),
        get("k1", "sha_ghost", 5, invoke_offset=0.5, complete_offset=0.6),
    ]
    final_reads = {"k1": ("sha_real", 5)}
    v, _, s = check(entries, final_reads, NO_INSPECT)
    assert v.phantom_read == 1
    assert s[0].kind == "phantom_read"


def test_phantom_read_no_alarm_when_sha_matches_put():
    """GET returned the sha256 that was PUT — fine."""
    entries = [
        put("k1", "sha_real", 5),
        get("k1", "sha_real", 5, invoke_offset=0.5, complete_offset=0.6),
    ]
    final_reads = {"k1": ("sha_real", 5)}
    v, _, _ = check(entries, final_reads, NO_INSPECT)
    assert v.phantom_read == 0


# ── all unknown — no false alarms ────────────────────────────────────────────

def test_all_unknown_writes_no_violations():
    """A ledger full of unknown writes must produce zero violations for any final state."""
    entries = [
        put("k1", "sha_a", None, outcome="unknown"),
        put("k1", "sha_b", None, invoke_offset=0.2, outcome="unknown"),
        delete("k1", None, invoke_offset=0.4, outcome="unknown"),
    ]
    # Whether the key is absent or present, no violation
    for final in [{"k1": (None, None)}, {"k1": ("sha_a", None)}, {"k1": ("sha_b", None)}]:
        v, _, _ = check(entries, final, NO_INSPECT)  # type: ignore[arg-type]
        assert v == RunViolations(), f"Got violations with final={final}: {v}"


# ── under_protected health ────────────────────────────────────────────────────

def test_under_protected_counted_from_inspect():
    inspect = [
        inspect_obj("k1", durable_min=3, target=3),   # OK
        inspect_obj("k2", durable_min=1, target=3),   # under-protected
        inspect_obj("k3", durable_min=0, target=3),   # under-protected
    ]
    entries = [put("k1", "sha_a", 1), put("k2", "sha_b", 2), put("k3", "sha_c", 3)]
    final_reads = {"k1": ("sha_a", 1), "k2": ("sha_b", 2), "k3": ("sha_c", 3)}
    _, h, _ = check(entries, final_reads, inspect)
    assert h.under_protected == 2


def test_under_protected_ignores_deleted():
    inspect = [inspect_obj("k1", durable_min=1, target=3, state="deleted")]
    entries: list[LedgerEntry] = []
    _, h, _ = check(entries, {}, inspect)
    assert h.under_protected == 0


# ── multi-key isolation ───────────────────────────────────────────────────────

def test_violations_are_per_key():
    """Violations on k1 must not bleed into k2."""
    entries = [
        put("k1", "sha_k1", 5),
        put("k2", "sha_k2", 6),
    ]
    # k1 is lost, k2 is fine
    final_reads: dict[str, tuple[str | None, int | None]] = {
        "k1": (None, None),
        "k2": ("sha_k2", 6),
    }
    v, _, s = check(entries, final_reads, NO_INSPECT)
    assert v.lost == 1
    assert all(samp.key == "k1" for samp in s if samp.kind == "lost")
    assert v.damaged == 0


# ── clean run — no violations ─────────────────────────────────────────────────

def test_clean_run_no_violations():
    """A healthy run: each key's final read matches the last acked put."""
    keys = [f"k{i:03d}" for i in range(10)]
    entries = [put(k, f"sha_{k}", i + 1) for i, k in enumerate(keys)]
    final_reads: dict[str, tuple[str | None, int | None]] = {
        k: (f"sha_{k}", i + 1) for i, k in enumerate(keys)
    }
    v, h, s = check(entries, final_reads, NO_INSPECT)
    assert v == RunViolations()
    assert h.under_protected == 0
    assert s == []
