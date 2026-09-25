"""Vault API contract v1 — the executable form of ARCHITECTURE.md §7 and §8.

FROZEN. Owner: Anushka. Changing anything here is gated (TEAM_PROTOCOL §4).
The TypeScript mirror is web/lib/contracts.ts (same field names, snake_case kept).

Every payload that crosses a process boundary is one of these models.
No ad-hoc dicts across processes (TECH_STACK §5).
"""
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _M(BaseModel):
    # Ignore unknown fields so a newer producer never breaks an older consumer.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ─────────────────────────────── Enums (§3.4) ───────────────────────────────

class NodeState(str, Enum):
    JOINING = "JOINING"
    ALIVE = "ALIVE"
    SUSPECT = "SUSPECT"
    PARTITIONED = "PARTITIONED"
    DOWN = "DOWN"
    DEAD = "DEAD"
    REJOINING = "REJOINING"
    DRAINING = "DRAINING"
    RETIRED = "RETIRED"


class FragState(str, Enum):
    pending = "pending"
    ok = "ok"
    incoming = "incoming"
    corrupt = "corrupt"
    missing = "missing"
    lost = "lost"
    trim = "trim"


class VersionState(str, Enum):
    pending = "pending"
    committed = "committed"
    aborted = "aborted"
    superseded = "superseded"


class VersionKind(str, Enum):
    data = "data"
    tombstone = "tombstone"


class JobKind(str, Enum):
    repair = "repair"
    move = "move"
    trim = "trim"


class JobReason(str, Enum):
    under_replicated = "under_replicated"
    corrupt = "corrupt"
    missing = "missing"
    fate = "fate"
    rebalance = "rebalance"
    drain = "drain"
    over_replicated = "over_replicated"
    orphan = "orphan"
    superseded = "superseded"
    aborted = "aborted"


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class Severity(str, Enum):
    info = "info"
    success = "success"
    warn = "warn"
    danger = "danger"


Mode = Literal["vault", "naive"]
PolicyName = Literal["rep2", "rep3", "ec42"]
PolicyType = Literal["replication", "erasure"]
SummaryLevel = Literal["ok", "degraded", "at_risk", "critical"]
FragmentProblem = Literal["corrupt", "missing", "unreachable", "slow"]
ReportContext = Literal["read", "scrub", "repair"]
Direction = Literal["in", "out", "both"]


# ─────────────────────────────── Common ───────────────────────────────

class ErrorBody(_M):
    """Every error response: {"error": "<code>", "message": "<human>", "detail": {...}}"""
    error: str
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class Policy(_M):
    """§3.3. Replication: n, w. Erasure: k, m, n=k+m, w."""
    name: str
    type: PolicyType
    n: int
    w: int
    k: Optional[int] = None
    m: Optional[int] = None

    @property
    def needed(self) -> int:
        """Distinct verified fragments needed to read a chunk."""
        return self.k if self.type == "erasure" and self.k else 1


class DomainRef(_M):
    """A shared-fate domain. key is a label key ('power', …) or 'node' for a single machine."""
    key: str
    value: str


class Reach(_M):
    ok: bool
    rtt_ms: Optional[float] = None


# ─────────────────────────── 7.1 Gateway (public) ───────────────────────────

class CreateBucketRequest(_M):
    policy: PolicyName = "rep3"


class BucketInfo(_M):
    bucket: str
    policy: str


class ObjectListItem(_M):
    key: str
    size: int
    etag: str
    seq: int
    commit_seq: int
    policy: str
    updated_at: float


class ObjectList(_M):
    bucket: str
    objects: list[ObjectListItem] = Field(default_factory=list)


class PutStored(_M):
    chunks: int
    fragments_acked: int
    w: int
    relayed: int = 0


class PutResult(_M):
    bucket: str
    key: str
    version_id: str
    seq: int
    commit_seq: int
    etag: str
    size: int
    policy: str
    stored: PutStored


class DeleteResult(_M):
    deleted: bool
    commit_seq: Optional[int] = None


class ServiceHealth(_M):
    """GET /_vault/health on gateway, metadata, oracle, supervisor."""
    pid: str
    ok: bool = True
    config_version: int = 0


# ─────────────────────────── 7.2 Metadata ───────────────────────────

class BucketCreate(_M):
    name: str
    policy: PolicyName = "rep3"


class Bucket(_M):
    name: str
    policy: Policy


class BucketList(_M):
    buckets: list[Bucket] = Field(default_factory=list)


class ModeRequest(_M):
    mode: Mode


class ModeResult(_M):
    mode: Mode
    config_version: int


