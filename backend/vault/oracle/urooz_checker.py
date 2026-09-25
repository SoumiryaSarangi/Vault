"""check(entries, final_reads, inspect) -> (RunViolations, RunHealth, samples). Pure. Rules + acceptable final states in §5. Task U1.
Owner: Urooz.

Five violation kinds (ARCHITECTURE §5):
  lost          – final GET 404/unavailable, but acceptable final states don't include "absent"
  damaged       – final GET bytes match no acceptable final value
  resurrected   – final GET returns data, but acceptable states are only "absent"
  stale_read    – a GET returned commit_seq c while an earlier-completed acked write had commit_seq > c
  phantom_read  – a GET returned bytes whose SHA-256 matches no PUT attempted on that key before the GET completed

Acceptable final states for a key:
  {result of the acked write with the highest commit_seq}
  ∪ {result of every unknown write invoked AFTER that acked write's invocation}
  A DELETE's result is "absent".
"""
import datetime
import time as _time
from collections import defaultdict

from vault.common.models import (
    InspectObject,
    LedgerEntry,
    RunHealth,
    RunViolations,
    ViolationSample,
)


def _ts(t: float) -> str:
    return datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%H:%M:%S")


def check(
    entries: list[LedgerEntry],
    final_reads: dict[str, tuple[str | None, int | None]],   # key -> (sha256|None, commit_seq|None)
    inspect: list[InspectObject],
) -> tuple[RunViolations, RunHealth, list[ViolationSample]]:
    """Pure checker.  All three arguments are computed after settling.

    Args:
        entries:     All LedgerEntry rows from the run ledger (in order).
        final_reads: Verification GETs performed after settling.
                     Value is (sha256, commit_seq) if the object is present,
                     or (None, None) if 404/unavailable.
        inspect:     Objects returned by GET /v1/inspect/objects after settling.

    Returns:
        (RunViolations, RunHealth, list[ViolationSample])
    """
    violations = RunViolations()
    samples: list[ViolationSample] = []

    # ── per-key history ───────────────────────────────────────────────────────
    # puts_by_key[key] = list of (invoke, complete, outcome, sha256, commit_seq)
    puts_by_key: dict[str, list[LedgerEntry]] = defaultdict(list)
    deletes_by_key: dict[str, list[LedgerEntry]] = defaultdict(list)
    # All GETs during the run (for stale_read and phantom_read detection)
    gets_by_key: dict[str, list[LedgerEntry]] = defaultdict(list)

    for e in entries:
        if e.op == "put":
            puts_by_key[e.key].append(e)
        elif e.op == "delete":
            deletes_by_key[e.key].append(e)
        elif e.op == "get":
            gets_by_key[e.key].append(e)

    # ── compute acceptable final states per key ───────────────────────────────
    all_keys = set(final_reads) | set(puts_by_key) | set(deletes_by_key)

    for key in all_keys:
        # Collect acked writes (puts + deletes) sorted by commit_seq (highest wins)
        acked_puts = [e for e in puts_by_key.get(key, []) if e.outcome == "ok" and e.commit_seq is not None]
        acked_dels = [e for e in deletes_by_key.get(key, []) if e.outcome == "ok"]

        # Find the acked write with the highest commit_seq
        best_acked: LedgerEntry | None = None
        for e in acked_puts + acked_dels:
            if best_acked is None or (e.commit_seq or 0) > (best_acked.commit_seq or 0):
                best_acked = e

        # Acceptable final states:
        # 1. Result of the best acked write
        acceptable_shas: set[str] = set()   # empty = only "absent" acceptable
        absent_acceptable = False
        if best_acked is not None:
            if best_acked.op == "delete":
                absent_acceptable = True
            else:
                if best_acked.sha256:
                    acceptable_shas.add(best_acked.sha256)

        # 2. Unknown writes invoked AFTER best_acked's invocation
        invoke_threshold = best_acked.invoke if best_acked else -1.0
        for e in puts_by_key.get(key, []):
            if e.outcome == "unknown" and e.invoke > invoke_threshold:
                if e.sha256:
                    acceptable_shas.add(e.sha256)
                # unknown PUT could also have failed → absent remains possible
                absent_acceptable = True
        for e in deletes_by_key.get(key, []):
            if e.outcome == "unknown" and e.invoke > invoke_threshold:
                absent_acceptable = True

        # If there have been no acked writes at all, every outcome is acceptable
        if best_acked is None:
            absent_acceptable = True   # key may never have been written

        # ── check final read ─────────────────────────────────────────────────
        final_sha, final_cs = final_reads.get(key, (None, None))
        present = final_sha is not None

        if not present:
            # Final GET was 404 or unavailable
            if not absent_acceptable:
                violations.lost += 1
                # Find the best acked put for the human message
                ref = max(acked_puts, key=lambda x: x.commit_seq or 0) if acked_puts else best_acked
                at = _ts(ref.invoke) if ref else "?"
                samples.append(ViolationSample(
                    kind="lost",
                    key=key,
                    human=f"{key} was saved at {at} and is now missing.",
                    technical=f"acked put commit_seq={ref.commit_seq if ref else '?'}; final GET 404",
                ))
        else:
            # Final GET returned data
            if absent_acceptable and not acceptable_shas:
                # Only acceptable state was "absent" (all writes were deletes, no unknown puts)
                violations.resurrected += 1
                samples.append(ViolationSample(
                    kind="resurrected",
                    key=key,
                    human=f"{key} was deleted but has come back from the dead.",
                    technical=f"final GET returned sha256={final_sha}; only acceptable state was absent",
                ))
            elif acceptable_shas and final_sha not in acceptable_shas:
                violations.damaged += 1
                exp = next(iter(acceptable_shas))[:16]
                samples.append(ViolationSample(
                    kind="damaged",
                    key=key,
                    human=f"{key} was corrupted: the bytes don't match what was saved.",
                    technical=f"final GET sha256={final_sha}; expected one of {sorted(acceptable_shas)}",
                ))

    # ── stale_read check ─────────────────────────────────────────────────────
    # For every GET that returned a commit_seq c, check if any acked write with
    # commit_seq > c completed BEFORE the GET was invoked.
    for key, gets in gets_by_key.items():
        for g in gets:
            if g.outcome != "ok" or g.commit_seq is None:
                continue
            for e in puts_by_key.get(key, []) + deletes_by_key.get(key, []):
                if (
                    e.outcome == "ok"
                    and e.commit_seq is not None
                    and e.commit_seq > g.commit_seq
                    and e.complete < g.invoke   # completed before GET started
                ):
                    violations.stale_read += 1
                    samples.append(ViolationSample(
                        kind="stale_read",
                        key=key,
                        human=f"{key} returned an old version: a newer save (#{e.commit_seq}) was already visible.",
                        technical=(
                            f"GET at invoke={g.invoke:.3f} returned commit_seq={g.commit_seq}; "
                            f"acked write commit_seq={e.commit_seq} completed at {e.complete:.3f}"
                        ),
                    ))
                    break  # one sample per GET

    # ── phantom_read check ───────────────────────────────────────────────────
    # A GET returned bytes whose SHA-256 matches no PUT attempted on that key
    # before the GET *completed*.
    all_put_shas_by_key: dict[str, set[str]] = {}
    for key, gets in gets_by_key.items():
        for g in gets:
            if g.outcome != "ok" or g.sha256 is None:
                continue
            # Collect SHA-256s of all PUTs attempted (invoked) before this GET completed
            known = all_put_shas_by_key.get(key)
            if known is None:
                known = set()
                for e in puts_by_key.get(key, []):
                    if e.sha256 and e.invoke < g.complete:
                        known.add(e.sha256)
                all_put_shas_by_key[key] = known
            if g.sha256 not in known:
                violations.phantom_read += 1
                samples.append(ViolationSample(
                    kind="phantom_read",
                    key=key,
                    human=f"{key} returned bytes that were never written to it.",
                    technical=(
                        f"GET sha256={g.sha256} not in any PUT sha256 for this key "
                        f"(invoked before GET completed at {g.complete:.3f})"
                    ),
                ))
                break  # one sample per key

    # ── under_protected (health, separate) ───────────────────────────────────
    under = sum(
        1 for obj in inspect
        if obj.state == "live" and obj.durable_min < obj.target
    )
    health = RunHealth(under_protected=under)

    return violations, health, samples
