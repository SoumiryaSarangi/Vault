"""Run lifecycle preparing → running → settling → verifying → done; RunStatus + SSE; faults_injected counting. §5. Task U5.
Owner: Urooz.
"""
import asyncio
import time
import uuid
from pathlib import Path

from vault.common.config import VaultConfig
from vault.common.models import (
    ExternalEvent,
    InspectObject,
    InspectPage,
    LedgerEntry,
    ModeRequest,
    RunCounters,
    RunHealth,
    RunList,
    RunRequest,
    RunState,
    RunStatus,
    RunSummary,
    RunViolations,
    Severity,
    ViolationSample,
)
from vault.common.rpc import Rpc
from vault.oracle.urooz_checker import check
from vault.oracle.urooz_ledger import Ledger, load_ledger
from vault.oracle.urooz_workload import run_workload, verify_all_keys
from vault.supervisor.urooz_scripts import fault_units, steps

# ── in-memory state ─────────────────────────────────────────────────────────

_runs: dict[str, RunStatus] = {}     # run_id → RunStatus
_active_run_id: str | None = None   # only one run at a time
_run_tasks: dict[str, asyncio.Task] = {}  # run_id → background task
_subscribers: dict[str, list[asyncio.Queue]] = {}   # run_id → list of SSE queues


def get_run(run_id: str) -> RunStatus | None:
    return _runs.get(run_id)


def get_run_list() -> RunList:
    runs = sorted(_runs.values(), key=lambda r: r.started_at, reverse=True)
    summaries = [
        RunSummary(
            run_id=r.run_id, mode=r.mode, seed=r.seed, state=r.state,
            started_at=r.started_at, elapsed_s=r.elapsed_s,
            chaos_script="standard",
            counters=r.counters, violations=r.violations, health=r.health,
        )
        for r in runs
    ]
    return RunList(runs=summaries)


def is_run_active() -> bool:
    return _active_run_id is not None and _active_run_id in _runs and \
        _runs[_active_run_id].state not in ("done", "failed")