class ExternalEvent(_M):
    """POST /v1/events — only supervisor (chaos.*) and oracle (oracle.*) use this."""
    type: str
    severity: Severity = Severity.info
    subject: dict[str, Any] = Field(default_factory=dict)
    human: str
    technical: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


# Nodes and participants

class RegisterRequest(_M):
    node_id: str
    addr: str
    capacity_bytes: int
    display_name: str
    labels: dict[str, str] = Field(default_factory=dict)
    discarded_on_startup: int = 0   # tmp/*.part removed at startup → metadata emits node.startup_discarded


class RegisterReply(_M):
    epoch: int
    state: NodeState
    lease_ttl_ms: int
    config_version: int


class ScrubStatus(_M):
    last_pass_at: Optional[float] = None
    scanned: int = 0
    corrupt_found: int = 0
    running: bool = False


class Heartbeat(_M):
    node_id: str
    epoch: int
    disk_used: int
    capacity: int
    fragments: int
    fenced: bool = False
    reach: dict[str, Reach] = Field(default_factory=dict)
    scrub: ScrubStatus = Field(default_factory=ScrubStatus)


class HeartbeatReply(_M):
    """state == DEAD tells the node to re-register (§4.6)."""
    state: NodeState
    epoch: int
    lease_ttl_ms: int
    config_version: int


class InventoryItem(_M):
    fid: str
    sha256: str
    size: int
    mtime: float


class Inventory(_M):
    node_id: str
    epoch: int
    fragments: list[InventoryItem] = Field(default_factory=list)


class InventoryResult(_M):
    missing: int = 0
    adopted: int = 0
    orphans: int = 0
    corrupt: int = 0


class LabelsPatch(_M):
    labels: dict[str, str]
    display_name: Optional[str] = None


class OpStats(_M):
    ok: int = 0
    failed: int = 0


class GatewayStats(_M):
    window_s: float = 1
    ok: int = 0
    failed: int = 0
    by_op: dict[str, OpStats] = Field(default_factory=dict)
    failover_reads: dict[str, int] = Field(default_factory=dict)  # node_id → reads served elsewhere (read.failover)


class ParticipantReport(_M):
    reach: dict[str, Reach] = Field(default_factory=dict)
    stats: Optional[GatewayStats] = None


class FaultReport(_M):
    """POST /v1/incidents/fault — supervisor ground truth for MTTR."""
    kind: str
    subject: str
    at: float


# Uploads, objects

class UploadRequest(_M):
    bucket: str
    key: str
    size: int
    if_match: Optional[str] = None
    if_none_match: Optional[str] = None


class TargetRef(_M):
    frag_idx: int
    node_id: str
    addr: str
    epoch: int


class SpareRef(_M):
    node_id: str
    addr: str
    epoch: int


class PlanChunk(_M):
    chunk_id: str
    idx: int
    size: int
    targets: list[TargetRef]
    spares: list[SpareRef] = Field(default_factory=list)


class UploadPlan(_M):
    version_id: str
    chunk_size: int
    policy: Policy
    chunks: list[PlanChunk] = Field(default_factory=list)


class CommitFragment(_M):
    frag_idx: int
    node_id: str
    sha256: str
    ok: bool


class CommitChunk(_M):
    chunk_id: str
    sha256: str
    frag_size: int
    fragments: list[CommitFragment]


class CommitRequest(_M):
    object_sha256: str
    size: int
    chunks: list[CommitChunk] = Field(default_factory=list)


class CommitResult(_M):
    version_id: str
    seq: int
    commit_seq: int
    etag: str
    under_replicated_chunks: int = 0


class FragLoc(_M):
    frag_idx: int
    node_id: str
    addr: str
    state: FragState
    sha256: Optional[str] = None
    route: str = "direct"          # "direct" | "relay:n2"
    slow: bool = False


class ManifestChunk(_M):
    chunk_id: str
    idx: int
    size: int
    sha256: str
    frag_size: int
    fragments: list[FragLoc]


class Manifest(_M):
    bucket: str
    key: str
    version_id: str
    seq: int
    commit_seq: int
    size: int
    sha256: str
    policy: Policy                  # full policy (gateway needs k/n to decode)
    chunk_size: int
    chunks: list[ManifestChunk] = Field(default_factory=list)


class ChunkHealth(_M):
    idx: int
    needed: int
    durable: int
    available: int
    fragments: list[FragLoc]


class ObjectHealth(_M):
    ifl: int
    target: int
    chunks: list[ChunkHealth] = Field(default_factory=list)
    shared: list[DomainRef] = Field(default_factory=list)
    min_cut: list[DomainRef] = Field(default_factory=list)


