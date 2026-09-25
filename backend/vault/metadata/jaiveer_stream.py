"""GET /v1/stream (SSE: snapshot every 500 ms, event per new Event, last 100 on connect), GET/POST /v1/events. Task J6.
Owner: Jaiveer.

J4 part: the event bus (sink target for vault.common.events.emit), GET /v1/events, POST /v1/events.
J6 adds GET /v1/stream on top of EventBus.subscribe().
"""
import asyncio
import json
import time
from collections import deque
from typing import Any, Optional

from fastapi import APIRouter, Request

from vault.common.models import Event, EventList, ExternalEvent
from vault.common.service import VaultHTTPError

router = APIRouter()

EXTERNAL_PREFIXES = ("chaos.", "oracle.")


class EventBus:
    """Stores every event in SQLite (events table) and fans it out to live subscribers (SSE, J6)."""

    def __init__(self, db, keep: int = 100):
        self.db = db
        self.recent: deque[dict[str, Any]] = deque(maxlen=keep)
        self._subs: set[asyncio.Queue] = set()

    async def load_recent(self) -> None:
        rows = await self.db.fetchall("SELECT * FROM events ORDER BY id DESC LIMIT ?", (self.recent.maxlen,))
        for r in reversed(rows):
            self.recent.append(_row_to_event(r))

    async def sink(self, ev: dict[str, Any]) -> dict[str, Any]:
        """events.set_sink target. Never call emit() from inside a db.write fn (writer lock isn't reentrant)."""
        async def ins(c):
            cur = await c.execute(
                "INSERT INTO events (ts, type, severity, subject, human, technical, data) VALUES (?,?,?,?,?,?,?)",
                (ev["ts"], ev["type"], str(ev["severity"].value if hasattr(ev["severity"], "value") else ev["severity"]),
                 json.dumps(ev.get("subject", {}), default=str), ev["human"], ev.get("technical", ""),
                 json.dumps(ev.get("data", {}), default=str)))
            return cur.lastrowid
        ev = dict(ev)
        ev["id"] = await self.db.write(ins)
        self.recent.append(ev)
        for q in list(self._subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(ev)
        return ev

    def subscribe(self, maxsize: int = 500) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)


def _row_to_event(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "ts": r["ts"], "type": r["type"], "severity": r["severity"],
            "subject": json.loads(r["subject"] or "{}"), "human": r["human"], "technical": r["technical"],
            "data": json.loads(r["data"] or "{}")}


@router.get("/v1/events")
async def list_events(request: Request, after_id: int = 0, limit: int = 200) -> EventList:
    db = request.app.state.brain.db
    limit = max(1, min(limit, 1000))
    if after_id > 0:
        rows = await db.fetchall("SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT ?", (after_id, limit))
    else:
        rows = list(reversed(await db.fetchall("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))))
    return EventList(events=[Event(**_row_to_event(r)) for r in rows])


@router.post("/v1/events", status_code=201)
async def post_event(request: Request, body: ExternalEvent) -> Event:
    """External emitters only (supervisor chaos.*, oracle oracle.*). The body already carries human/technical."""
    if not body.type.startswith(EXTERNAL_PREFIXES):
        raise VaultHTTPError(400, "bad_event_type", "Only chaos.* and oracle.* events can be posted from outside.",
                             {"type": body.type})
    ev = {"ts": time.time(), "type": body.type, "severity": body.severity.value, "subject": body.subject,
          "human": body.human, "technical": body.technical, "data": body.data}
    stored = await request.app.state.bus.sink(ev)
    return Event(**stored)


def latest_event(bus: EventBus) -> Optional[dict[str, Any]]:
    return bus.recent[-1] if bus.recent else None