def subscribe(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _subscribers.setdefault(run_id, []).append(q)
    return q


def unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    subs = _subscribers.get(run_id, [])
    try:
        subs.remove(q)
    except ValueError:
        pass


def _notify(run_id: str, status: RunStatus) -> None:
    for q in list(_subscribers.get(run_id, [])):
        try:
            q.put_nowait(status.model_dump_json())
        except asyncio.QueueFull:
            pass


# ── run execution ────────────────────────────────────────────────────────────

def _run_dir(cfg: VaultConfig, run_id: str) -> Path:
    return Path(cfg.cluster.data_dir).parent / "oracle_runs" / run_id


async def _set_state(run_id: str, state: RunState) -> None:
    run = _runs[run_id]
    run.state = state
    run.elapsed_s = time.time() - run.started_at
    _notify(run_id, run)


async def _emit_event(rpc: Rpc, event_type: str, human: str, data: dict, severity: Severity = Severity.info) -> None:
    try:
        ev = ExternalEvent(type=event_type, severity=severity, human=human,
                           technical=human, data=data)
        await rpc.request("meta", "POST", "/v1/events", json=ev.model_dump(), timeout=5.0)
    except Exception:
        pass


async def _fetch_inspect(rpc: Rpc) -> list[InspectObject]:
    """Paginate through /v1/inspect/objects and return all objects."""
    objects: list[InspectObject] = []
    cursor: str | None = None
    while True:
        params: dict = {"limit": 500}
        if cursor:
            params["cursor"] = cursor
        try:
            r = await rpc.request("meta", "GET", "/v1/inspect/objects", params=params, timeout=10.0)
            if r.status_code != 200:
                break
            page = InspectPage(**r.json())
            objects.extend(page.objects)
            cursor = page.next_cursor
            if not cursor:
                break
        except Exception:
            break
    return objects


async def execute_run(run_id: str, req: RunRequest, cfg: VaultConfig, rpc: Rpc) -> None:
    """Background task: full run lifecycle."""
    global _active_run_id
    run = _runs[run_id]
    run_dir = _run_dir(cfg, run_id)

    try:
        # ── PREPARING ────────────────────────────────────────────────────────
        await _set_state(run_id, "preparing")

        # Set mode on metadata
        try:
            mode_req = ModeRequest(mode=req.mode)
            await rpc.request("meta", "POST", "/v1/mode", json=mode_req.model_dump(), timeout=10.0)
        except Exception:
            pass

        # Reset cluster via supervisor
        try:
            await rpc.request("sup", "POST", "/cluster/reset",
                              json={"seed": True, "mode": req.mode}, timeout=60.0)
        except Exception:
            pass

        await _emit_event(rpc, "oracle.run_started",
                          f"Oracle run started in {req.mode} mode (seed={req.seed}).",
                          {"run_id": run_id, "mode": req.mode})

        # ── RUNNING ─────────────────────────────────────────────────────────
        await _set_state(run_id, "running")
        ledger = Ledger(run_dir)
        await ledger.open()

        stop_event = asyncio.Event()
        counters_dict: dict = {"ops": 0, "unknown": 0, "failed": 0,
                               "acked_puts": 0, "acked_deletes": 0, "reads": 0}

        # Launch chaos script alongside the workload
        script_steps = steps(req.chaos_script, req.seed, list(cfg.node_ids()))
        fault_count = fault_units(script_steps)

        async def _run_chaos() -> None:
            """Fire chaos steps at their scheduled times relative to run start."""
            t0 = time.monotonic()
            for step in sorted(script_steps, key=lambda s: s.t):
                delay = step.t - (time.monotonic() - t0)
                if delay > 0:
                    await asyncio.sleep(delay)
                if stop_event.is_set():
                    break
                try:
                    await rpc.request("sup", "POST", "/chaos/script",
                                      json={"name": req.chaos_script, "seed": req.seed},
                                      timeout=5.0)
                except Exception:
                    pass
                run.counters.faults_injected = min(run.counters.faults_injected + 1, fault_count)
                run.timeline.append(
                    __import__("vault.common.models", fromlist=["TimelineMark"]).TimelineMark(
                        t=time.monotonic() - t0,
                        kind="chaos",
                        label=f"{step.action} {step.params}",
                    )
                )
                _notify(run_id, run)

        async def _update_loop() -> None:
            """Periodically push status updates to SSE subscribers."""
            while not stop_event.is_set():
                run.elapsed_s = time.time() - run.started_at
                run.counters.ops = counters_dict["ops"]
                run.counters.unknown = counters_dict["unknown"]
                run.counters.failed = counters_dict["failed"]
                _notify(run_id, run)
                await asyncio.sleep(0.5)

        workload_task = asyncio.create_task(
            run_workload(rpc, ledger, keyspace=req.keyspace, clients=req.clients,
                         duration_s=req.duration_s, seed=req.seed,
                         stop_event=stop_event, counters=counters_dict),
            name="oracle-workload"
        )
        chaos_task = asyncio.create_task(_run_chaos(), name="oracle-chaos")
        update_task = asyncio.create_task(_update_loop(), name="oracle-update")

        # Wait for workload to finish
        await workload_task
        stop_event.set()
        chaos_task.cancel()
        update_task.cancel()
        await asyncio.gather(chaos_task, update_task, return_exceptions=True)
        await ledger.close()

        # ── SETTLING ─────────────────────────────────────────────────────────
        await _set_state(run_id, "settling")
        # Wait until repair queue is empty or 60 s
        settle_deadline = time.monotonic() + cfg.oracle.settle_max_s
        while time.monotonic() < settle_deadline:
            try:
                r = await rpc.request("meta", "GET", "/v1/repair", timeout=5.0)
                if r.status_code == 200:
                    repair = r.json()
                    q = repair.get("queued", {})
                    active = repair.get("active", [])
                    if sum(q.values()) == 0 and len(active) == 0:
                        break
            except Exception:
                pass
            await asyncio.sleep(2.0)
            run.elapsed_s = time.time() - run.started_at
            _notify(run_id, run)

        # ── VERIFYING ────────────────────────────────────────────────────────
        await _set_state(run_id, "verifying")
        entries = load_ledger(run_dir)
        final_reads = await verify_all_keys(rpc, keyspace=req.keyspace)
        inspect = await _fetch_inspect(rpc)

        violations, health, samples = check(entries, final_reads, inspect)

        run.violations = violations
        run.health = health
        run.samples = samples
        run.counters.faults_injected = fault_count

        # Emit oracle events for each violation kind
        total_v = sum(violations.model_dump().values())
        if total_v > 0:
            await _emit_event(rpc, "oracle.violation",
                              f"Oracle found {total_v} violation(s) in {req.mode} mode.",
                              {"run_id": run_id, "violations": violations.model_dump()},
                              severity=Severity.danger)

        # ── DONE ─────────────────────────────────────────────────────────────
        await _set_state(run_id, "done")
        await _emit_event(rpc, "oracle.run_completed",
                          f"Oracle run complete. {req.mode}: {total_v} violation(s).",
                          {"run_id": run_id, "violations": violations.model_dump(), "mode": req.mode},
                          severity=Severity.success if total_v == 0 else Severity.warn)

    except asyncio.CancelledError:
        run.state = "failed"
        run.elapsed_s = time.time() - run.started_at
        _notify(run_id, run)
    except Exception as exc:
        run.state = "failed"
        run.elapsed_s = time.time() - run.started_at
        _notify(run_id, run)
        raise
    finally:
        _active_run_id = None


def start_run(req: RunRequest, cfg: VaultConfig, rpc: Rpc) -> str:
    """Create a run record, start the background task, return run_id."""
    global _active_run_id
    run_id = f"r_{req.mode}_{uuid.uuid4().hex[:8]}"
    run = RunStatus(
        run_id=run_id, mode=req.mode, seed=req.seed, state="preparing",
        started_at=time.time(), elapsed_s=0.0,
        counters=RunCounters(faults_injected=0),
        violations=RunViolations(),
        health=RunHealth(),
    )
    _runs[run_id] = run
    _active_run_id = run_id
    task = asyncio.create_task(execute_run(run_id, req, cfg, rpc), name=f"run-{run_id}")
    _run_tasks[run_id] = task
    return run_id


def stop_run(run_id: str) -> RunStatus | None:
    """Cancel a running run."""
    task = _run_tasks.get(run_id)
    if task and not task.done():
        task.cancel()
    return _runs.get(run_id)