# Health brain and inspection

class FragmentReport(_M):
    fid: str
    node_id: str
    problem: FragmentProblem
    observed_by: str
    context: ReportContext


class Job(_M):
    id: int
    kind: JobKind
    reason: JobReason
    chunk_id: Optional[str] = None
    frag_idx: Optional[int] = None
    source_node: Optional[str] = None
    target_node: Optional[str] = None
    priority: int
    state: JobState
    attempts: int = 0
    bytes: int = 0
    error: Optional[str] = None
    incident_id: Optional[int] = None
    created_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class RepairStatus(_M):
    queued: dict[str, int] = Field(default_factory=lambda: {"p0": 0, "p1": 0, "p2": 0, "p3": 0, "p4": 0})
    active: list[Job] = Field(default_factory=list)
    recent: list[Job] = Field(default_factory=list)


class Incident(_M):
    id: int
    kind: str                       # node_dead | corruption | power_cut | fate | partition
    subject: Optional[str] = None
    fault_at: Optional[float] = None
    detected_at: Optional[float] = None
    repair_started_at: Optional[float] = None
    recovered_at: Optional[float] = None
    affected_chunks: int = 0
    remaining_chunks: int = 0
    bytes_repaired: int = 0


class IncidentList(_M):
    incidents: list[Incident] = Field(default_factory=list)


class ScrubRequest(_M):
    nodes: Optional[list[str]] = None


class InspectObject(_M):
    bucket: str
    key: str
    state: Literal["live", "deleted"]
    seq: int
    commit_seq: int
    sha256: Optional[str] = None
    size: int = 0
    durable_min: int = 0
    target: int = 0
    ifl: int = 0


class InspectPage(_M):
    objects: list[InspectObject] = Field(default_factory=list)
    next_cursor: Optional[str] = None


# ─────────────────────────── 7.3 Storage node ───────────────────────────

class FragmentHeader(_M):
    """Stored at the start of every .blk file (§3.6) and sent base64url-JSON in X-Vault-Meta."""
    fid: str
    chunk_id: str
    frag_idx: int
    version_id: str
    bucket: str
    key: str
    policy: str
    chunk_sha256: str
    frag_sha256: str
    chunk_size: int
    frag_size: int
    epoch: int


class FragmentPutResult(_M):
    fid: str
    sha256: str
    size: int
    durable: bool


class PullSource(_M):
    node_id: str
    addr: str
    fid: str


class PullEc(_M):
    k: int
    n: int
    frag_idx: int


class PullRequest(_M):
    fid: str
    header: FragmentHeader
    expected_sha256: str
    mode: Literal["copy", "ec_rebuild"]
    sources: list[PullSource]
    ec: Optional[PullEc] = None


class PullResult(_M):
    fid: str
    sha256: str
    size: int
    ms: float
    source_used: str


class Ping(_M):
    pid: str
    epoch: int = 0
    ts: float


class NodeHealth(_M):
    pid: str
    epoch: int
    fenced: bool
    lease_expiry: Optional[float] = None
    disk_used: int
    capacity: int
    fragments: int
    scrub: ScrubStatus = Field(default_factory=ScrubStatus)


# Chaos endpoints (every Python participant; corrupt/disk_full on nodes only)

class BlockRequest(_M):
    peers: list[str]
    direction: Direction = "both"


class UnblockRequest(_M):
    peers: Optional[list[str]] = None


class SlowRequest(_M):
    ms: int


class FreezeRequest(_M):
    seconds: float


class CorruptRequest(_M):
    count: Optional[int] = None
    fids: Optional[list[str]] = None
    mode: Literal["bitflip", "zero", "truncate", "delete"] = "bitflip"


class CorruptResult(_M):
    corrupted: list[str] = Field(default_factory=list)


class DiskFullRequest(_M):
    on: bool


class ChaosState(_M):
    """GET /_chaos"""
    pid: str
    blocked_out: list[str] = Field(default_factory=list)
    blocked_in: list[str] = Field(default_factory=list)
    delay_ms: int = 0
    frozen_until: float = 0.0
    node_faults: dict[str, Any] = Field(default_factory=dict)   # e.g. {"disk_full": true}


# ─────────────────────────── 7.4 Supervisor ───────────────────────────

class Proc(_M):
    pid: str
    port: int
    state: Literal["running", "stopped"]
    os_pid: Optional[int] = None
    uptime_s: float = 0.0
    display_name: str = ""


