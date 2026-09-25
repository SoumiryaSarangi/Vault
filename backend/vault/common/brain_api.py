"""Interface between the metadata process and the health-brain modules. Owner: Anushka (shared, frozen).

Why this exists: brain modules owned by different people (Jaiveer: detector, scheduler, repair,
auditor; Soum: reconciler, gc, rebalancer) all run INSIDE the metadata process and need the same
handles. Jaiveer's metadata app builds one object that satisfies BrainContext and passes it to
every loop. Nobody imports another owner's private module to get at the DB.

Loop signature every brain module exposes:
    async def run(ctx: BrainContext) -> None        # started in the metadata lifespan, loops forever

Extra entry points (called by Jaiveer's metadata routes):
    soum_reconciler.reconcile_inventory(ctx, inv: Inventory) -> InventoryResult     # POST /v1/nodes/{id}/inventory
    soum_rebalancer.on_node_added(ctx, node_id: str) -> None                        # new ALIVE node with no fragments
    soum_rebalancer.on_drain(ctx, node_id: str) -> None                             # POST /v1/nodes/{id}/decommission
    jaiveer_auditor.request_audit(ctx, reason: str) -> None                         # label/membership change
"""
from typing import Any, Awaitable, Callable, Optional, Protocol, Sequence, TypeVar

import aiosqlite

from vault.common.config import SafetyCfg, VaultConfig
from vault.common.models import JobKind, JobReason, NodeView
from vault.common.rpc import Rpc

T = TypeVar("T")


class Db(Protocol):
    """SQLite access (ARCHITECTURE §11): one writer guarded by a lock + a separate reader (WAL)."""

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict[str, Any]]: ...

    async def write(self, fn: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
        """Run fn(conn) inside BEGIN IMMEDIATE … COMMIT under the single writer lock."""
        ...


class BrainContext(Protocol):
    cfg: VaultConfig
    rpc: Rpc
    db: Db

    def now(self) -> float: ...

    def nodes(self) -> list[NodeView]:
        """Current in-memory membership view (state, labels, epoch, disk, fenced, slow)."""
        ...

    def node(self, node_id: str) -> Optional[NodeView]: ...

    def safety(self) -> SafetyCfg:
        """Live safety switches (they change with POST /v1/mode)."""
        ...

    async def enqueue_job(self, kind: JobKind, reason: JobReason, *, chunk_id: Optional[str], frag_idx: Optional[int],
                          priority: int, source_node: Optional[str] = None, target_node: Optional[str] = None,
                          incident_id: Optional[int] = None) -> int:
        """Insert a queued job (deduplicated on kind+chunk_id+frag_idx while queued/running). Returns job id."""
        ...

    async def emit(self, type_: str, subject: dict[str, Any], data: Optional[dict[str, Any]] = None,
                   **fields: Any) -> None: ...
