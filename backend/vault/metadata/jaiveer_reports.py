"""POST /v1/reports/fragment, POST /v1/participants/{pid}/report, POST /v1/incidents/fault, POST /v1/scrub (fan-out).
Task J7. Owner: Jaiveer.

J6 part (needed by the detector and the snapshot): /v1/incidents/fault (supervisor ground truth for fault_at and
the node fault chips) and /v1/participants/{pid}/report (gateway reach row + traffic stats).
J7 adds fragment reports, read.failover aggregation and scrub fan-out.
"""
import time

from fastapi import APIRouter, Request, Response

from vault.common.models import FaultReport, ParticipantReport

router = APIRouter()


def _brain(request: Request):
    return request.app.state.brain


@router.post("/v1/incidents/fault", status_code=204)
async def fault(request: Request, body: FaultReport) -> Response:
    brain = _brain(request)
    brain.faults.record(body.kind, body.subject, body.at)
    brain.invalidate_snapshot()
    return Response(status_code=204)


@router.post("/v1/participants/{pid}/report", status_code=204)
async def participant_report(request: Request, pid: str, body: ParticipantReport) -> Response:
    brain = _brain(request)
    now = time.monotonic()
    brain.membership.participant_report(pid, body.reach, now)
    if body.stats is not None:
        brain.gw_stats = (body.stats, now)
    return Response(status_code=204)