class ProcList(_M):
    procs: list[Proc] = Field(default_factory=list)


class PowerCutRequest(_M):
    scope: Literal["all", "label"]
    label: Optional[str] = None             # "power=A"
    restore_after_s: Optional[float] = None


class PowerCutResult(_M):
    killed: list[str] = Field(default_factory=list)


class PowerRestoreRequest(_M):
    scope: Literal["all", "label"]
    label: Optional[str] = None


class PowerRestoreResult(_M):
    started: list[str] = Field(default_factory=list)


class LinkRequest(_M):
    a: str
    b: str
    cut: bool = True
    direction: Literal["both", "a_to_b"] = "both"


class NodeChaosRequest(_M):
    action: Literal["slow", "freeze", "corrupt", "disk_full", "clear"]
    params: dict[str, Any] = Field(default_factory=dict)


class Fault(_M):
    id: str
    kind: str
    subject: str
    since: float
    params: dict[str, Any] = Field(default_factory=dict)


class FaultList(_M):
    faults: list[Fault] = Field(default_factory=list)


class ScriptRequest(_M):
    name: Literal["standard", "heavy"] = "standard"
    seed: int = 42


class ScriptStarted(_M):
    script_id: str


class AddNodeRequest(_M):
    display_name: str
    labels: dict[str, str] = Field(default_factory=dict)


class ResetRequest(_M):
    seed: bool = True
    mode: Mode = "vault"


class ResetResult(_M):
    ok: bool
    elapsed_s: float


class SeedRequest(_M):
    bucket: str = "clinic"
    count: int = 200


class SeedResult(_M):
    uploaded: int


# ─────────────────────────── 7.5 Oracle ───────────────────────────

RunState = Literal["preparing", "running", "settling", "verifying", "done", "failed"]
ViolationKind = Literal["lost", "damaged", "resurrected", "stale_read", "phantom_read"]


class RunRequest(_M):
    mode: Mode = "vault"
    seed: int = 42
    duration_s: int = 60
    clients: int = 8
    keyspace: int = 200
    chaos_script: Literal["standard", "heavy"] = "standard"


class RunCreated(_M):
    run_id: str


class RunCounters(_M):
    ops: int = 0
    acked_puts: int = 0
    acked_deletes: int = 0
    reads: int = 0
    unknown: int = 0
    failed: int = 0
    faults_injected: int = 0


class RunViolations(_M):
    lost: int = 0
    damaged: int = 0
    resurrected: int = 0
    stale_read: int = 0
    phantom_read: int = 0


class RunHealth(_M):
    under_protected: int = 0


class ViolationSample(_M):
    kind: ViolationKind
    key: str
    human: str
    technical: str


class TimelineMark(_M):
    t: float
    kind: Literal["chaos", "violation"]
    label: str


class RunStatus(_M):
    run_id: str
    mode: Mode
    seed: int
    state: RunState
    started_at: float
    elapsed_s: float = 0.0
    counters: RunCounters = Field(default_factory=RunCounters)
    violations: RunViolations = Field(default_factory=RunViolations)
    health: RunHealth = Field(default_factory=RunHealth)
    samples: list[ViolationSample] = Field(default_factory=list)
    timeline: list[TimelineMark] = Field(default_factory=list)


class RunSummary(_M):
    """GET /runs rows: RunStatus without samples/timeline."""
    run_id: str
    mode: Mode
    seed: int
    state: RunState
    started_at: float
    elapsed_s: float = 0.0
    chaos_script: str = "standard"
    counters: RunCounters = Field(default_factory=RunCounters)
    violations: RunViolations = Field(default_factory=RunViolations)
    health: RunHealth = Field(default_factory=RunHealth)


class RunList(_M):
    runs: list[RunSummary] = Field(default_factory=list)


class LedgerEntry(_M):
    """One line of oracle_runs/<run_id>/ledger.jsonl."""
    op: Literal["put", "get", "delete"]
    key: str
    invoke: float
    complete: float
    outcome: Literal["ok", "fail", "unknown"]
    sha256: Optional[str] = None
    commit_seq: Optional[int] = None
    seq: Optional[int] = None
    http: Optional[int] = None


# ─────────────────────────── 8.1 Events ───────────────────────────

class Event(_M):
    id: int
    ts: float
    type: str
    severity: Severity
    subject: dict[str, Any] = Field(default_factory=dict)
    human: str
    technical: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class EventList(_M):
    events: list[Event] = Field(default_factory=list)


# ─────────────────────────── 8.2 Snapshot ───────────────────────────

class Summary(_M):
    level: SummaryLevel
    human: str
    technical: str = ""
    files: int = 0
    logical_bytes: int = 0
    raw_bytes: int = 0
    overhead: float = 0.0
    under_replicated_chunks: int = 0
    at_risk_files: int = 0
    unreadable_files: int = 0
    min_ifl: int = 0


class NodeView(_M):
    id: str
    display_name: str
    addr: str
    state: NodeState
    phi: float = 0.0
    route: str = "direct"           # "direct" | "relay:n2"
    slow: bool = False
    fenced: bool = False
    epoch: int = 1
    labels: dict[str, str] = Field(default_factory=dict)
    disk_used: int = 0
    capacity: int = 0
    fragments: int = 0
    faults: list[str] = Field(default_factory=list)


class ControlView(_M):
    id: str                         # "meta" | "gw"
    ok: bool


class LinkView(_M):
    a: str
    b: str
    a_to_b: bool
    b_to_a: bool
    relay: Optional[str] = None


class ActiveJob(_M):
    job_id: int
    kind: JobKind
    reason: JobReason
    src: Optional[str] = None
    dst: Optional[str] = None
    bytes: int = 0
    progress: float = 0.0           # 0..1


class RepairView(_M):
    queued: dict[str, int] = Field(default_factory=lambda: {"p0": 0, "p1": 0, "p2": 0, "p3": 0, "p4": 0})
    active: list[ActiveJob] = Field(default_factory=list)
    mbps: float = 0.0


class TrafficView(_M):
    puts_per_s: float = 0.0
    gets_per_s: float = 0.0


class Snapshot(_M):
    ts: float
    mode: Mode
    config_version: int
    summary: Summary
    nodes: list[NodeView] = Field(default_factory=list)
    control: list[ControlView] = Field(default_factory=list)
    links: list[LinkView] = Field(default_factory=list)
    repair: RepairView = Field(default_factory=RepairView)
    incident: Optional[Incident] = None
    traffic: TrafficView = Field(default_factory=TrafficView)


# ─────────────────────────── 8.3 Metrics, fate ───────────────────────────

class LastIncident(_M):
    id: int
    detect_s: Optional[float] = None
    grace_s: Optional[float] = None
    repair_s: Optional[float] = None
    mttr_s: Optional[float] = None
    bytes: int = 0


class OverheadMetric(_M):
    cluster: float = 0.0
    by_policy: dict[str, float] = Field(default_factory=dict)


class RepairMetric(_M):
    bytes_total: int = 0
    mbps_now: float = 0.0


class RebalanceLast(_M):
    moved_fraction: float
    ideal_fraction: float


class RebalanceMetric(_M):
    last: Optional[RebalanceLast] = None


class IflMetric(_M):
    histogram: dict[str, int] = Field(default_factory=dict)   # {"1": 0, "2": 0, "3": 204}
    min: int = 0
    at_risk_files: int = 0


class ScrubMetric(_M):
    checked_last_pass: int = 0
    corrupt_found_total: int = 0


class Metrics(_M):
    availability_60s: float = 1.0
    readable_now_pct: float = 100.0
    overhead: OverheadMetric = Field(default_factory=OverheadMetric)
    last_incident: Optional[LastIncident] = None
    incidents_avg_mttr_s: Optional[float] = None
    unnecessary_repairs_avoided: int = 0
    repair: RepairMetric = Field(default_factory=RepairMetric)
    rebalance: RebalanceMetric = Field(default_factory=RebalanceMetric)
    ifl: IflMetric = Field(default_factory=IflMetric)
    scrub: ScrubMetric = Field(default_factory=ScrubMetric)


class FateDomain(_M):
    key: str
    value: str
    nodes: list[str]
    cluster_wide: bool = False


class ClusterWideRisk(_M):
    key: str
    value: str
    human: str


class AtRiskFile(_M):
    bucket: str
    key: str
    ifl: int
    target: int
    shared: list[DomainRef] = Field(default_factory=list)


class FateFiles(_M):
    histogram: dict[str, int] = Field(default_factory=dict)
    at_risk: list[AtRiskFile] = Field(default_factory=list)


class Advice(_M):
    human: str
    technical: str = ""


class FateReport(_M):
    fate_keys: list[str] = Field(default_factory=list)
    domains: list[FateDomain] = Field(default_factory=list)
    cluster_wide: list[ClusterWideRisk] = Field(default_factory=list)
    files: FateFiles = Field(default_factory=FateFiles)
    advice: list[Advice] = Field(default_factory=list)
    last_audit_at: Optional[float] = None
